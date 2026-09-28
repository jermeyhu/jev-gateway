#!/usr/bin/env python3
"""Speed + output-token benchmark: gateway logprobs vs direct tool calling.

Uses the PRECISE rubric (numeric thresholds) that the diagnosis showed both
paths can answer.  Every case asks the same three decisions:

* ``is_incident``  noul   -> boolean
* ``severity``     choice -> sev1 | sev2 | sev3   (precise thresholds)
* ``action``       choice -> rollback | failover | investigate | wait

Paths, thinking disabled on both:

* gateway  ``POST /v1/systemone``        -- 3 x max_tokens=1 logprob passes
* direct   ``POST /v1/chat/completions`` -- 1 forced tool call (OpenAI tools)

Runs strictly single-threaded, ``--repeat`` full passes over the case set.

    python scripts/bench_speed_tokens.py --repeat 3 [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path
from typing import Any

import httpx

GATEWAY_URL = "http://127.0.0.1:8000"
BACKEND_URL = "http://127.0.0.1:8080"
TIMEOUT = 120.0

PRECISE_SEVERITY: dict[str, str] = {
    "sev1": (
        "error_rate is 0.20 or higher, OR a whole region is down, "
        "OR revenue is actively being lost"
    ),
    "sev2": (
        "error_rate is 0.05 or higher but below 0.20, OR a specific degradation "
        "with an identified cause such as memory climbing or slow database queries"
    ),
    "sev3": "error_rate is below 0.05 AND users are not visibly impacted",
}

PRECISE_ACTION: dict[str, str] = {
    "rollback": (
        "the most recent deploy is the likely cause "
        "(deploy within the last 2 hours AND a resource metric is climbing)"
    ),
    "failover": "an external dependency or region is down while this service is healthy",
    "investigate": "degradation exists but the cause is not yet identified",
    "wait": "no user-visible impact; keep monitoring",
}

QUESTIONS: dict[str, dict[str, Any]] = {
    "is_incident": {
        "type": "noul",
        "instructions": "Is the service currently in an active incident?",
        "criteria": {
            "true": "Active incident harming users right now",
            "false": "No active incident; metrics within normal range",
        },
    },
    "severity": {
        "type": "choice",
        "instructions": "Pick the incident severity.",
        "criteria": PRECISE_SEVERITY,
    },
    "action": {
        "type": "choice",
        "instructions": "Pick the single best next action.",
        "criteria": PRECISE_ACTION,
    },
}

TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "report_triage",
        "description": "Report the triage decision for the payment checkout service.",
        "parameters": {
            "type": "object",
            "properties": {
                "is_incident": {
                    "type": "boolean",
                    "description": (
                        "Is the service currently in an active incident? "
                        "true = Active incident harming users right now; "
                        "false = No active incident; metrics within normal range"
                    ),
                },
                "severity": {
                    "type": "string",
                    "enum": list(PRECISE_SEVERITY),
                    "description": "Pick the incident severity. "
                    + " ".join(f"{k} = {v}." for k, v in PRECISE_SEVERITY.items()),
                },
                "action": {
                    "type": "string",
                    "enum": list(PRECISE_ACTION),
                    "description": "Pick the single best next action. "
                    + " ".join(f"{k} = {v}." for k, v in PRECISE_ACTION.items()),
                },
            },
            "required": ["is_incident", "severity", "action"],
        },
    },
}

SYSTEM_PROMPT = (
    "You are an SRE triage assistant for a payment checkout service. "
    "Read the evidence and call report_triage exactly once with your final decision."
)

CASES: list[dict[str, Any]] = [
    {
        "id": "healthy_canary",
        "state": {
            "service": "checkout",
            "error_rate": 0.001,
            "p99_latency_ms": 180,
            "payment_success_rate": 0.999,
            "queue_depth": 12,
            "recent_deploy": "2h ago, canary fully promoted",
            "notes": "all alerts green, traffic at seasonal low",
        },
    },
    {
        "id": "provider_outage",
        "state": {
            "service": "checkout",
            "error_rate": 0.38,
            "p99_latency_ms": 8200,
            "payment_success_rate": 0.61,
            "payment_provider_status": "major_outage confirmed on provider status page",
            "notes": "errors are upstream timeouts to the payment provider",
        },
    },
    {
        "id": "memory_leak_after_deploy",
        "state": {
            "service": "checkout",
            "error_rate": 0.09,
            "p99_latency_ms": 1400,
            "payment_success_rate": 0.90,
            "memory_percent": 93,
            "memory_trend": "climbing 2% every 5 minutes since deploy",
            "recent_deploy": "40 minutes ago",
            "notes": "GC pauses growing, pods restarting on OOM",
        },
    },
    {
        "id": "db_slow_queries",
        "state": {
            "service": "checkout",
            "error_rate": 0.07,
            "p99_latency_ms": 2900,
            "payment_success_rate": 0.94,
            "db_cpu_percent": 98,
            "slow_queries_per_min": 340,
            "notes": "one query plan regressed after a stats refresh",
        },
    },
    {
        "id": "campaign_traffic_spike",
        "state": {
            "service": "checkout",
            "error_rate": 0.004,
            "p99_latency_ms": 420,
            "payment_success_rate": 0.996,
            "traffic_multiplier": 4.0,
            "autoscaling": "engaged, 3 new pods healthy",
            "notes": "marketing flash sale started 10 minutes ago",
        },
    },
    {
        "id": "region_failover_partial",
        "state": {
            "service": "checkout",
            "error_rate": 0.22,
            "p99_latency_ms": 5100,
            "payment_success_rate": 0.77,
            "eu_region": "all requests failing, failover partially engaged",
            "us_region": "healthy",
            "notes": "EU checkout fully down, revenue being lost in EU",
        },
    },
]


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * len(ordered)) - 1))
    return ordered[index]


def call_gateway(client: httpx.Client, case: dict[str, Any]) -> tuple[dict[str, Any], float]:
    payload = {"state": case["state"], "questions": QUESTIONS}
    started = time.perf_counter()
    response = client.post(GATEWAY_URL + "/v1/systemone", json=payload, timeout=TIMEOUT)
    elapsed = (time.perf_counter() - started) * 1000.0
    response.raise_for_status()
    return response.json(), elapsed


def call_direct(client: httpx.Client, case: dict[str, Any]) -> tuple[dict[str, Any], float]:
    body = {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps({"evidence": case["state"]}, ensure_ascii=False),
            },
        ],
        "tools": [TOOL],
        "tool_choice": {"type": "function", "function": {"name": "report_triage"}},
        "temperature": 0.0,
        "max_tokens": 256,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    started = time.perf_counter()
    response = client.post(BACKEND_URL + "/v1/chat/completions", json=body, timeout=TIMEOUT)
    elapsed = (time.perf_counter() - started) * 1000.0
    response.raise_for_status()
    return response.json(), elapsed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeat", type=int, default=3, help="full passes over the case set")
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()

    rows: list[dict[str, Any]] = []
    with httpx.Client() as client:
        # warm both prompt caches once so the timed runs measure steady state
        warm = CASES[0]
        call_gateway(client, warm)
        call_direct(client, warm)
        print("caches warmed\n")

        for run in range(max(1, args.repeat)):
            for case in CASES:
                gw_body, gw_ms = call_gateway(client, case)
                dr_body, dr_ms = call_direct(client, case)
                gw_usage = gw_body.get("usage") or {}
                dr_usage = dr_body.get("usage") or {}
                row = {
                    "run": run + 1,
                    "case": case["id"],
                    "gw_ms": gw_ms,
                    "dr_ms": dr_ms,
                    "gw_in": gw_usage.get("input_tokens", 0),
                    "gw_out": gw_usage.get("output_tokens", 0),
                    "dr_in": dr_usage.get("prompt_tokens", 0),
                    "dr_out": dr_usage.get("completion_tokens", 0),
                }
                rows.append(row)
                print(
                    f"run{row['run']} {row['case']:<26} "
                    f"gw {gw_ms:6.0f} ms (out {row['gw_out']:>3} tok)   "
                    f"dr {dr_ms:6.0f} ms (out {row['dr_out']:>3} tok)"
                )

    gw_ms_all = [r["gw_ms"] for r in rows]
    dr_ms_all = [r["dr_ms"] for r in rows]
    print("\n=== latency (single-threaded, per case = 3 decisions) ===")
    for name, values in (("gateway", gw_ms_all), ("direct ", dr_ms_all)):
        print(
            f"{name}  n={len(values):<3} p50={percentile(values, 0.5):6.0f} "
            f"p95={percentile(values, 0.95):6.0f} mean={statistics.fmean(values):6.0f} "
            f"min={min(values):6.0f} max={max(values):6.0f} ms"
        )
    print(
        f"speed ratio (direct / gateway): "
        f"{statistics.fmean(dr_ms_all) / statistics.fmean(gw_ms_all):.2f}x"
    )

    gw_out = [r["gw_out"] for r in rows]
    dr_out = [r["dr_out"] for r in rows]
    print("\n=== output tokens per case (3 decisions) ===")
    for name, values in (("gateway", gw_out), ("direct ", dr_out)):
        print(
            f"{name}  n={len(values):<3} p50={percentile(values, 0.5):5.0f} "
            f"mean={statistics.fmean(values):6.1f} min={min(values):4.0f} max={max(values):4.0f}"
        )
    print(
        f"output ratio (direct / gateway): "
        f"{statistics.fmean(dr_out) / statistics.fmean(gw_out):.1f}x"
    )

    gw_in_total = sum(r["gw_in"] for r in rows)
    dr_in_total = sum(r["dr_in"] for r in rows)
    print("\n=== input tokens (total over all runs) ===")
    print(f"gateway input: {gw_in_total}")
    print(f"direct  input: {dr_in_total}")

    if args.json:
        args.json.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
