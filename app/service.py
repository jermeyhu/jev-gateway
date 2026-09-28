"""Application service: wires config, backend, engine and coordinator together."""

from __future__ import annotations

from app.backends import build_backend
from app.backends.base import DecisionBackend
from app.config import MAX_CANDIDATES, Settings
from app.decision.coordinator import CoordinatedResult, Coordinator
from app.decision.engine import DecisionEngine
from app.decision.ir import DecisionTask
from app.primitives.base import build_candidates, to_decision_task
from app.schemas.request import Question, SystemOneRequest
from app.schemas.response import (
    Diagnostics,
    QuestionDiagnostic,
    SystemOneResponse,
    Usage,
)
from app.utils.errors import InvalidRequest
from app.utils.imaging import ImageRef, parse_images


class DecisionService:
    def __init__(self, settings: Settings, backend: DecisionBackend | None = None) -> None:
        self.settings = settings
        self.backend = backend or build_backend(settings.backend)
        self.engine = DecisionEngine(self.backend, prompt_layout=settings.request.prompt_layout)
        self.coordinator = Coordinator(
            self.engine,
            max_concurrency=settings.backend.max_concurrency,
            total_timeout_seconds=settings.request.total_timeout_seconds,
        )

    @property
    def model_name(self) -> str:
        return self.backend.model or self.settings.backend.model or "unknown"

    async def aclose(self) -> None:
        await self.backend.aclose()

    async def check_ready(self) -> None:
        await self.backend.check_ready()

    def build_tasks(self, payload: SystemOneRequest) -> tuple[list[DecisionTask], list[ImageRef]]:
        images = parse_images(payload.images, self.settings.multimodal)
        self.backend.ensure_images_supported(images)
        questions = payload.questions.root
        if len(questions) > self.settings.request.max_questions:
            raise InvalidRequest(
                f"too many questions: {len(questions)} "
                f"(limit {self.settings.request.max_questions})"
            )
        tasks = [
            self._to_task(question_id, payload.state, question, images)
            for question_id, question in questions.items()
        ]
        return tasks, images

    @staticmethod
    def _to_task(
        question_id: str,
        state: object,
        question: Question,
        images: list[ImageRef],
    ) -> DecisionTask:
        """Build a task, refusing more candidates than the hard ceiling.

        The request schema already bounds choice/score criteria at 16, but the
        gateway enforces it here too so the invariant holds no matter how a
        question reached the service layer.
        """
        candidate_count = len(build_candidates(question))
        if candidate_count > MAX_CANDIDATES:
            raise InvalidRequest(
                f"question {question_id!r} has {candidate_count} candidates "
                f"(limit {MAX_CANDIDATES})"
            )
        return to_decision_task(question_id, state, question, images)

    async def systemone(self, payload: SystemOneRequest) -> SystemOneResponse:
        tasks, _ = self.build_tasks(payload)
        result: CoordinatedResult = await self.coordinator.run(tasks)
        return to_response(
            result, model=self.model_name, backend=self.backend.type
        )


def to_response(result: CoordinatedResult, *, model: str, backend: str) -> SystemOneResponse:
    return SystemOneResponse(
        model=model,
        answers=result.answers,
        usage=Usage(input_tokens=result.input_tokens, output_tokens=result.output_tokens),
        diagnostics=Diagnostics(
            backend=backend,
            model=model,
            total_latency_ms=round(result.total_latency_ms, 3),
            questions={
                qid: QuestionDiagnostic(
                    latency_ms=round(item.latency_ms, 3),
                    truncated=item.truncated,
                    backend=backend,
                )
                for qid, item in result.per_question.items()
            },
        ),
    )
