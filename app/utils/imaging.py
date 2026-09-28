"""Image reference validation and normalisation.

Three input shapes are accepted, matching what the inference servers understand:

* ``data:image/jpeg;base64,...``  — data URL, forwarded verbatim to the backend
* ``https://...``                 — remote URL, forwarded verbatim (opt-in)
* raw base64                      — wrapped into a data URL using a sniffed mime type
"""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from app.config import MultimodalSettings
from app.utils.errors import InvalidRequest

ImageKind = Literal["data_url", "http_url", "raw_base64"]


@dataclass(frozen=True, slots=True)
class ImageRef:
    """A validated image, ready to be handed to an inference backend."""

    kind: ImageKind
    mime: str
    payload: str
    url: str

_DATA_URL_RE = re.compile(r"^data:(?P<mime>[^;,]+)?(?P<params>;[^,]*)?,(?P<data>.*)$", re.DOTALL)
_BASE64_CHARS_RE = re.compile(r"^[A-Za-z0-9+/\r\n]+={0,2}$")
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
        raise InvalidRequest(f"images[{index}] is empty")
    if not _BASE64_CHARS_RE.match(compact):
        raise InvalidRequest(f"images[{index}] is not valid base64")
    try:
        raw = base64.b64decode(compact, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise InvalidRequest(f"images[{index}] is not valid base64") from exc
    if len(raw) > settings.max_image_bytes:
        raise InvalidRequest(
            f"images[{index}] is {len(raw)} bytes, above the "
            f"{settings.max_image_bytes} byte limit"
        )
    return raw


def parse_images(
    images: Iterable[str] | None, settings: MultimodalSettings
) -> list[ImageRef]:
    """Validate raw request images and return normalised references."""
    if not images:
        return []
    incoming = list(images)
    if not settings.enabled:
        raise InvalidRequest("this gateway instance has multimodal input disabled")
    if len(incoming) > settings.max_images:
        raise InvalidRequest(
            f"too many images: {len(incoming)} (limit {settings.max_images})"
        )

    refs: list[ImageRef] = []
    for index, raw in enumerate(incoming):
        item = raw.strip()
        match = _DATA_URL_RE.match(item)
        if match:
            mime = _check_mime(match.group("mime") or "", settings, index)
            payload = match.group("data")
            _decode(payload, settings, index)
            refs.append(ImageRef("data_url", mime, "".join(payload.split()), item))
            continue
        if item.startswith(("http://", "https://")):
            if not settings.allow_remote_urls:
                raise InvalidRequest(
                    "remote image URLs are disabled; enable multimodal.allow_remote_urls "
                    "or pass a data URL"
                )
            refs.append(ImageRef("http_url", "", "", item))
            continue
        payload = _decode(item, settings, index)
        mime = _sniff_mime(payload)
        if mime == "application/octet-stream":
            raise InvalidRequest(
                f"images[{index}] is raw base64 of an unrecognised format; "
                "use a data URL with an explicit media type"
            )
        _check_mime(mime, settings, index)
        data_url = f"data:{mime};base64,{''.join(item.split())}"
        refs.append(ImageRef("raw_base64", mime, "".join(item.split()), data_url))
    return refs


def to_openai_parts(refs: Iterable[ImageRef]) -> list[dict[str, object]]:
    """Render image references as OpenAI-style chat content parts."""
    return [{"type": "image_url", "image_url": {"url": ref.url}} for ref in refs]
