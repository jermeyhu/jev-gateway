from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from app.config import ConfigError, build_default_settings, load_settings

CONFIG = """
server:
  host: 127.0.0.1
  port: 9001
backend:
  type: openai
  base_url: http://vllm:8000/
  model: Qwen3-0.6B
  api_key: secret
  timeout_seconds: 12.5
  max_concurrency: 4
  supports_images: false
  extra_body:
    chat_template_kwargs:
      enable_thinking: false
request:
  prompt_layout: split
  max_questions: 8
multimodal:
  max_images: 2
  allow_remote_urls: true
logging:
  level: debug
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_defaults_are_sane() -> None:
    settings = build_default_settings()
    assert settings.backend.type == "openai"
    assert settings.request.prompt_layout == "fused"
    assert settings.multimodal.enabled is True


def test_full_config_round_trip(tmp_path: Path) -> None:
    settings = load_settings(write(tmp_path, CONFIG))
    assert settings.server.host == "127.0.0.1"
    assert settings.server.port == 9001
    assert settings.backend.type == "openai"
    assert settings.backend.base_url == "http://vllm:8000"  # trailing slash trimmed
    assert settings.backend.api_key == "secret"
    assert settings.backend.timeout_seconds == 12.5
    assert settings.backend.supports_images is False
    assert settings.backend.images_enabled is False
    assert settings.backend.extra_body == {"chat_template_kwargs": {"enable_thinking": False}}
    assert settings.request.prompt_layout == "split"
    assert settings.request.max_questions == 8
    assert settings.multimodal.max_images == 2
    assert settings.multimodal.allow_remote_urls is True
    assert settings.log_level == "DEBUG"


def test_shipped_config_file_is_valid() -> None:
    shipped = Path(__file__).resolve().parents[1] / "config.yaml"
    settings = load_settings(shipped)
    assert settings.source_path == shipped


def test_default_extra_body_is_empty() -> None:
    settings = build_default_settings()
    # Nothing engine-specific may be sent unless the operator opts in.
    assert settings.backend.extra_body == {}


def test_extra_body_must_be_a_mapping(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="extra_body"):
        load_settings(write(tmp_path, "backend:\n  extra_body: not-a-mapping\n"))


def test_missing_explicit_config_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_settings(tmp_path / "nope.yaml")


def test_env_var_points_at_the_config(tmp_path: Path, monkeypatch) -> None:
    path = write(tmp_path, CONFIG)
    monkeypatch.setenv("JEV_GATEWAY_CONFIG", str(path))
    assert load_settings().server.port == 9001


@pytest.mark.parametrize(
    "text, message",
    [
        ("backend:\n  type: triton\n", "backend.type"),
        ("backend:\n  base_url: ftp://x\n", "base_url"),
        ("request:\n  prompt_layout: weird\n", "prompt_layout"),
        ("backend:\n  top_logprobs: 4\n", "top_logprobs"),
        ("request:\n  max_questions: 0\n", "max_questions"),
        ("backend:\n  supports_images: maybe\n", "supports_images"),
        ("backend:\n  extra_body: [1, 2]\n", "extra_body"),
        ("typo_section:\n  a: 1\n", "unknown keys"),
        ("backend:\n  nope: 1\n", "unknown keys in config section"),
        ("multimodal:\n  max_images: 0\n", "max_images"),
        ("server:\n  host: h\n  port: notanumber\n", "invalid literal"),
    ],
)
def test_invalid_configs_are_rejected(tmp_path: Path, text: str, message: str) -> None:
    with pytest.raises((ConfigError, ValueError)) as excinfo:
        load_settings(write(tmp_path, text))
    assert message in str(excinfo.value)


def test_settings_are_frozen() -> None:
    settings = build_default_settings()
    with pytest.raises(AttributeError):
        settings.log_level = "DEBUG"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        settings.backend.type = "openai"  # type: ignore[misc]


def test_replace_produces_an_independent_copy() -> None:
    base = build_default_settings()
    tuned = replace(base, request=replace(base.request, prompt_layout="split"))
    assert base.request.prompt_layout == "fused"
    assert tuned.request.prompt_layout == "split"
