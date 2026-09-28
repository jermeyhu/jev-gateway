"""Backend implementations and the factory that picks one from config."""

from __future__ import annotations

from app.backends.base import CandidateScores, DecisionBackend
from app.backends.openai import OpenAICompatibleBackend
from app.config import BackendSettings

BACKEND_CLASSES: dict[str, type[DecisionBackend]] = {
    OpenAICompatibleBackend.type: OpenAICompatibleBackend,
}

__all__ = [
    "BACKEND_CLASSES",
    "CandidateScores",
    "DecisionBackend",
    "OpenAICompatibleBackend",
    "build_backend",
]


def build_backend(settings: BackendSettings) -> DecisionBackend:
    """Instantiate the backend described by ``settings.type``.

    Only one backend exists -- the generic OpenAI-compatible client -- so this
    is little more than a name lookup today.  It stays because the type key is
    still validated against config, and a second wire protocol would slot in
    here without touching the service layer.
    """
    try:
        backend_class = BACKEND_CLASSES[settings.type]
    except KeyError as exc:  # pragma: no cover - config validation catches this
        raise ValueError(f"unknown backend type: {settings.type!r}") from exc
    return backend_class(settings)
