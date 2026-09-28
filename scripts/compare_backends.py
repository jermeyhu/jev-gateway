#!/usr/bin/env python3
"""A/B two or more OpenAI-compatible inference servers running the same model.

    python scripts/compare_backends.py \
        --backend llama=http://127.0.0.1:8080 \
        --backend vllm=http://127.0.0.1:8001 \
        --repeat 3

Every target is spoken to over the same OpenAI chat-completions protocol; the
name is only a label for the report.

Reports, per question:

* argmax agreement between every pair of backends
* mean absolute probability difference (``noul`` questions)
* Brier distance, i.e. the squared gap between the two probability vectors,
  which is the metric that actually matters once you threshold on ``p``
* latency p50 / p95 as reported by the gateway

Use this to decide whether a quantisation (Q8_0 vs Q4_K_M) or a backend swap
kept the decision layer calibrated.  A large Brier distance with high argmax
agreement means the *ranking* survived but the *confidence* did not — exactly
the failure mode that breaks threshold based gating.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.backends import build_backend  # noqa: E402
from app.config import BackendSettings  # noqa: E402
from app.decision.engine import DecisionEngine  # noqa: E402
from app.primitives.base import to_decision_task  # noqa: E402

#: A small, opinionated routing set: cheap to run, and it exercises all three
#: primitives plus the noul/choice/score arithmetic end to end.
DEFAULT_CASES: list[dict[str, Any]] = [
    {
        "id": "healthy_deploy",
        "state": {
            "error_rate": 0.0,
            "p99_latency_ms": 210,
            "recent_deploy": True,
            "notes": "canary promoted, all checks green",
        },
        "question": {"type": "noul", "instructions": "Is the service healthy?"},
    },
    {
        "id": "degraded_deploy",
        "state": {
            "error_rate": 0.42,
            "p99_latency_ms": 3100,
            "recent_deploy": True,
            "notes": "timeouts after the canary promotion",
        },
        "question": {"type": "noul", "instructions": "Is the service healthy?"},
    },
    {
        "id": "severity",
        "state": {"error_rate": 0.42, "users_affected": "some", "revenue_lost": False},
        "question": {
            "type": "choice",
            "instructions": "Pick the incident severity.",
            "criteria": {
                "sev1": "Total outage, revenue lost",
                "sev2": "Degraded, some users affected",
                "sev3": "Minor, workaround exists",
            },
        },
    },
    {
        "id": "urgency",
        "state": {"users_affected": "many", "mitigation": "none"},
        "question": {
            "type": "score",
            "instructions": "How urgent is the response?",
            "criteria": ["can wait", "today", "right now"],
        },
    },
    {
        "id": "no_deploy",
        "state": {
            "error_rate": 0.39,
            "p99_latency_ms": 2900,
            "recent_deploy": False,
            "notes": "third party provider outage",
        },
        "question": {"type": "noul", "instructions": "Should we roll back?"},
    },
]


@dataclass
class Observation:
    backend: str
    case: str
    answer: dict[str, Any]
    latency_ms: float
    truncated: bool

    @property
    def key(self) -> Any:
        if self.answer["type"] == "noul":
            return round(self.answer["noul"], 9)
        if self.answer["type"] == "choice":
            return self.answer["choice"]
        return round(self.answer["score"], 9)

    def vector(self) -> list[float] | None:
        probabilities = self.answer.get("probabilities")
        if probabilities:
            return [float(value) for value in probabilities.values()]
        if self.answer["type"] == "noul":
            return [self.answer["noul"], 1.0 - self.answer["noul"]]
        return None


ALLOWED_OPTIONS = {"model", "api_key", "top_logprobs", "supports_images"}


def parse_backends(items: list[str]) -> list[tuple[str, str, dict[str, Any]]]:
    """Parse ``NAME=URL+key:value+key:value`` command line arguments."""
    parsed: list[tuple[str, str, dict[str, Any]]] = []
    for item in items:
        if "=" not in item:
            raise SystemExit(f"--backend expects name=url, got {item!r}")
        name, rest = item.split("=", 1)
        parts = rest.split("+")
        url = parts[0]
        if not url.startswith(("http://", "https://")):
            raise SystemExit(f"--backend url must be http(s), got {url!r}")
        extras: dict[str, Any] = {}
        for pair in parts[1:]:
            if ":" not in pair:
                raise SystemExit(f"--backend option must be key:value, got {pair!r}")
            key, value = pair.split(":", 1)
            if key not in ALLOWED_OPTIONS:
                raise SystemExit(
                    f"unknown --backend option {key!r}; allowed: "
                    f"{', '.join(sorted(ALLOWED_OPTIONS))}"
                )
            extras[key] = value
        parsed.append((name, url, extras))
    return parsed


async def run_backend(
    name: str, url: str, extras: dict[str, Any], cases: list[dict[str, Any]], repeat: int
) -> list[Observation]:
    options = dict(extras)
    unknown = set(options) - ALLOWED_OPTIONS
    if unknown:
        raise SystemExit(
            f"unknown backend options: {', '.join(sorted(unknown))}; "
            f"allowed: {', '.join(sorted(ALLOWED_OPTIONS))}"
        )
    settings = BackendSettings(
        type="openai",
        base_url=url,
        model=options.pop("model", None) or None,
        api_key=options.pop("api_key", None),
        top_logprobs=int(options.pop("top_logprobs", 128)),
        supports_images=str(options.pop("supports_images", "true")).lower() != "false",
        **options,
    )
    backend = build_backend(settings)
    engine = DecisionEngine(backend)
    observations: list[Observation] = []
    try:
        await backend.check_ready()
        for _ in range(repeat):
            for case in cases:
                task = to_decision_task(
                    case["id"], case["state"], case["question"], ()
                )
                started = time.perf_counter()
                result = await engine.decide(task)
                observations.append(
                    Observation(
                        backend=name,
                        case=case["id"],
                        answer=result.answer,
                        latency_ms=(time.perf_counter() - started) * 1000.0,
                        truncated=result.truncated,
                    )
                )
    finally:
        await backend.aclose()
    return observations


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))
    return ordered[index]


def report(observations: list[Observation], names: list[str]) -> None:
    by_case: dict[str, dict[str, Observation]] = {}
    for observation in observations:
        by_case.setdefault(observation.case, {}).setdefault(observation.backend, observation)

    print("\n=== per question ===")
    header = f"{'case':<18}{'backend':<22}{'answer':<14}{'latency p50':>12}{'trunc':>7}"
    print(header)
    print("-" * len(header))
    for case, per_backend in by_case.items():
        for name in names:
            observation = per_backend.get(name)
            if observation is None:
                continue
            key = observation.key
            key_text = f"{key:.4f}" if isinstance(key, float) else str(key)
            print(
                f"{case:<18}{name:<22}{key_text:<14}"
                f"{observation.latency_ms:>10.1f}ms{str(observation.truncated):>7}"
            )
        print()

    print("=== pairwise agreement ===")
    for index, left in enumerate(names):
        for right in names[index + 1 :]:
            same = 0
            mean_abs = 0.0
            brier = 0.0
            compared = 0
            for case in by_case:
                first = by_case[case].get(left)
                second = by_case[case].get(right)
                if first is None or second is None:
                    continue
                compared += 1
                same += int(first.key == second.key)
                left_vector, right_vector = first.vector(), second.vector()
                if left_vector and right_vector and len(left_vector) == len(right_vector):
                    mean_abs += sum(
                        abs(a - b) for a, b in zip(left_vector, right_vector, strict=True)
                    ) / len(left_vector)
                    brier += sum(
                        (a - b) ** 2 for a, b in zip(left_vector, right_vector, strict=True)
                    )
            if not compared:
                continue
            print(
                f"{left} vs {right}: argmax {same}/{compared}, "
                f"mean|dp| {mean_abs / compared:.4f}, "
                f"brier distance {brier / compared:.5f}"
            )

    print("\n=== latency ===")
    for name in names:
        values = [o.latency_ms for o in observations if o.backend == name]
        if not values:
            continue
        print(
            f"{name:<22} n={len(values):<4} p50={percentile(values, 0.5):7.1f}ms "
            f"p95={percentile(values, 0.95):7.1f}ms mean={statistics.fmean(values):7.1f}ms"
        )

    truncated = [o for o in observations if o.truncated]
    if truncated:
        cases = sorted({o.case for o in truncated})
        print(
            f"\nwarn  {len(truncated)} observation(s) had floored candidates: "
            f"{', '.join(cases)}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backend",
        action="append",
        required=True,
        metavar="NAME=URL[+key:value...]",
        help=(
            "repeat for each server, e.g. "
            "--backend llama=http://127.0.0.1:8080+top_logprobs:64"
        ),
    )
    parser.add_argument("--cases", type=Path, help="JSON file of {id, state, question} cases")
    parser.add_argument("--repeat", type=int, default=1)
    args = parser.parse_args()

    cases = (
        json.loads(args.cases.read_text(encoding="utf-8")) if args.cases else DEFAULT_CASES
    )

    targets = parse_backends(args.backend)
    results = asyncio.run(
        _gather(targets, cases, max(1, args.repeat))
    )
    names = [name for name, _, _ in targets]
    report(results, names)
    return 0


async def _gather(
    targets: list[tuple[str, str, dict[str, Any]]], cases: list[dict[str, Any]], repeat: int
) -> list[Observation]:
    runs = await asyncio.gather(
        *(run_backend(name, url, dict(extras), cases, repeat) for name, url, extras in targets),
        return_exceptions=True,
    )
    observations: list[Observation] = []
    for (name, url, _), run in zip(targets, runs, strict=True):
        if isinstance(run, BaseException):
            print(f"FAIL  {name} ({url}): {run}", file=sys.stderr)
            continue
        observations.extend(run)
    return observations


if __name__ == "__main__":
    raise SystemExit(main())
