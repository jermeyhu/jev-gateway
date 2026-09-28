#!/usr/bin/env python3
"""End-to-end smoke test against a running gateway.

    python scripts/smoke_test.py --url http://127.0.0.1:8000
    python scripts/smoke_test.py --url http://127.0.0.1:8000 --image photo.jpg

Exits non-zero as soon as anything looks wrong, so it can be used as a
container health probe in CI.
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import sys
import time
from pathlib import Path
from typing import Any

import httpx

STATE = {
    "service": "checkout",
    "status": "degraded",
    "error_rate": 0.42,
    "p99_latency_ms": 3100,
    "deploy": "2026-09-27T10:12:00Z",
}

QUESTIONS: dict[str, dict[str, Any]] = {
    "is_healthy": {"type": "noul", "instructions": "Is the service healthy?"},
    "severity": {
        "type": "choice",
        "instructions": "Pick the incident severity.",
        "criteria": {
            "sev1": "Total outage, revenue lost",
            "sev2": "Degraded, some users affected",
            "sev3": "Minor, workaround exists",
        },
    },
    "urgency": {
        "type": "score",
        "instructions": "How urgent is the response?",
        "criteria": ["can wait for the weekly review", "today", "right now"],
    },
}


def data_url(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    payload = base64.b64encode(path.read_bytes()).decode()
    return f"data:{mime};base64,{payload}"


def fail(message: str) -> None:
    print(f"FAIL  {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000", help="gateway base URL")
    parser.add_argument("--image", type=Path, help="optional image file for a multimodal check")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--json", type=Path, help="write the raw response here")
    parser.add_argument("--api-key", default=None, help="sent as a bearer token if set")
    args = parser.parse_args()

    headers = {"authorization": f"Bearer {args.api_key}"} if args.api_key else {}
    base = args.url.rstrip("/")

    with httpx.Client(base_url=base, headers=headers, timeout=args.timeout) as client:
        health = client.get("/healthz")
        if health.status_code != 200:
            fail(f"/healthz returned {health.status_code}: {health.text[:200]}")
        print(f"ok    /healthz  {health.json()}")

        ready = client.get("/readyz")
        if ready.status_code != 200:
            fail(f"/readyz returned {ready.status_code}: {ready.text[:200]}")
        print(f"ok    /readyz   {ready.json()}")

        payload: dict[str, Any] = {"state": STATE, "questions": QUESTIONS}
        if args.image:
            if not args.image.exists():
                fail(f"image not found: {args.image}")
            payload["images"] = [data_url(args.image)]

        started = time.perf_counter()
        response = client.post("/v1/systemone", json=payload)
        elapsed = (time.perf_counter() - started) * 1000
        if response.status_code != 200:
            fail(f"/v1/systemone returned {response.status_code}: {response.text[:400]}")

        body = response.json()
        answers = body.get("answers") or {}
        if set(answers) != set(QUESTIONS):
            fail(f"expected answers for {sorted(QUESTIONS)}, got {sorted(answers)}")

        noul = answers["is_healthy"]
        if noul.get("type") != "noul" or not 0.0 <= noul.get("noul", -1) <= 1.0:
            fail(f"bad noul answer: {noul}")

        choice = answers["severity"]
        probabilities = choice.get("probabilities") or {}
        if choice.get("choice") not in probabilities:
            fail(f"choice {choice.get('choice')!r} is not in its own probabilities")
        if abs(sum(probabilities.values()) - 1.0) > 1e-6:
            fail(f"probabilities do not sum to 1: {probabilities}")

        score = answers["urgency"]
        if not 0.0 <= score.get("score", -1) <= 2.0:
            fail(f"score outside [0, 2]: {score}")

        if not body.get("usage"):
            fail("response has no usage block")

    print(f"ok    /v1/systemone  {elapsed:.0f} ms wall clock")
    print(f"      model    {body['model']}")
    print(f"      usage    {body['usage']}")
    print(f"      backend  {(body.get('diagnostics') or {}).get('backend')}")
    print(f"      noul     yes={noul['noul']:.4f}")
    print(f"      choice   {choice['choice']} -> " + ", ".join(
        f"{key}={value:.4f}" for key, value in probabilities.items()
    ))
    print(f"      score    {score['score']:.4f} -> " + ", ".join(
        f"{key}={value:.4f}" for key, value in score["probabilities"].items()
    ))
    truncated = [
        qid
        for qid, item in ((body.get("diagnostics") or {}).get("questions") or {}).items()
        if item.get("truncated")
    ]
    if truncated:
        print(f"warn  truncated candidates in: {', '.join(sorted(truncated))}")

    if args.json:
        args.json.write_text(json.dumps(body, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"ok    wrote {args.json}")

    print("\nPASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
