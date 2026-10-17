from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class ResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Usage(ResponseModel):
    input_tokens: int
    output_tokens: int


class SystemOneResponse(ResponseModel):
    model: str
    answers: dict[str, dict[str, Any]]
    usage: Usage
