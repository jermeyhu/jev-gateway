"""Turn candidate logprobs into a Jev answer."""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from app.decision.ir import DecisionTask
from app.decision.prompt import build_messages
from app.utils.softmax import stable_softmax

if TYPE_CHECKING:  # avoids a cycle: backends.base imports this module's types
    from app.backends.base import CandidateScores, DecisionBackend


@dataclass(frozen=True, slots=True)
class DecisionResult:
    question_id: str
    answer: dict[str, Any]
    truncated: bool
    input_tokens: int
    output_tokens: int
    latency_ms: float


class DecisionEngine:
    """Scores one question against one backend."""

    def __init__(
        self,
        backend: DecisionBackend,
        *,
        prompt_layout: Literal["fused", "split"] = "fused",
    ) -> None:
        self.backend = backend
        self.prompt_layout = prompt_layout

    async def decide(self, task: DecisionTask) -> DecisionResult:
        started = time.perf_counter()
        messages = build_messages(task, self.prompt_layout)
        letters = [candidate.letter for candidate in task.candidates]
        scores: CandidateScores = await self.backend.score_candidates(
            messages, letters, task.images
        )
        answer = build_answer(task, scores)
        return DecisionResult(
            question_id=task.question_id,
            answer=answer,
            truncated=scores.truncated,
            input_tokens=scores.input_tokens,
            output_tokens=scores.output_tokens,
            latency_ms=(time.perf_counter() - started) * 1000.0,
        )


def normalise(candidates: Sequence[Any], scores: CandidateScores) -> dict[str, float]:
    """Softmax restricted to the candidates, in candidate order."""
    letters = [candidate.letter for candidate in candidates]
    values = [scores.logprobs[letter] for letter in letters]
    return dict(zip(letters, stable_softmax(values), strict=True))


def build_answer(task: DecisionTask, scores: CandidateScores) -> dict[str, Any]:
    candidates = task.candidates
    probabilities = normalise(candidates, scores)

    if task.primitive == "noul":
        true_letter = candidates[0].letter
        return {"type": "noul", "noul": probabilities[true_letter]}

    if task.primitive == "choice":
        best = max(candidates, key=lambda candidate: probabilities[candidate.letter])
        return {
            "type": "choice",
            "choice": best.key,
            "probabilities": {
                candidate.key: probabilities[candidate.letter] for candidate in candidates
            },
        }

    if task.primitive == "score":
        legend = {candidate.key: candidate.description for candidate in candidates}
        expected = sum(
            index * probabilities[candidate.letter] for index, candidate in enumerate(candidates)
        )
        return {
            "type": "score",
            "score": expected,
            "legend": legend,
            "probabilities": {
                candidate.key: probabilities[candidate.letter] for candidate in candidates
            },
        }

    raise ValueError(f"unsupported primitive: {task.primitive!r}")
