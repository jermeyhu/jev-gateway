from __future__ import annotations

import asyncio

import pytest

from app.decision.coordinator import Coordinator
from app.decision.engine import DecisionEngine
from app.primitives.base import to_decision_task
from app.schemas.request import NoulQuestion
from app.utils.errors import BackendUnavailable, RequestTimeout

from .conftest import FakeBackend


def tasks(count: int) -> list:
    return [
        to_decision_task(
            f"q{index}", "state", NoulQuestion(type="noul", instructions=f"q{index}")
        )
        for index in range(count)
    ]


async def test_questions_run_concurrently() -> None:
    backend = FakeBackend(default={"A": -0.1, "B": -2.0})
    backend.latency = 0.1
    coordinator = Coordinator(DecisionEngine(backend), max_concurrency=8)
    result = await coordinator.run(tasks(8))
    assert len(result.answers) == 8
    # 8 x 100ms serially would be >= 800ms; concurrent it must be far less.
    assert result.total_latency_ms < 500
    assert result.input_tokens == 88
    assert result.output_tokens == 8


async def test_concurrency_is_capped() -> None:
    backend = FakeBackend(default={"A": -0.1, "B": -2.0})
    backend.latency = 0.05
    coordinator = Coordinator(DecisionEngine(backend), max_concurrency=2)
    result = await coordinator.run(tasks(6))
    # 6 tasks / 2 concurrent slots = 3 waves of 50ms.
    assert result.total_latency_ms >= 140
    assert len(result.answers) == 6


async def test_first_error_in_task_order_wins() -> None:
    backend = FakeBackend(default={"A": -0.1, "B": -2.0})
    original = backend.score_candidates

    async def failing(messages, letters, images=()):
        if "q1" in str(messages):
            raise BackendUnavailable("backend refused the batch")
        return await original(messages, letters, images)

    backend.score_candidates = failing  # type: ignore[method-assign]
    coordinator = Coordinator(DecisionEngine(backend))
    with pytest.raises(BackendUnavailable, match="refused"):
        await coordinator.run(tasks(3))


async def test_unexpected_exception_becomes_a_protocol_error() -> None:
    backend = FakeBackend(default={"A": -0.1, "B": -2.0})

    async def exploding(messages, letters, images=()):
        raise KeyError("surprise")

    backend.score_candidates = exploding  # type: ignore[method-assign]
    coordinator = Coordinator(DecisionEngine(backend))
    with pytest.raises(Exception, match="surprise"):
        await coordinator.run(tasks(1))


async def test_total_timeout_cancels_slow_questions() -> None:
    backend = FakeBackend(default={"A": -0.1, "B": -2.0})
    backend.latency = 1.0
    coordinator = Coordinator(DecisionEngine(backend), total_timeout_seconds=0.05)
    with pytest.raises(RequestTimeout):
        await asyncio.wait_for(coordinator.run(tasks(2)), timeout=2)
