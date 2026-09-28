"""Runtime configuration, read from environment variables.

Every setting has a default that works out of the box, so the gateway starts
with an empty environment.  Each field below names the variable that overrides
it; the prefix is ``JEV_`` throughout, e.g. ``JEV_BACKEND_BASE_URL``.

Values are parsed and validated here exactly as they were when the settings
lived in a YAML file, so a typo fails at startup with the variable named in the
message rather than silently defaulting.  There is no config file to mount, to
copy, or to keep in sync.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, cast

BackendType = Literal["openai"]

#: The gateway speaks exactly one wire protocol: the OpenAI chat-completions
#: API.  The name stays in ``JEV_BACKEND_TYPE`` so the protocol is explicit and
#: a future one can be added without renaming the field.
BACKEND_TYPES: tuple[str, ...] = ("openai",)

#: Prefix shared by every variable the gateway reads.
ENV_PREFIX = "JEV_"

#: Comma-separated MIME prefixes are accepted here; a JSON array works too.
MIME_SEPARATOR = ","

#: Spellings accepted for a boolean, case-insensitively.  Being liberal here
#: costs nothing and saves a startup failure over ``JEV_X=on`` vs ``JEV_X=ON``.
TRUE_WORDS = frozenset({"true", "1", "yes", "on"})
FALSE_WORDS = frozenset({"false", "0", "no", "off"})


class ConfigError(RuntimeError):
    """Raised when an environment variable is set to something unusable."""


def env_name(field_name: str) -> str:
    """``backend.base_url`` -> ``JEV_BACKEND_BASE_URL``."""
    return ENV_PREFIX + field_name.replace(".", "_").upper()


def _raw(environ: Mapping[str, str], name: str) -> str | None:
    """Return a variable's value, treating blank as unset.

    Compose passes unset keys through as empty strings, and an empty value can
    never be a valid setting, so this keeps "" and unset behaving identically.
    """
    value = environ.get(env_name(name))
    if value is None or not value.strip():
        return None
    return value


def _string(environ: Mapping[str, str], name: str, default: str) -> str:
    value = _raw(environ, name)
    return default if value is None else value.strip()


def _optional_string(environ: Mapping[str, str], name: str) -> str | None:
    return _raw(environ, name)


def _int(environ: Mapping[str, str], name: str, default: int) -> int:
    value = _raw(environ, name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        raise ConfigError(f"{env_name(name)} must be an integer (got {value!r})") from None


def _float(environ: Mapping[str, str], name: str, default: float) -> float:
    value = _raw(environ, name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        raise ConfigError(f"{env_name(name)} must be a number (got {value!r})") from None


def _bool(environ: Mapping[str, str], name: str, default: bool) -> bool:
    value = _raw(environ, name)
    if value is None:
        return default
    lowered = value.lower()
    if lowered in TRUE_WORDS:
        return True
    if lowered in FALSE_WORDS:
        return False
    raise ConfigError(
        f"{env_name(name)} must be true or false (got {value!r}; "
        f"use one of {', '.join(sorted(TRUE_WORDS | FALSE_WORDS))})"
    )


def _tri_state(environ: Mapping[str, str], name: str) -> bool | None:
    value = _raw(environ, name)
    if value is None:
        return None
    lowered = value.lower()
    if lowered == "auto":
        return None
    if lowered in TRUE_WORDS:
        return True
    if lowered in FALSE_WORDS:
        return False
    raise ConfigError(
        f"{env_name(name)} must be true, false or auto (got {value!r}; "
        f"use true, false, auto, 1, 0, yes, no, on, off)"
    )


def _json_object(environ: Mapping[str, str], name: str) -> dict[str, Any]:
    """Parse a JSON object, for the two free-form passthrough settings."""
    value = _raw(environ, name)
    if value is None:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{env_name(name)} must be a JSON object (got {exc.msg})") from None
    if not isinstance(parsed, dict):
        raise ConfigError(f"{env_name(name)} must be a JSON object")
    return parsed


def _string_map(environ: Mapping[str, str], name: str) -> dict[str, str]:
    parsed = _json_object(environ, name)
    return {str(key): str(item) for key, item in parsed.items()}


def _tuple(environ: Mapping[str, str], name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    value = _raw(environ, name)
    if value is None:
        return default
    return tuple(item.strip() for item in value.split(MIME_SEPARATOR) if item.strip())


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
    #: verbatim -- see the docs for per-backend recipes.
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


def _build(environ: Mapping[str, str]) -> Settings:
    """Resolve and validate every setting against ``environ``.

    ``environ`` is a parameter rather than a direct :data:`os.environ` read so
    the whole environment can be exercised in a test without mutating the real
    one, and so resolution stays free of global state.
    """
    backend_type = _string(environ, "backend.type", "openai")
    if backend_type not in BACKEND_TYPES:
        raise ConfigError(
            f"{env_name('backend.type')} must be one of "
            f"{', '.join(BACKEND_TYPES)} (got {backend_type!r})"
        )
    backend_kind = cast(BackendType, backend_type)

    prompt_layout = _string(environ, "request.prompt_layout", "fused")
    if prompt_layout not in {"fused", "split"}:
        raise ConfigError(
            f"{env_name('request.prompt_layout')} must be 'fused' or 'split'"
        )
    layout = cast('Literal["fused", "split"]', prompt_layout)

    base_url = _string(environ, "backend.base_url", "http://127.0.0.1:8080")
    if not base_url.startswith(("http://", "https://")):
        raise ConfigError(f"{env_name('backend.base_url')} must be an http(s) URL")

    max_questions = _int(environ, "request.max_questions", MAX_QUESTIONS)
    if not 1 <= max_questions <= MAX_QUESTIONS:
        raise ConfigError(
            f"{env_name('request.max_questions')} must be between 1 and {MAX_QUESTIONS}"
        )

    max_images = _int(environ, "multimodal.max_images", 4)
    if max_images < 1:
        raise ConfigError(f"{env_name('multimodal.max_images')} must be at least 1")

    max_concurrency = _int(environ, "backend.max_concurrency", 32)
    if max_concurrency < 1:
        raise ConfigError(f"{env_name('backend.max_concurrency')} must be at least 1")

    top_logprobs = _int(environ, "backend.top_logprobs", 128)
    if not 16 <= top_logprobs <= 4096:
        raise ConfigError(f"{env_name('backend.top_logprobs')} must be between 16 and 4096")

    return Settings(
        server=ServerSettings(
            host=_string(environ, "server.host", "0.0.0.0"),
            port=_int(environ, "server.port", 8000),
        ),
        backend=BackendSettings(
            type=backend_kind,
            base_url=base_url.rstrip("/"),
            model=_optional_string(environ, "backend.model"),
            api_key=_optional_string(environ, "backend.api_key"),
            timeout_seconds=_float(environ, "backend.timeout_seconds", 30.0),
            max_concurrency=max_concurrency,
            top_logprobs=top_logprobs,
            supports_images=_tri_state(environ, "backend.supports_images"),
            extra_headers=_string_map(environ, "backend.extra_headers"),
            extra_body=_json_object(environ, "backend.extra_body"),
        ),
        request=RequestSettings(
            max_questions=max_questions,
            total_timeout_seconds=_float(environ, "request.total_timeout_seconds", 60.0),
            prompt_layout=layout,
        ),
        multimodal=MultimodalSettings(
            enabled=_bool(environ, "multimodal.enabled", True),
            max_images=max_images,
            max_image_bytes=_int(environ, "multimodal.max_image_bytes", 5 * 1024 * 1024),
            allow_remote_urls=_bool(environ, "multimodal.allow_remote_urls", False),
            allowed_mime_prefixes=_tuple(environ, "multimodal.allowed_mime_prefixes", ("image/",)),
        ),
        log_level=_string(environ, "logging.level", "INFO").upper(),
    )


def build_default_settings() -> Settings:
    """Settings with every default applied, ignoring the environment.

    Useful for embedding the gateway, and for tests that want a known baseline
    rather than whatever the surrounding shell happens to export.
    """
    return _build({})


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    """Build settings from the environment.

    Pass ``environ`` to resolve against a specific mapping instead of the live
    process environment; omit it to read :data:`os.environ`.
    """
    return _build(os.environ if environ is None else environ)
