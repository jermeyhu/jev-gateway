from __future__ import annotations

import base64
import json
from dataclasses import replace

import pytest

from app.config import build_default_settings
from app.utils.errors import InvalidRequest
from app.utils.imaging import ImageRef, parse_images, to_openai_parts

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 32).decode()


def multimodal(**overrides):
    return replace(build_default_settings().multimodal, **overrides)


def test_data_url_is_forwarded_verbatim() -> None:
    url = f"data:image/png;base64,{PNG}"
    (ref,) = parse_images([url], multimodal())
    assert ref.kind == "data_url"
    assert ref.mime == "image/png"
    assert ref.payload == PNG
    assert ref.url == url


def test_whitespace_in_base64_is_stripped() -> None:
    wrapped = "\n".join(PNG[i : i + 20] for i in range(0, len(PNG), 20))
    (ref,) = parse_images([f"data:image/png;base64,{wrapped}"], multimodal())
    assert ref.payload == PNG


def test_raw_base64_is_sniffed_and_wrapped() -> None:
    (ref,) = parse_images([PNG], multimodal())
    assert ref.kind == "raw_base64"
    assert ref.mime == "image/png"
    assert ref.url == f"data:image/png;base64,{PNG}"


def test_unknown_raw_format_is_rejected() -> None:
    junk = base64.b64encode(b"not an image at all").decode()
    with pytest.raises(InvalidRequest, match="unrecognised"):
        parse_images([junk], multimodal())


def test_non_image_mime_is_rejected() -> None:
    with pytest.raises(InvalidRequest, match="unsupported media type"):
        parse_images(["data:text/plain;base64,aGk="], multimodal())


def test_invalid_base64_is_rejected() -> None:
    with pytest.raises(InvalidRequest, match="not valid base64"):
        parse_images(["data:image/png;base64,***"], multimodal())


def test_oversized_image_is_rejected() -> None:
    with pytest.raises(InvalidRequest, match="above the"):
        parse_images([PNG], multimodal(max_image_bytes=8))


def test_too_many_images_is_rejected() -> None:
    url = f"data:image/png;base64,{PNG}"
    with pytest.raises(InvalidRequest, match="too many images"):
        parse_images([url, url], multimodal(max_images=1))


def test_remote_urls_require_opt_in() -> None:
    with pytest.raises(InvalidRequest, match="remote image URLs are disabled"):
        parse_images(["https://example.com/a.png"], multimodal())
    (ref,) = parse_images(
        ["https://example.com/a.png"], multimodal(allow_remote_urls=True)
    )
    assert ref.kind == "http_url"
    assert ref.url == "https://example.com/a.png"


def test_disabled_multimodal_rejects_images() -> None:
    with pytest.raises(InvalidRequest, match="multimodal input disabled"):
        parse_images([f"data:image/png;base64,{PNG}"], multimodal(enabled=False))


def test_no_images_returns_empty_list() -> None:
    assert parse_images(None, multimodal()) == []
    assert parse_images([], multimodal()) == []


def test_openai_parts_render_image_urls() -> None:
    (ref,) = parse_images([f"data:image/png;base64,{PNG}"], multimodal())
    assert to_openai_parts([ref]) == [
        {"type": "image_url", "image_url": {"url": ref.url}}
    ]


def test_image_ref_is_hashable_and_frozen() -> None:
    ref = ImageRef("data_url", "image/png", PNG, f"data:image/png;base64,{PNG}")
    with pytest.raises(AttributeError):
        ref.mime = "image/jpeg"  # type: ignore[misc]
    assert json.dumps({"url": ref.url})  # serialisable
