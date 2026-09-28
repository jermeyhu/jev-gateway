from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.utils.imaging import ImageRef


@dataclass(frozen=True, slots=True)
class Candidate:
    key: str
    description: str
    letter: str


@dataclass(frozen=True, slots=True)
class DecisionTask:
    question_id: str
    primitive: str
    state: Any
    instruction: str
    candidates: list[Candidate]
    images: list[ImageRef] = field(default_factory=list)
