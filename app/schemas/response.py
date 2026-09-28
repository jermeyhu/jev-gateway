from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class ResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Usage(ResponseModel):
    input_tokens: int
    output_tokens: int


class QuestionDiagnostic(ResponseModel):
    latency_ms: float
    truncated: bool = False
    backend: str


class Diagnostics(ResponseModel):
    backend: str
    model: str
    total_latency_ms: float
    questions: dict[str, QuestionDiagnostic]


class SystemOneResponse(ResponseModel):
    model: str
    answers: dict[str, dict[str, Any]]
    usage: Usage
    diagnostics: Diagnostics | None = None
