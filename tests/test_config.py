"""Configuration is resolved from environment variables, nothing else.

These tests drive ``load_settings(environ=...)`` with an explicit mapping rather
than mutating the real process environment, so a stray ``JEV_`` variable in the
developer's shell can never change what they assert.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from app.config import (
    ConfigError,
    build_default_settings,
    env_name,
    load_settings,
)

#: Every variable, with a value of the right shape.  One mapping drives the
#: round-trip test so a new field cannot be added without being exercised here.
EVERYTHING = {
    "JEV_SERVER_HOST": "127.0.0.1",
    "JEV_SERVER_PORT": "9001",
    "JEV_BACKEND_TYPE": "openai",
    "JEV_BACKEND_BASE_URL": "https://example.test/v1/",
    "JEV_BACKEND_MODEL": "qwen3-0.6b",
    "JEV_BACKEND_API_KEY": "sk-test",
    "JEV_BACKEND_TIMEOUT_SECONDS": "12.5",
    "JEV_BACKEND_MAX_CONCURRENCY": "4",
    "JEV_BACKEND_TOP_LOGPROBS": "32",
    "JEV_BACKEND_SUPPORTS_IMAGES": "false",
    "JEV_BACKEND_EXTRA_HEADERS": '{"X-Trace": "on"}',
    "JEV_BACKEND_EXTRA_BODY": '{"chat_template_kwargs": {"enable_thinking": false}}',
    "JEV_REQUEST_MAX_QUESTIONS": "8",
    "JEV_REQUEST_TOTAL_TIMEOUT_SECONDS": "30",
    "JEV_REQUEST_PROMPT_LAYOUT": "split",
    "JEV_MULTIMODAL_ENABLED": "false",
    "JEV_MULTIMODAL_MAX_IMAGES": "2",
    "JEV_MULTIMODAL_MAX_IMAGE_BYTES": "1024",
    "JEV_MULTIMODAL_ALLOW_REMOTE_URLS": "true",
    "JEV_MULTIMODAL_ALLOWED_MIME_PREFIXES": "image/,application/pdf",
    "JEV_LOGGING_LEVEL": "debug",
}


def test_env_name_prefixes_and_upper_cases_the_field() -> None:
    assert env_name("backend.base_url") == "JEV_BACKEND_BASE_URL"
    assert env_name("logging.level") == "JEV_LOGGING_LEVEL"
    assert env_name("server.port") == "JEV_SERVER_PORT"


def test_an_empty_environment_yields_the_defaults() -> None:
    assert load_settings({}) == build_default_settings()


def test_defaults_are_sane() -> None:
    settings = load_settings({})
    assert settings.server.host == "0.0.0.0"
    assert settings.server.port == 8000
    assert settings.backend.type == "openai"
    assert settings.backend.base_url == "http://127.0.0.1:8080"
    assert settings.backend.model is None
    assert settings.backend.api_key is None
    assert settings.backend.max_concurrency > 0
    assert settings.backend.top_logprobs >= 16
    assert settings.backend.supports_images is None
    assert settings.backend.images_enabled is True
    assert settings.request.max_questions > 0
    assert settings.request.prompt_layout in {"fused", "split"}
    assert settings.multimodal.enabled is True
    assert settings.multimodal.max_images >= 1
    assert settings.multimodal.allow_remote_urls is False
    assert settings.log_level == "INFO"


def test_every_variable_is_read() -> None:
    settings = load_settings(EVERYTHING)

    assert settings.server.host == "127.0.0.1"
    assert settings.server.port == 9001
    assert settings.backend.type == "openai"
    # The trailing slash is stripped so callers can always join paths safely.
    assert settings.backend.base_url == "https://example.test/v1"
    assert settings.backend.model == "qwen3-0.6b"
    assert settings.backend.api_key == "sk-test"
    assert settings.backend.timeout_seconds == 12.5
    assert settings.backend.max_concurrency == 4
    assert settings.backend.top_logprobs == 32
    assert settings.backend.supports_images is False
    assert settings.backend.images_enabled is False
    assert settings.backend.extra_headers == {"X-Trace": "on"}
    assert settings.backend.extra_body == {"chat_template_kwargs": {"enable_thinking": False}}
    assert settings.request.max_questions == 8
    assert settings.request.total_timeout_seconds == 30.0
    assert settings.request.prompt_layout == "split"
    assert settings.multimodal.enabled is False
    assert settings.multimodal.max_images == 2
    assert settings.multimodal.max_image_bytes == 1024
    assert settings.multimodal.allow_remote_urls is True
    assert settings.multimodal.allowed_mime_prefixes == ("image/", "application/pdf")
    assert settings.log_level == "DEBUG"


def test_extra_body_is_empty_by_default() -> None:
    assert load_settings({}).backend.extra_body == {}
    assert load_settings({}).backend.extra_headers == {}


def test_blank_values_fall_back_to_the_default() -> None:
    # Compose passes unset keys through as empty strings; treating "" as unset
    # keeps "JEV_BACKEND_MODEL=" from becoming an empty model name.
    settings = load_settings({"JEV_BACKEND_MODEL": "", "JEV_BACKEND_API_KEY": "   "})
    assert settings.backend.model is None
    assert settings.backend.api_key is None


def test_supports_images_is_a_tri_state() -> None:
    assert load_settings({}).backend.supports_images is None
    assert load_settings({"JEV_BACKEND_SUPPORTS_IMAGES": "auto"}).backend.supports_images is None
    assert load_settings({"JEV_BACKEND_SUPPORTS_IMAGES": "true"}).backend.supports_images is True
    assert load_settings({"JEV_BACKEND_SUPPORTS_IMAGES": "off"}).backend.supports_images is False


@pytest.mark.parametrize("value", ["1", "yes", "YES", "on", "0", "no", "NO", "off"])
def test_booleans_accept_the_usual_spellings(value: str) -> None:
    # The parser lower-cases, so every case of a word means the same thing.
    truthy = {"1", "yes", "on"}
    assert load_settings({"JEV_MULTIMODAL_ENABLED": value}).multimodal.enabled == (
        value.lower() in truthy
    )


def test_mime_prefixes_split_on_comma_and_drop_blanks() -> None:
    settings = load_settings(
        {"JEV_MULTIMODAL_ALLOWED_MIME_PREFIXES": " image/ , ,application/pdf "}
    )
    assert settings.multimodal.allowed_mime_prefixes == ("image/", "application/pdf")


@pytest.mark.parametrize(
    ("environ", "variable"),
    [
        ({"JEV_BACKEND_TYPE": "anthropic"}, "JEV_BACKEND_TYPE"),
        ({"JEV_BACKEND_BASE_URL": "127.0.0.1:8080"}, "JEV_BACKEND_BASE_URL"),
        ({"JEV_BACKEND_BASE_URL": "ftp://example.test"}, "JEV_BACKEND_BASE_URL"),
        ({"JEV_REQUEST_PROMPT_LAYOUT": "stacked"}, "JEV_REQUEST_PROMPT_LAYOUT"),
        ({"JEV_BACKEND_TOP_LOGPROBS": "15"}, "JEV_BACKEND_TOP_LOGPROBS"),
        ({"JEV_BACKEND_TOP_LOGPROBS": "4097"}, "JEV_BACKEND_TOP_LOGPROBS"),
        ({"JEV_REQUEST_MAX_QUESTIONS": "0"}, "JEV_REQUEST_MAX_QUESTIONS"),
        ({"JEV_REQUEST_MAX_QUESTIONS": "65"}, "JEV_REQUEST_MAX_QUESTIONS"),
        ({"JEV_MULTIMODAL_MAX_IMAGES": "0"}, "JEV_MULTIMODAL_MAX_IMAGES"),
        ({"JEV_BACKEND_MAX_CONCURRENCY": "0"}, "JEV_BACKEND_MAX_CONCURRENCY"),
        ({"JEV_BACKEND_SUPPORTS_IMAGES": "maybe"}, "JEV_BACKEND_SUPPORTS_IMAGES"),
        ({"JEV_MULTIMODAL_ENABLED": "sometimes"}, "JEV_MULTIMODAL_ENABLED"),
        ({"JEV_BACKEND_EXTRA_BODY": "not json"}, "JEV_BACKEND_EXTRA_BODY"),
        ({"JEV_BACKEND_EXTRA_BODY": "[1, 2]"}, "JEV_BACKEND_EXTRA_BODY"),
        ({"JEV_BACKEND_EXTRA_HEADERS": "oops"}, "JEV_BACKEND_EXTRA_HEADERS"),
        ({"JEV_SERVER_PORT": "http"}, "JEV_SERVER_PORT"),
        ({"JEV_BACKEND_TIMEOUT_SECONDS": "soon"}, "JEV_BACKEND_TIMEOUT_SECONDS"),
    ],
)
def test_unusable_values_are_rejected_and_name_the_variable(
    environ: dict[str, str], variable: str
) -> None:
    # A bad value must fail at startup with the variable named, never degrade
    # silently to the default -- a typo in a shell profile is otherwise invisible.
    with pytest.raises(ConfigError, match=variable):
        load_settings(environ)


def test_extra_body_must_be_a_json_object() -> None:
    assert load_settings({"JEV_BACKEND_EXTRA_BODY": '{"a": 1}'}).backend.extra_body == {"a": 1}
    with pytest.raises(ConfigError, match="JEV_BACKEND_EXTRA_BODY"):
        load_settings({"JEV_BACKEND_EXTRA_BODY": '"just a string"'})


def test_unrelated_variables_are_ignored() -> None:
    settings = load_settings({"PATH": "/usr/bin", "JEV_SOMETHING_ELSE": "x", "HOME": "/root"})
    assert settings == build_default_settings()


def test_load_settings_defaults_to_the_process_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JEV_BACKEND_BASE_URL", "http://10.0.0.9:9999")
    monkeypatch.setenv("JEV_SERVER_PORT", "8123")
    settings = load_settings()
    assert settings.backend.base_url == "http://10.0.0.9:9999"
    assert settings.server.port == 8123


def test_settings_are_frozen() -> None:
    settings = load_settings({})
    with pytest.raises(FrozenInstanceError):
        settings.log_level = "DEBUG"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        settings.backend.timeout_seconds = 1.0  # type: ignore[misc]


def test_replace_produces_an_independent_copy() -> None:
    settings = load_settings({})
    other = replace(settings, log_level="DEBUG")
    assert other.log_level == "DEBUG"
    assert settings.log_level == "INFO"
    # The nested sections are shared, not re-derived, so replace() on a section
    # is the supported way to vary one knob without rebuilding the whole tree.
    assert other.backend is settings.backend


def test_build_default_settings_ignores_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JEV_BACKEND_BASE_URL", "http://10.0.0.9:9999")
    assert build_default_settings().backend.base_url == "http://127.0.0.1:8080"
