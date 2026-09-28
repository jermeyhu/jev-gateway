"""Request schemas for the System One endpoint.

The shape is the Jev / System One contract. The only intentional extension over
the reference implementation is ``images``: a top-level list of image references
that is forwarded to multimodal backends.
"""

from __future__ import annotations

import math
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator, model_validator

from app.config import MAX_CANDIDATES, MAX_QUESTIONS

MIN_CANDIDATES = 2


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoulCriteria(StrictModel):
    true: str
    false: str

    @field_validator("true", "false")
    @classmethod
    def non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("criteria descriptions must not be empty")
        return value


class NoulQuestion(StrictModel):
    type: Literal["noul"]
    instructions: str = Field(min_length=1)
    criteria: NoulCriteria | None = None


class ChoiceQuestion(StrictModel):
    type: Literal["choice"]
    instructions: str = Field(min_length=1)
    criteria: dict[str, str]

    @field_validator("criteria")
    @classmethod
    def valid_criteria(cls, value: dict[str, str]) -> dict[str, str]:
        if not MIN_CANDIDATES <= len(value) <= MAX_CANDIDATES:
            raise ValueError(
                f"choice criteria must contain {MIN_CANDIDATES} to {MAX_CANDIDATES} candidates"
            )
        if any(not key or not description.strip() for key, description in value.items()):
            raise ValueError("choice keys and descriptions must not be empty")
        return value


class ScoreQuestion(StrictModel):
    type: Literal["score"]
    instructions: str = Field(min_length=1)
    criteria: list[str]

    @field_validator("criteria")
    @classmethod
    def valid_criteria(cls, value: list[str]) -> list[str]:
        if not MIN_CANDIDATES <= len(value) <= MAX_CANDIDATES:
            raise ValueError(
                f"score criteria must contain {MIN_CANDIDATES} to {MAX_CANDIDATES} levels"
            )
        if any(not item.strip() for item in value):
            raise ValueError("score descriptions must not be empty")
        return value


Question = Annotated[NoulQuestion | ChoiceQuestion | ScoreQuestion, Field(discriminator="type")]


def _validate_json_value(value: Any) -> None:
    if value is None or isinstance(value, (bool, str)):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("state must not contain NaN or Infinity")
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item)
        return
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("state object keys must be strings")
        for item in value.values():
            _validate_json_value(item)
        return
    raise ValueError("state must be JSON-compatible")


class Questions(RootModel[dict[str, Question]]):
    @model_validator(mode="after")
    def validate_questions(self) -> Questions:
        if not 1 <= len(self.root) <= MAX_QUESTIONS:
            raise ValueError(f"questions must contain 1 to {MAX_QUESTIONS} items")
        if any(not key for key in self.root):
            raise ValueError("question IDs must not be empty")
        return self


class SystemOneRequest(StrictModel):
    state: str | dict[str, Any] | list[Any]
    model: str | None = None
    images: list[str] | None = None
    questions: Questions

    @field_validator("state")
    @classmethod
    def valid_state(cls, value: Any) -> Any:
        if value == "" or value == {} or value == []:
            raise ValueError("state must not be empty")
        _validate_json_value(value)
        return value

    @field_validator("images")
    @classmethod
    def valid_images(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        if not value:
            raise ValueError("images must not be empty when provided")
        if any(not isinstance(item, str) or not item.strip() for item in value):
            raise ValueError("images must be non-empty strings")
        return value
