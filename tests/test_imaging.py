from __future__ import annotations

import base64
import json
from dataclasses import replace

import pytest

from app.config import build_default_settings
from app.schemas.request import ImageAsset
from app.utils.errors import InvalidRequest
from app.utils.imaging import ImageRef, parse_assets, referenced_assets, to_openai_parts

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 32).decode()


def multimodal(**overrides):
    return replace(build_default_settings().multimodal, **overrides)


def test_base64_asset_is_normalized_to_a_data_url() -> None:
    ref = parse_assets(
        {"photo-ref": ImageAsset(type="image", format="png", data_base64=PNG)},
        multimodal(),
    )["photo-ref"]
    assert ref.mime == "image/png"
    assert ref.payload == PNG
    assert ref.url == f"data:image/png;base64,{PNG}"
    assert ref.reference == "photo-ref"


def test_invalid_base64_is_rejected() -> None:
    with pytest.raises(InvalidRequest, match="not valid base64"):
        parse_assets(
            {"photo": ImageAsset(type="image", format="png", data_base64="***")},
            multimodal(),
        )


def test_oversized_image_is_rejected() -> None:
    with pytest.raises(InvalidRequest, match="above the"):
        parse_assets(
            {"photo": ImageAsset(type="image", format="png", data_base64=PNG)},
            multimodal(max_image_bytes=8),
        )


def test_too_many_images_is_rejected() -> None:
    asset = ImageAsset(type="image", format="png", data_base64=PNG)
    with pytest.raises(InvalidRequest, match="too many image assets"):
        parse_assets({"one": asset, "two": asset}, multimodal(max_images=1))


def test_disabled_multimodal_rejects_images() -> None:
    with pytest.raises(InvalidRequest, match="multimodal input disabled"):
        parse_assets(
            {"photo": ImageAsset(type="image", format="png", data_base64=PNG)},
            multimodal(enabled=False),
        )


def test_referenced_assets_follow_request_string_values() -> None:
    assets = parse_assets(
        {
            "photo": ImageAsset(type="image", format="png", data_base64=PNG),
            "other": ImageAsset(type="image", format="png", data_base64=PNG),
        },
        multimodal(),
    )
    assert [item.reference for item in referenced_assets({"photo": "see `photo`"}, assets)] == [
        "photo"
    ]


def test_repeated_asset_references_are_repeated_uses() -> None:
    assets = parse_assets(
        {"photo": ImageAsset(type="image", format="png", data_base64=PNG)},
        multimodal(),
    )
    assert [item.reference for item in referenced_assets("photo then photo", assets)] == [
        "photo",
        "photo",
    ]


def test_longer_overlapping_reference_wins() -> None:
    assets = parse_assets(
        {
            "photo": ImageAsset(type="image", format="png", data_base64=PNG),
            "photo-large": ImageAsset(type="image", format="png", data_base64=PNG),
        },
        multimodal(),
    )
    assert [
        item.reference for item in referenced_assets("photo-large", assets)
    ] == ["photo-large"]


def test_openai_parts_render_image_urls() -> None:
    ref = parse_assets(
        {"photo": ImageAsset(type="image", format="png", data_base64=PNG)},
        multimodal(),
    )["photo"]
    assert to_openai_parts([ref]) == [
        {"type": "image_url", "image_url": {"url": ref.url}}
    ]


def test_image_ref_is_hashable_and_frozen() -> None:
    ref = ImageRef("image/png", PNG, f"data:image/png;base64,{PNG}", "photo")
    with pytest.raises(AttributeError):
        ref.mime = "image/jpeg"  # type: ignore[misc]
    assert json.dumps({"url": ref.url})  # serialisable
