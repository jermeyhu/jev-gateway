"""TypeSafe-compatible image asset validation and OpenAI part rendering."""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from app.config import MultimodalSettings
from app.utils.errors import InvalidRequest


@dataclass(frozen=True, slots=True)
class ImageRef:
    """A validated image, ready to be handed to an inference backend."""

    mime: str
    payload: str
    url: str
    reference: str = ""

_BASE64_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
)
_SNIFF_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
)


def _sniff_mime(payload: bytes) -> str:
    for magic, mime in _SNIFF_MAGIC:
        if payload.startswith(magic):
            return mime
    if payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


def _check_mime(mime: str, settings: MultimodalSettings, index: int) -> str:
    mime = (mime or "application/octet-stream").strip().lower()
    prefixes = tuple(p.lower() for p in settings.allowed_mime_prefixes)
    if not any(mime.startswith(prefix) for prefix in prefixes):
        raise InvalidRequest(
            f"images[{index}] has unsupported media type {mime!r}; "
            f"allowed prefixes: {', '.join(prefixes)}"
        )
    return mime


def _decode(payload: str, settings: MultimodalSettings, index: int) -> bytes:
    compact = "".join(payload.split())
    if not compact:
        raise InvalidRequest(f"image asset {index} is empty")
    if any(char not in _BASE64_CHARS for char in compact):
        raise InvalidRequest(f"image asset {index} is not valid base64")
    try:
        raw = base64.b64decode(compact, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise InvalidRequest(f"image asset {index} is not valid base64") from exc
    if len(raw) > settings.max_image_bytes:
        raise InvalidRequest(
            f"image asset {index} is {len(raw)} bytes, above the "
            f"{settings.max_image_bytes} byte limit"
        )
    return raw


def parse_assets(
    assets: Mapping[str, object] | None, settings: MultimodalSettings
) -> dict[str, ImageRef]:
    """Validate TypeSafe-style image assets and return them by reference."""
    if not assets:
        return {}
    if not settings.enabled:
        raise InvalidRequest("this gateway instance has multimodal input disabled")
    if len(assets) > settings.max_images:
        raise InvalidRequest(
            f"too many image assets: {len(assets)} (limit {settings.max_images})"
        )

    formats = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}
    parsed: dict[str, ImageRef] = {}
    total_reference_bytes = 0
    for index, (reference, asset) in enumerate(assets.items()):
        reference_bytes = len(reference.encode("utf-8"))
        total_reference_bytes += reference_bytes
        if not reference.strip() or reference_bytes > 1024:
            raise InvalidRequest(f"assets[{reference!r}] has an invalid reference")
        if total_reference_bytes > 65536:
            raise InvalidRequest("combined image asset references exceed 65536 bytes")
        data = getattr(asset, "data_base64", None)
        image_format = getattr(asset, "format", None)
        if not data or image_format not in formats:
            raise InvalidRequest(
                f"assets[{reference!r}] requires data_base64 and a supported format"
            )
        payload = _decode(data, settings, index)
        mime = formats[image_format]
        if _sniff_mime(payload) != mime:
            raise InvalidRequest(
                f"assets[{reference!r}] format {image_format!r} does not match image data"
            )
        _check_mime(mime, settings, index)
        compact = "".join(data.split())
        parsed[reference] = ImageRef(
            mime, compact, f"data:{mime};base64,{compact}", reference
        )
    return parsed


def referenced_assets(value: object, assets: Mapping[str, ImageRef]) -> list[ImageRef]:
    """Return image uses found in JSON string values, preserving occurrence order."""
    strings: list[str] = []

    def collect(item: object) -> None:
        if isinstance(item, str):
            strings.append(item)
        elif isinstance(item, dict):
            for child in item.values():
                collect(child)
        elif isinstance(item, list):
            for child in item:
                collect(child)

    collect(value)
    if not assets:
        return []
    references = sorted(assets, key=len, reverse=True)
    matcher = re.compile("|".join(re.escape(reference) for reference in references))
    return [assets[match.group()] for text in strings for match in matcher.finditer(text)]


def to_openai_parts(refs: Iterable[ImageRef]) -> list[dict[str, object]]:
    """Render image references as OpenAI-style chat content parts."""
    return [{"type": "image_url", "image_url": {"url": ref.url}} for ref in refs]
