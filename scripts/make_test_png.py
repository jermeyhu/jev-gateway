#!/usr/bin/env python3
"""Generate a tiny valid PNG without Pillow (stdlib only).

    python scripts/make_test_png.py out.png [width] [height]

Used to exercise the gateway's multimodal path when no sample image is around.
The image is a red/blue diagonal split so a vision model has something to look at.
"""

from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path


def _chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def make_png(width: int, height: int) -> bytes:
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter type 0 for this scanline
        for x in range(width):
            red = x + y < width  # diagonal split
            raw += bytes((255, 0, 0) if red else (0, 0, 255))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + _chunk(b"IEND", b"")
    )


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "test.png")
    width = int(sys.argv[2]) if len(sys.argv) > 2 else 64
    height = int(sys.argv[3]) if len(sys.argv) > 3 else 64
    out.write_bytes(make_png(width, height))
    print(f"wrote {out} ({width}x{height}, {out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
