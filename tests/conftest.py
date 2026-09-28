from __future__ import annotations

import base64
import json
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.backends.base import CandidateScores, DecisionBackend  # noqa: E402
from app.config import BackendSettings, Settings, build_default_settings  # noqa: E402
from app.decision.prompt import Message  # noqa: E402
from app.utils.imaging import ImageRef  # noqa: E402

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 16).decode()
IMAGE = ImageRef("data_url", "image/png", PNG, f"data:image/png;base64,{PNG}")


def message_text(message: Message) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    return json.dumps(content, ensure_ascii=False)


class FakeBackend(DecisionBackend):
    """Backend stub returning canned logprobs, keyed by the instruction text."""

    type = "fake"

    def __init__(
        self,
        logprobs: dict[str, dict[str, float]] | None = None,
        *,
        default: dict[str, float] | None = None,
    ) -> None:
        self._by_instruction = logprobs or {}
        self._default = default
        # No HTTP client is created: the stub never talks to a real server, but
        # the base class still expects `settings` to exist for capability checks.
        self.settings = BackendSettings(
            type="fake", base_url="http://fake", model="fake-model"
        )
        self.calls: list[tuple[list[Message], tuple[str, ...], tuple[ImageRef, ...]]] = []
        self.closed = False
        self.ready = True
        self.error: Exception | None = None
        self.latency = 0.0

    @property
    def model(self) -> str:
        return "fake-model"

    async def score_candidates(
        self,
        messages: list[Message],
        letters: Sequence[str],
        images: Sequence[ImageRef] = (),
    ) -> CandidateScores:
        self.calls.append((messages, tuple(letters), tuple(images)))
        if self.latency:
            import asyncio

            await asyncio.sleep(self.latency)
        if self.error is not None:
            raise self.error

        text = "\n".join(message_text(message) for message in messages)
        scores = self._default
        for instruction, candidate_scores in self._by_instruction.items():
            if instruction in text:
                scores = candidate_scores
        scores = scores or {}

        trimmed = {letter: float(scores.get(letter, -20.0)) for letter in letters}
        return CandidateScores(
            logprobs=trimmed,
            truncated=any(letter not in scores for letter in letters),
            input_tokens=11,
            output_tokens=1,
        )

    async def check_ready(self) -> None:
        if not self.ready:
            from app.utils.errors import BackendNotReady

            raise BackendNotReady("fake backend is down")

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def settings() -> Settings:
    return build_default_settings()


@pytest.fixture
def backend() -> FakeBackend:
    return FakeBackend(default={"A": -0.1, "B": -2.3, "C": -3.0})
