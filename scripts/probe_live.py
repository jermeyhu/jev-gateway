"""Ad-hoc boundary probe against a live gateway (run manually, not a pytest)."""

from __future__ import annotations

import base64

import httpx

BASE = "http://127.0.0.1:8000"
STATE = {"service": "checkout", "status": "degraded"}
Q = {"ok": {"type": "noul", "instructions": "Is it healthy?"}}
PNG_DATA_URL = "data:image/png;base64,{IMG}"


def probe(name: str, payload: dict) -> None:
    r = httpx.post(BASE + "/v1/systemone", json=payload, timeout=30)
    detail = ""
    if r.status_code != 200:
        try:
            detail = r.json().get("error", {})
        except Exception:
            detail = r.text[:120]
    print(f"{name:28} -> {r.status_code}  {detail}")


def load_image() -> str:
    with open("test_image.png", "rb") as handle:
        return base64.b64encode(handle.read()).decode()


def candidates(count: int) -> dict:
    return {
        "state": STATE,
        "questions": {
            "c": {
                "type": "choice",
                "instructions": "pick",
                "criteria": {f"k{i}": f"v{i}" for i in range(count)},
            }
        },
    }


def main() -> None:
    img = load_image()
    data_url = PNG_DATA_URL.format(IMG=img)
    probe("baseline (no image)", {"state": STATE, "questions": Q})
    probe("1 valid image", {"state": STATE, "questions": Q, "images": [data_url]})
    probe("raw base64 (sniffed)", {"state": STATE, "questions": Q, "images": [img]})
    probe(
        "remote url (disabled)",
        {"state": STATE, "questions": Q, "images": ["https://example.com/a.png"]},
    )
    probe("bad base64", {"state": STATE, "questions": Q, "images": ["not!base64!!"]})
    probe(
        "unsupported mime",
        {"state": STATE, "questions": Q, "images": [f"data:text/plain;base64,{img}"]},
    )
    probe("5 images (>4)", {"state": STATE, "questions": Q, "images": [data_url] * 5})
    probe("17 candidates (reject)", candidates(17))
    probe("16 candidates (accept)", candidates(16))


if __name__ == "__main__":
    main()
