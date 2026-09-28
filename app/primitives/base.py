"""Mapping from wire questions to backend-independent decision tasks."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.decision.ir import Candidate, DecisionTask
from app.schemas.request import ChoiceQuestion, NoulQuestion, Question, ScoreQuestion
from app.utils.imaging import ImageRef

DEFAULT_NOUL_CRITERIA = ("Yes", "No")


def _letter(index: int) -> str:
    return chr(ord("A") + index)


def build_candidates(question: Question) -> list[Candidate]:
    if isinstance(question, NoulQuestion):
        descriptions = (
            (question.criteria.true, question.criteria.false)
            if question.criteria is not None
            else DEFAULT_NOUL_CRITERIA
        )
        return [
            Candidate(key="true", description=descriptions[0], letter=_letter(0)),
            Candidate(key="false", description=descriptions[1], letter=_letter(1)),
        ]
    if isinstance(question, ChoiceQuestion):
        return [
            Candidate(key=key, description=description, letter=_letter(index))
            for index, (key, description) in enumerate(question.criteria.items())
        ]
    if isinstance(question, ScoreQuestion):
        return [
            Candidate(key=str(index), description=description, letter=_letter(index))
            for index, description in enumerate(question.criteria)
        ]
    raise TypeError("unsupported primitive")


def to_decision_task(
    question_id: str,
    state: Any,
    question: Question,
    images: Sequence[ImageRef] = (),
) -> DecisionTask:
    return DecisionTask(
        question_id=question_id,
        primitive=question.type,
        state=state,
        instruction=question.instructions,
        candidates=build_candidates(question),
        images=list(images),
    )
