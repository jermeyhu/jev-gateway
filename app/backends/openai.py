"""Generic OpenAI-compatible backend.

The gateway talks to *any* server that implements the OpenAI
``POST /v1/chat/completions`` contract, without knowing which engine is behind
it (llama.cpp, vLLM, SGLang, Ollama, LM Studio, TGI, a hosted API, ...):

1. send the chat prompt with ``max_tokens: 1`` and ``logprobs: true``
   (plus ``top_logprobs: N``),
2. read the next-token distribution back from
   ``choices[0].logprobs.content[0].top_logprobs``,
3. map every returned token onto a candidate letter (``"A"`` and ``" A"`` both
   count) and keep the most likely variant.

Nothing engine-specific is sent by default: no ``logprob_token_ids``, no native
``/generate``, no chat-template kwargs.  ``backend.extra_body`` can add
vendor-specific fields when a server needs them -- most often to turn a
reasoning model's thinking off so the first token is the candidate letter.  The
trade-off is that a candidate outside the returned top-N window cannot be
recovered, so it is floored at the least likely token that *was* returned and
the question is flagged ``truncated``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from app.backends.base import CandidateScores, DecisionBackend
from app.utils.errors import BackendProtocolError
from app.utils.imaging import ImageRef

if TYPE_CHECKING:  # avoids a cycle: decision.engine imports this module
    from app.decision.prompt import Message

#: Logprob used when a candidate letter never appeared in the top-N window and
#: no other returned token can bound it.
MISSING_FLOOR = -20.0


def normalise_letter(token: Any) -> str | None:
    """Map a returned token onto a candidate letter, if it is one.

    ``"A"``, ``" A"`` and ``"a"`` all map to ``"A"``; anything that is not a
    single ASCII letter (``"the"``, ``"。"``, ``"AB"``) maps to ``None``.
    """
    if not isinstance(token, str):
        return None
    stripped = token.strip()
    if len(stripped) != 1:
        return None
    letter = stripped.upper()
    return letter if letter.isascii() and letter.isalpha() else None


class OpenAICompatibleBackend(DecisionBackend):
    """Scores candidates through the standard chat-completions logprobs API."""

    type = "openai"

    async def score_candidates(
        self,
        messages: list[Message],
        letters: Sequence[str],
        images: Sequence[ImageRef] = (),
    ) -> CandidateScores:
        self.ensure_images_supported(images)
        # Vendor extras go in first so the contract fields below always win: a
        # stray extra_body key can never disable logprobs or change max_tokens.
        payload: dict[str, Any] = dict(self.settings.extra_body)
        payload.update(
            {
                "messages": list(messages),
                "max_tokens": 1,
                "temperature": 0.0,
                "stream": False,
                "logprobs": True,
                "top_logprobs": self.settings.top_logprobs,
            }
        )
        if self.settings.model:
            payload["model"] = self.settings.model

        path = "/v1/chat/completions"
        body = self._json(await self._post(path, payload), path)
        scores, floor = self._collect(body, set(letters))
        if not scores:
            raise BackendProtocolError(
                "backend returned no logprobs; make sure the server supports "
                "logprobs/top_logprobs on /v1/chat/completions"
            )
        # A candidate outside the top-N window is unknowable, so it is floored at
        # the least likely token that *was* returned and the question is flagged.
        missing = [letter for letter in letters if letter not in scores]
        for letter in missing:
            scores[letter] = floor
        usage = body.get("usage")
        return CandidateScores(
            logprobs=scores,
            truncated=bool(missing),
            input_tokens=self._token_count(usage, "prompt_tokens"),
            output_tokens=self._token_count(usage, "completion_tokens"),
        )

    @staticmethod
    def _collect(body: dict[str, Any], wanted: set[str]) -> tuple[dict[str, float], float]:
        """Return (logprob per letter, lowest logprob seen)."""
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise BackendProtocolError("backend response contained no choices")
        first = choices[0]
        if not isinstance(first, dict):
            raise BackendProtocolError("backend response choice was not an object")
        logprobs = first.get("logprobs")
        if not isinstance(logprobs, dict):
            raise BackendProtocolError("backend response contained no logprobs object")
        content = logprobs.get("content")
        if not isinstance(content, list) or not content:
            raise BackendProtocolError("backend response logprobs.content was empty")

        scores: dict[str, float] = {}
        floor = float("inf")
        for entry in content:
            if not isinstance(entry, dict):
                continue
            candidates = entry.get("top_logprobs")
            if not isinstance(candidates, list):
                continue
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    continue
                try:
                    value = float(candidate["logprob"])
                except (KeyError, TypeError, ValueError):
                    continue
                if value != value or value in (float("inf"), float("-inf")):
                    continue
                floor = min(floor, value)
                letter = normalise_letter(candidate.get("token"))
                if letter is None or letter not in wanted:
                    continue
                # Several tokens can map to the same letter (e.g. "A" and " A");
                # keep the most likely one.
                if letter not in scores or value > scores[letter]:
                    scores[letter] = value
        return scores, floor if floor != float("inf") else MISSING_FLOOR

    async def check_ready(self) -> None:
        await self._require_health()


__all__ = ["MISSING_FLOOR", "OpenAICompatibleBackend", "normalise_letter"]