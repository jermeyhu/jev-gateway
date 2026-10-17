"""Application service: wires config, backend, engine and coordinator together."""

from __future__ import annotations

from app.backends import build_backend
from app.backends.base import DecisionBackend
from app.config import MAX_CANDIDATES, Settings
from app.decision.coordinator import CoordinatedResult, Coordinator
from app.decision.engine import DecisionEngine
from app.decision.ir import DecisionTask
from app.primitives.base import build_candidates, to_decision_task
from app.schemas.request import BoxQuestion, Question, SystemOneRequest
from app.schemas.response import (
    SystemOneResponse,
    Usage,
)
from app.utils.errors import InvalidRequest
from app.utils.imaging import ImageRef, parse_assets, referenced_assets


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
        assets = parse_assets(payload.assets, self.settings.multimodal)
        questions = payload.questions.root
        if len(questions) > self.settings.request.max_questions:
            raise InvalidRequest(
                f"too many questions: {len(questions)} "
                f"(limit {self.settings.request.max_questions})"
            )
        request_references = referenced_assets(
            {"state": payload.state, "questions": [q.model_dump() for q in questions.values()]},
            assets,
        )
        used = {image.reference for image in request_references}
        unused = set(assets) - used
        if unused:
            raise InvalidRequest(f"unreferenced image assets: {', '.join(sorted(unused))}")
        self.backend.ensure_images_supported(request_references)

        tasks: list[DecisionTask] = []
        for question_id, question in questions.items():
            question_images = [
                *referenced_assets(payload.state, assets),
                *referenced_assets(question.model_dump(), assets),
            ]
            if isinstance(question, BoxQuestion) and not question_images:
                raise InvalidRequest("box questions require a referenced image asset")
            tasks.append(
                self._to_task(
                    question_id,
                    payload.state,
                    question,
                    question_images,
                    payload.model,
                )
            )
        return tasks, request_references

    @staticmethod
    def _to_task(
        question_id: str,
        state: object,
        question: Question,
        images: list[ImageRef],
        model: str,
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
        return to_decision_task(question_id, state, question, images, model)

    async def systemone(self, payload: SystemOneRequest) -> SystemOneResponse:
        tasks, _ = self.build_tasks(payload)
        result: CoordinatedResult = await self.coordinator.run(tasks)
        return to_response(
            result, model=result.model or payload.model
        )


def to_response(result: CoordinatedResult, *, model: str) -> SystemOneResponse:
    return SystemOneResponse(
        model=model,
        answers=result.answers,
        usage=Usage(input_tokens=result.input_tokens, output_tokens=result.output_tokens),
    )
