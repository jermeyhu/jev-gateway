"""Request schemas for the TypeSafe System One endpoint."""

from __future__ import annotations

import math
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    field_validator,
    model_validator,
)

from app.config import MAX_CANDIDATES, MAX_QUESTIONS

MIN_CANDIDATES = 2
MAX_SCORE_LEVELS = 10
JsonContent = str | dict[str, Any] | list[Any]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ImageAsset(StrictModel):
    type: Literal["image"] | None = None
    part: str | None = None
    format: Literal["png", "jpg", "jpeg"] | None = None
    data_base64: str | None = None

    @model_validator(mode="after")
    def valid_source(self) -> ImageAsset:
        if "type" in self.model_fields_set and self.type != "image":
            raise ValueError("asset type must be 'image'")
        if self.part is not None and not self.part.strip():
            raise ValueError("asset part must not be empty")
        if self.data_base64 is not None and self.format is None and self.part is None:
            raise ValueError("asset format is required with data_base64")
        if self.data_base64 is None and self.part is None:
            raise ValueError("asset requires data_base64 or part")
        return self


class NoulCriteria(StrictModel):
    true: JsonContent | None = None
    false: JsonContent | None = None

    @field_validator("true", "false")
    @classmethod
    def valid_content(cls, value: JsonContent | None) -> JsonContent | None:
        if value is not None:
            _validate_json_value(value)
        return value

    @model_validator(mode="after")
    def valid_nesting(self) -> NoulCriteria:
        if any(
            key in self.model_fields_set and getattr(self, key) is None
            for key in ("true", "false")
        ):
            raise ValueError("noul criterion values must not be null")
        _validate_json_value(self.model_dump(exclude_none=True))
        return self


class NoulQuestion(StrictModel):
    type: Literal["noul"]
    instructions: JsonContent
    criteria: NoulCriteria | None = None

    @field_validator("instructions")
    @classmethod
    def valid_instructions(cls, value: JsonContent) -> JsonContent:
        _validate_json_value(value)
        return value


class ChoiceQuestion(StrictModel):
    type: Literal["choice"]
    instructions: JsonContent
    criteria: dict[str, JsonContent | None]

    @field_validator("instructions")
    @classmethod
    def valid_instructions(cls, value: JsonContent) -> JsonContent:
        _validate_json_value(value)
        return value

    @field_validator("criteria")
    @classmethod
    def valid_criteria(
        cls, value: dict[str, JsonContent | None]
    ) -> dict[str, JsonContent | None]:
        if not MIN_CANDIDATES <= len(value) <= MAX_CANDIDATES:
            raise ValueError(
                f"choice criteria must contain {MIN_CANDIDATES} to {MAX_CANDIDATES} candidates"
            )
        if any(not key for key in value):
            raise ValueError("choice keys must not be empty")
        _validate_json_value(value)
        for description in value.values():
            if description is not None:
                _validate_json_value(description)
        return value


class ScoreQuestion(StrictModel):
    type: Literal["score"]
    instructions: JsonContent
    criteria: list[JsonContent]

    @field_validator("instructions")
    @classmethod
    def valid_instructions(cls, value: JsonContent) -> JsonContent:
        _validate_json_value(value)
        return value

    @field_validator("criteria")
    @classmethod
    def valid_criteria(cls, value: list[JsonContent]) -> list[JsonContent]:
        if not MIN_CANDIDATES <= len(value) <= MAX_SCORE_LEVELS:
            raise ValueError(
                f"score criteria must contain {MIN_CANDIDATES} to {MAX_SCORE_LEVELS} levels"
            )
        _validate_json_value(value)
        return value


class BoxQuestion(StrictModel):
    type: Literal["box"]
    instructions: JsonContent

    @field_validator("instructions")
    @classmethod
    def valid_instructions(cls, value: JsonContent) -> JsonContent:
        _validate_json_value(value)
        return value


Question = Annotated[
    NoulQuestion | ChoiceQuestion | ScoreQuestion | BoxQuestion,
    Field(discriminator="type"),
]


def _validate_json_value(value: Any, depth: int = 0) -> None:
    if depth > 64:
        raise ValueError("JSON content must not exceed 64 nested levels")
    if value is None or isinstance(value, (bool, str)):
        return
    if isinstance(value, int):
        try:
            if not math.isfinite(float(value)):
                raise ValueError("JSON numbers must be finite")
        except OverflowError as exc:
            raise ValueError("JSON numbers must be finite") from exc
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("state must not contain NaN or Infinity")
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item, depth + 1)
        return
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("state object keys must be strings")
        for item in value.values():
            _validate_json_value(item, depth + 1)
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
    model: str = Field(min_length=1)
    assets: dict[str, ImageAsset] | None = None
    questions: Questions

    @field_validator("state")
    @classmethod
    def valid_state(cls, value: Any) -> Any:
        if value == "" or value == {} or value == []:
            raise ValueError("state must not be empty")
        _validate_json_value(value)
        return value

    @field_validator("model")
    @classmethod
    def valid_model(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("model must not be empty")
        return value

    @field_validator("assets")
    @classmethod
    def valid_assets(cls, value: dict[str, ImageAsset] | None) -> dict[str, ImageAsset] | None:
        if value is None:
            return None
        if not value:
            raise ValueError("assets must not be empty when provided")
        if any(not reference.strip() for reference in value):
            raise ValueError("asset references must not be empty")
        return value
