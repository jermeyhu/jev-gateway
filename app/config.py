"""Runtime configuration, loaded from a single YAML file.

All runtime configuration lives in config.yaml; the only environment variable
the gateway reads is JEV_GATEWAY_CONFIG, which points at the file itself.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

BackendType = Literal["openai"]
DEFAULT_CONFIG_PATH = "config.yaml"
CONFIG_ENV_VAR = "JEV_GATEWAY_CONFIG"

#: The gateway speaks exactly one wire protocol: the OpenAI chat-completions
#: API.  The key stays in ``backend.type`` so the config file is explicit and a
#: future protocol can be added without renaming the field.
BACKEND_TYPES: tuple[str, ...] = ("openai",)


class ConfigError(RuntimeError):
    """Raised when config.yaml is missing or cannot be understood."""


@dataclass(frozen=True, slots=True)
class ServerSettings:
    host: str = "0.0.0.0"
    port: int = 8000


@dataclass(frozen=True, slots=True)
class BackendSettings:
    type: BackendType = "openai"
    base_url: str = "http://127.0.0.1:8080"
    model: str | None = None
    api_key: str | None = None
    timeout_seconds: float = 30.0
    max_concurrency: int = 32
    # How many next-token logprobs to read back from the server.  A candidate
    # letter outside the returned window cannot be recovered, so it is floored
    # at the least likely returned token and the question is flagged
    # ``truncated``.  64 is plenty for the 16-candidate limit.
    top_logprobs: int = 128
    # None means "auto": assume the backend can take images.
    supports_images: bool | None = None
    extra_headers: dict[str, str] = field(default_factory=dict)
    #: Extra top-level fields merged into every /v1/chat/completions payload.
    #: Empty by default, so the wire stays free of engine-specific options; set
    #: it to opt into a vendor knob, most commonly disabling a reasoning
    #: model's thinking so the first token is the candidate letter.  Because
    #: providers disagree on the spelling, the value is passed through
    #: verbatim -- see config.yaml for per-backend recipes.
    extra_body: dict[str, Any] = field(default_factory=dict)

    @property
    def images_enabled(self) -> bool:
        return self.supports_images is not False


@dataclass(frozen=True, slots=True)
class RequestSettings:
    max_questions: int = 64
    total_timeout_seconds: float = 60.0
    # fused: one user message holding evidence + criterion + options (byte
    # compatible with the reference gateway).  split: evidence in its own user
    # message so that a long shared state prefix can be reused by the backend
    # prompt cache.
    prompt_layout: Literal["fused", "split"] = "fused"


#: Hard ceiling on how many candidates a single question may carry.  The
#: request schema enforces it for every accepted request; the gateway re-checks
#: it at runtime so a question built outside the schema can never exceed it.
MAX_CANDIDATES = 16

#: Hard ceiling on how many questions one request may carry.  Shared with the
#: request schema so the two can never drift apart.
MAX_QUESTIONS = 64


@dataclass(frozen=True, slots=True)
class MultimodalSettings:
    enabled: bool = True
    max_images: int = 4
    max_image_bytes: int = 5 * 1024 * 1024
    allow_remote_urls: bool = False
    allowed_mime_prefixes: tuple[str, ...] = ("image/",)


@dataclass(frozen=True, slots=True)
class Settings:
    server: ServerSettings = field(default_factory=ServerSettings)
    backend: BackendSettings = field(default_factory=BackendSettings)
    request: RequestSettings = field(default_factory=RequestSettings)
    multimodal: MultimodalSettings = field(default_factory=MultimodalSettings)
    log_level: str = "INFO"
    source_path: Path | None = None


def _section(raw: dict[str, Any], name: str) -> dict[str, Any]:
    value = raw.get(name)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"config section '{name}' must be a mapping")
    return value


def _known(section: dict[str, Any], allowed: tuple[str, ...], name: str) -> None:
    unknown = sorted(set(section) - set(allowed))
    if unknown:
        raise ConfigError(f"unknown keys in config section '{name}': {', '.join(unknown)}")


def _tri_state(value: Any) -> bool | None:
    if value is None or value == "auto":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in {"true", "false", "auto"}:
        return {"true": True, "false": False, "auto": None}[value.lower()]
    raise ConfigError("backend.supports_images must be a boolean or 'auto'")


def _build(raw: dict[str, Any], path: Path | None) -> Settings:
    server_raw = _section(raw, "server")
    _known(server_raw, ("host", "port"), "server")
    backend_raw = _section(raw, "backend")
    _known(
        backend_raw,
        (
            "type",
            "base_url",
            "model",
            "api_key",
            "timeout_seconds",
            "max_concurrency",
            "top_logprobs",
            "supports_images",
            "extra_headers",
            "extra_body",
        ),
        "backend",
    )
    request_raw = _section(raw, "request")
    _known(request_raw, ("max_questions", "total_timeout_seconds", "prompt_layout"), "request")
    multimodal_raw = _section(raw, "multimodal")
    _known(
        multimodal_raw,
        (
            "enabled",
            "max_images",
            "max_image_bytes",
            "allow_remote_urls",
            "allowed_mime_prefixes",
        ),
        "multimodal",
    )
    logging_raw = _section(raw, "logging")
    _known(logging_raw, ("level",), "logging")

    backend_type = backend_raw.get("type", "openai")
    if backend_type not in BACKEND_TYPES:
        raise ConfigError(
            f"backend.type must be one of {', '.join(BACKEND_TYPES)} (got {backend_type!r})"
        )

    prompt_layout = request_raw.get("prompt_layout", "fused")
    if prompt_layout not in {"fused", "split"}:
        raise ConfigError("request.prompt_layout must be 'fused' or 'split'")

    base_url = backend_raw.get("base_url", "http://127.0.0.1:8080")
    if not isinstance(base_url, str) or not base_url.startswith(("http://", "https://")):
        raise ConfigError("backend.base_url must be an http(s) URL")

    max_questions = int(request_raw.get("max_questions", MAX_QUESTIONS))
    if not 1 <= max_questions <= MAX_QUESTIONS:
        raise ConfigError(f"request.max_questions must be between 1 and {MAX_QUESTIONS}")

    max_images = int(multimodal_raw.get("max_images", 4))
    if max_images < 1:
        raise ConfigError("multimodal.max_images must be at least 1")

    max_concurrency = int(backend_raw.get("max_concurrency", 32))
    if max_concurrency < 1:
        raise ConfigError("backend.max_concurrency must be at least 1")

    top_logprobs = int(backend_raw.get("top_logprobs", 128))
    if not 16 <= top_logprobs <= 4096:
        raise ConfigError("backend.top_logprobs must be between 16 and 4096")

    mime_prefixes = multimodal_raw.get("allowed_mime_prefixes", ["image/"])
    if not isinstance(mime_prefixes, list) or not all(isinstance(x, str) for x in mime_prefixes):
        raise ConfigError("multimodal.allowed_mime_prefixes must be a list of strings")

    extra_body = backend_raw.get("extra_body") or {}
    if not isinstance(extra_body, dict):
        raise ConfigError("backend.extra_body must be a mapping")

    return Settings(
        server=ServerSettings(
            host=str(server_raw.get("host", "0.0.0.0")),
            port=int(server_raw.get("port", 8000)),
        ),
        backend=BackendSettings(
            type=backend_type,
            base_url=base_url.rstrip("/"),
            model=backend_raw.get("model"),
            api_key=backend_raw.get("api_key"),
            timeout_seconds=float(backend_raw.get("timeout_seconds", 30.0)),
            max_concurrency=max_concurrency,
            top_logprobs=top_logprobs,
            supports_images=_tri_state(backend_raw.get("supports_images", "auto")),
            extra_headers={
                str(k): str(v) for k, v in (backend_raw.get("extra_headers") or {}).items()
            },
            extra_body=dict(extra_body),
        ),
        request=RequestSettings(
            max_questions=max_questions,
            total_timeout_seconds=float(request_raw.get("total_timeout_seconds", 60.0)),
            prompt_layout=prompt_layout,
        ),
        multimodal=MultimodalSettings(
            enabled=bool(multimodal_raw.get("enabled", True)),
            max_images=max_images,
            max_image_bytes=int(multimodal_raw.get("max_image_bytes", 5 * 1024 * 1024)),
            allow_remote_urls=bool(multimodal_raw.get("allow_remote_urls", False)),
            allowed_mime_prefixes=tuple(mime_prefixes),
        ),
        log_level=str(logging_raw.get("level", "INFO")).upper(),
        source_path=path,
    )


def build_default_settings() -> Settings:
    """Settings with every default applied (no config file read)."""
    return _build({}, None)


def load_settings(path: str | os.PathLike[str] | None = None) -> Settings:
    """Load settings from YAML.  A missing file is only tolerated for the default."""
    target = Path(path or os.environ.get(CONFIG_ENV_VAR) or DEFAULT_CONFIG_PATH)
    if not target.exists():
        if path is None and not os.environ.get(CONFIG_ENV_VAR):
            return _build({}, None)
        raise ConfigError(f"config file not found: {target}")
    with target.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    if not isinstance(raw, dict):
        raise ConfigError("config file must contain a mapping at the top level")
    _known(raw, ("server", "backend", "request", "multimodal", "logging"), "<root>")
    return _build(raw, target)
