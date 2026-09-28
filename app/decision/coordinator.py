"""Run every question of a request and aggregate the results.

Questions are independent, so they are scored concurrently behind a semaphore.
Like the reference implementation, the request as a whole fails when any single
question fails: returning a partial answer set would silently change the
meaning of a decision.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.decision.engine import DecisionEngine, DecisionResult
from app.decision.ir import DecisionTask
from app.utils.errors import BackendProtocolError, GatewayError, RequestTimeout


@dataclass(frozen=True, slots=True)
class CoordinatedResult:
    answers: dict[str, dict[str, Any]]
    input_tokens: int
    output_tokens: int
    per_question: dict[str, DecisionResult]
    total_latency_ms: float


class Coordinator:
    def __init__(
        self,
        engine: DecisionEngine,
        *,
        max_concurrency: int = 32,
        total_timeout_seconds: float = 60.0,
    ) -> None:
        self._engine = engine
        self._semaphore = asyncio.Semaphore(max(1, max_concurrency))
        self._total_timeout = total_timeout_seconds

    async def run(self, tasks: Sequence[DecisionTask]) -> CoordinatedResult:
        started = time.perf_counter()
        try:
            async with asyncio.timeout(self._total_timeout):
                outcomes = await asyncio.gather(
                    *(self._run_one(task) for task in tasks), return_exceptions=True
                )
        except TimeoutError as exc:
            raise RequestTimeout(
                f"request exceeded the {self._total_timeout:g}s total timeout"
            ) from exc

        for task, outcome in zip(tasks, outcomes, strict=True):
            if isinstance(outcome, BaseException):
                if isinstance(outcome, (asyncio.CancelledError, TimeoutError)):
                    raise RequestTimeout(
                        f"question {task.question_id!r} did not complete in time"
                    )
                if isinstance(outcome, GatewayError):
                    raise outcome
                raise BackendProtocolError(
                    f"question {task.question_id!r} failed: {outcome}"
                ) from outcome

        per_question = {
            result.question_id: result
            for result in outcomes
            if isinstance(result, DecisionResult)
        }
        return CoordinatedResult(
            answers={qid: result.answer for qid, result in per_question.items()},
            input_tokens=sum(result.input_tokens for result in per_question.values()),
            output_tokens=sum(result.output_tokens for result in per_question.values()),
            per_question=per_question,
            total_latency_ms=(time.perf_counter() - started) * 1000.0,
        )

    async def _run_one(self, task: DecisionTask) -> DecisionResult:
        async with self._semaphore:
            return await self._engine.decide(task)
