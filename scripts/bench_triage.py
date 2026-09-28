#!/usr/bin/env python3
"""Benchmark: gateway logprob scoring vs direct tool-calling, same backend.

Scenario: SRE incident triage for a payment checkout service.  Six evidence
packs (monitoring snapshots) with ground-truth labels; three decisions per
case:

* ``is_incident``  noul   -> boolean
* ``severity``     choice -> sev1 | sev2 | sev3
* ``action``       choice -> rollback | failover | investigate | wait

Two paths over the same evidence and the same rubric, thinking disabled on
both (``chat_template_kwargs: {enable_thinking: false}``):

* gateway  ``POST /v1/systemone``        -- one max_tokens=1 logprob pass per question
* direct   ``POST /v1/chat/completions`` -- one forced tool call per case (OpenAI tools API)

Runs strictly single-threaded: cases execute one after another, no concurrency.

    python scripts/bench_triage.py [--repeat N] [--json out.json]
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
REQUEST_TIMEOUT = 120.0

QUESTION_IDS = ("is_incident", "severity", "action")

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
        "criteria": {
            "sev1": "Total outage or revenue actively being lost",
            "sev2": "Degraded service, a meaningful share of users affected",
            "sev3": "Minor issue, workaround exists",
        },
    },
    "action": {
        "type": "choice",
        "instructions": "Pick the single best next action.",
        "criteria": {
            "rollback": "Revert the most recent deploy",
            "failover": "Switch traffic to the backup provider or region",
            "investigate": "Gather more evidence before acting",
            "wait": "No action needed; keep monitoring",
        },
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
                    "enum": ["sev1", "sev2", "sev3"],
                    "description": (
                        "Pick the incident severity. "
                        "sev1 = Total outage or revenue actively being lost; "
                        "sev2 = Degraded service, a meaningful share of users affected; "
                        "sev3 = Minor issue, workaround exists"
                    ),
                },
                "action": {
                    "type": "string",
                    "enum": ["rollback", "failover", "investigate", "wait"],
                    "description": (
                        "Pick the single best next action. "
                        "rollback = Revert the most recent deploy; "
                        "failover = Switch traffic to the backup provider or region; "
                        "investigate = Gather more evidence before acting; "
                        "wait = No action needed; keep monitoring"
                    ),
                },
            },
            "required": list(QUESTION_IDS),
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
            "cpu_percent": 35,
            "memory_percent": 41,
            "recent_deploy": "2h ago, canary fully promoted",
            "payment_provider_status": "operational",
            "notes": "all alerts green, traffic at seasonal low",
        },
        "truth": {"is_incident": False, "severity": "sev3", "action": "wait"},
    },
    {
        "id": "provider_outage",
        "state": {
            "service": "checkout",
            "error_rate": 0.38,
            "p99_latency_ms": 8200,
            "payment_success_rate": 0.61,
            "queue_depth": 4500,
            "cpu_percent": 22,
            "memory_percent": 38,
            "recent_deploy": "none in the last 7 days",
            "payment_provider_status": "major_outage confirmed on provider status page",
            "notes": "errors are upstream timeouts to the payment provider",
        },
        "truth": {"is_incident": True, "severity": "sev1", "action": "failover"},
    },
    {
        "id": "memory_leak_after_deploy",
        "state": {
            "service": "checkout",
            "error_rate": 0.09,
            "p99_latency_ms": 1400,
            "payment_success_rate": 0.90,
            "queue_depth": 800,
            "cpu_percent": 55,
            "memory_percent": 93,
            "memory_trend": "climbing 2% every 5 minutes since deploy",
            "recent_deploy": "40 minutes ago",
            "payment_provider_status": "operational",
            "notes": "GC pauses growing, pods restarting on OOM",
        },
        "truth": {"is_incident": True, "severity": "sev2", "action": "rollback"},
    },
    {
        "id": "db_slow_queries",
        "state": {
            "service": "checkout",
            "error_rate": 0.05,
            "p99_latency_ms": 2900,
            "payment_success_rate": 0.94,
            "queue_depth": 1200,
            "cpu_percent": 60,
            "memory_percent": 62,
            "db_cpu_percent": 98,
            "slow_queries_per_min": 340,
            "recent_deploy": "none in the last 5 days",
            "payment_provider_status": "operational",
            "notes": "one query plan regressed after a stats refresh",
        },
        "truth": {"is_incident": True, "severity": "sev2", "action": "investigate"},
    },
    {
        "id": "campaign_traffic_spike",
        "state": {
            "service": "checkout",
            "error_rate": 0.004,
            "p99_latency_ms": 420,
            "payment_success_rate": 0.996,
            "queue_depth": 90,
            "cpu_percent": 58,
            "memory_percent": 55,
            "traffic_multiplier": 4.0,
            "autoscaling": "engaged, 3 new pods healthy",
            "recent_deploy": "none in the last 3 days",
            "payment_provider_status": "operational",
            "notes": "marketing flash sale started 10 minutes ago",
        },
        "truth": {"is_incident": False, "severity": "sev3", "action": "wait"},
    },
    {
        "id": "region_failover_partial",
        "state": {
            "service": "checkout",
            "error_rate": 0.22,
            "p99_latency_ms": 5100,
            "payment_success_rate": 0.77,
            "queue_depth": 2600,
            "eu_region": "all requests failing, failover partially engaged",
            "us_region": "healthy",
            "recent_deploy": "none in the last 6 days",
            "payment_provider_status": "operational",
            "notes": "EU checkout fully down, revenue being lost in EU",
        },
        "truth": {"is_incident": True, "severity": "sev1", "action": "failover"},
    },
]


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * len(ordered)) - 1))
    return ordered[index]


def as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1"}:
            return True
        if lowered in {"false", "no", "0"}:
            return False
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value in (0, 1):
        return bool(value)
    return None


def call_gateway(client: httpx.Client, case: dict[str, Any]) -> tuple[dict[str, Any], float]:
    payload = {"state": case["state"], "questions": QUESTIONS}
    started = time.perf_counter()
    response = client.post(GATEWAY_URL + "/v1/systemone", json=payload, timeout=REQUEST_TIMEOUT)
    elapsed = (time.perf_counter() - started) * 1000.0
    response.raise_for_status()
    return response.json(), elapsed


def call_direct(
    client: httpx.Client, case: dict[str, Any], tool_choice: Any
) -> tuple[dict[str, Any] | None, float, dict[str, Any]]:
    body = {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps({"evidence": case["state"]}, ensure_ascii=False),
            },
        ],
        "tools": [TOOL],
        "tool_choice": tool_choice,
        "temperature": 0.0,
        "max_tokens": 256,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    started = time.perf_counter()
    response = client.post(BACKEND_URL + "/v1/chat/completions", json=body, timeout=REQUEST_TIMEOUT)
    elapsed = (time.perf_counter() - started) * 1000.0
    response.raise_for_status()
    data = response.json()
    message = data["choices"][0]["message"]
    args: dict[str, Any] | None = None
    for call in message.get("tool_calls") or []:
        fn = call.get("function") or {}
        if fn.get("name") != "report_triage":
            continue
        raw = fn.get("arguments") or "{}"
        try:
            args = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except (TypeError, ValueError):
            args = None
        break
    return args, elapsed, data.get("usage") or {}


def run_case(client: httpx.Client, case: dict[str, Any], tool_choice: Any) -> dict[str, Any]:
    truth = case["truth"]
    gateway_body, gateway_ms = call_gateway(client, case)
    answers = gateway_body["answers"]
    direct_args, direct_ms, direct_usage = call_direct(client, case, tool_choice)

    gw_pred = {
        "is_incident": bool(answers["is_incident"]["noul"] >= 0.5),
        "severity": answers["severity"]["choice"],
        "action": answers["action"]["choice"],
    }
    if direct_args is None:
        dr_pred: dict[str, Any] = {q: None for q in QUESTION_IDS}
    else:
        dr_pred = {
            "is_incident": as_bool(direct_args.get("is_incident")),
            "severity": direct_args.get("severity"),
            "action": direct_args.get("action"),
        }

    gw_correct = {q: gw_pred[q] == truth[q] for q in QUESTION_IDS}
    dr_correct = {q: dr_pred[q] == truth[q] for q in QUESTION_IDS}
    agree = {q: gw_pred[q] == dr_pred[q] for q in QUESTION_IDS}
    noul_p = answers["is_incident"]["noul"]

    def fmt(pred: dict[str, Any]) -> str:
        return (
            f"is_incident={pred['is_incident']} severity={pred['severity']} "
            f"action={pred['action']}"
        )

    print(f"\n{case['id']}")
    print(
        f"  truth   : is_incident={truth['is_incident']} "
        f"severity={truth['severity']} action={truth['action']}"
    )
    print(
        f"  gateway : {fmt(gw_pred)} (p={noul_p:.3f})  "
        f"[{sum(gw_correct.values())}/3]  {gateway_ms:7.0f} ms"
    )
    mark = "" if direct_args is not None else "  <no parseable tool call>"
    print(
        f"  direct  : {fmt(dr_pred)}  [{sum(dr_correct.values())}/3]  "
        f"{direct_ms:7.0f} ms{mark}"
    )

    return {
        "case": case["id"],
        "truth": truth,
        "gw_pred": gw_pred,
        "dr_pred": dr_pred,
        "gw_correct": gw_correct,
        "dr_correct": dr_correct,
        "agree": agree,
        "gw_ms": gateway_ms,
        "dr_ms": direct_ms,
        "noul_p": noul_p,
        "gw_usage": gateway_body.get("usage") or {},
        "dr_usage": direct_usage,
        "dr_parsed": direct_args is not None,
    }


def resolve_tool_choice(client: httpx.Client) -> Any:
    """Find a tool_choice mode this llama.cpp accepts; warm the prompt cache too."""
    candidates: list[Any] = [
        {"type": "function", "function": {"name": "report_triage"}},
        "required",
        "auto",
    ]
    base = {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps({"evidence": {"warmup": True}})},
        ],
        "tools": [TOOL],
        "temperature": 0.0,
        "max_tokens": 64,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    for choice in candidates:
        label = "named(report_triage)" if isinstance(choice, dict) else choice
        response = client.post(
            BACKEND_URL + "/v1/chat/completions", json=dict(base, tool_choice=choice),
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code == 200:
            print(f"direct path ready, tool_choice = {label}")
            return choice
        print(f"tool_choice {label} rejected ({response.status_code}), trying next")
    raise SystemExit("backend rejected every tool_choice mode")


def warm_gateway(client: httpx.Client) -> None:
    response = client.post(
        GATEWAY_URL + "/v1/systemone",
        json={
            "state": {"warmup": True},
            "questions": {"w": {"type": "noul", "instructions": "warmup"}},
        },
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    print("gateway path ready")


def report(rows: list[dict[str, Any]], json_path: Path | None) -> None:
    total = len(rows)
    print(f"\n=== accuracy over {total} scored cases ===")
    print(f"{'question':<14}{'gateway':>12}{'direct':>12}{'agree':>12}")
    print("-" * 50)
    for q in QUESTION_IDS:
        gw_ok = sum(1 for r in rows if r["gw_correct"][q])
        dr_ok = sum(1 for r in rows if r["dr_correct"][q])
        ag = sum(1 for r in rows if r["agree"][q])
        print(f"{q:<14}{f'{gw_ok}/{total}':>12}{f'{dr_ok}/{total}':>12}{f'{ag}/{total}':>12}")
    gw_all = sum(1 for r in rows if all(r["gw_correct"].values()))
    dr_all = sum(1 for r in rows if all(r["dr_correct"].values()))
    print(f"{'all three':<14}{f'{gw_all}/{total}':>12}{f'{dr_all}/{total}':>12}")

    print("\n=== latency (single-threaded, per case) ===")
    series = (("gateway", [r["gw_ms"] for r in rows]), ("direct", [r["dr_ms"] for r in rows]))
    for name, values in series:
        print(
            f"{name:<10} n={len(values):<3} p50={percentile(values, 0.5):6.0f} "
            f"p95={percentile(values, 0.95):6.0f} mean={statistics.fmean(values):6.0f} "
            f"total={sum(values):6.0f} ms"
        )

    gw_in = sum(r["gw_usage"].get("input_tokens", 0) for r in rows)
    gw_out = sum(r["gw_usage"].get("output_tokens", 0) for r in rows)
    dr_in = sum(r["dr_usage"].get("prompt_tokens", 0) for r in rows)
    dr_out = sum(r["dr_usage"].get("completion_tokens", 0) for r in rows)
    print("\n=== tokens ===")
    print(f"gateway: input {gw_in}, output {gw_out}")
    print(f"direct : prompt {dr_in}, completion {dr_out}")

    if json_path:
        json_path.write_text(
            json.dumps(rows, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
        )
        print(f"\nwrote {json_path}")


def main() -> int:
    global GATEWAY_URL, BACKEND_URL
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeat", type=int, default=1, help="times to run the full case set")
    parser.add_argument("--gateway-url", default=GATEWAY_URL)
    parser.add_argument("--backend-url", default=BACKEND_URL)
    parser.add_argument("--json", type=Path, default=None, help="write raw results here")
    args = parser.parse_args()
    GATEWAY_URL = args.gateway_url.rstrip("/")
    BACKEND_URL = args.backend_url.rstrip("/")

    with httpx.Client() as client:
        warm_gateway(client)
        tool_choice = resolve_tool_choice(client)
        rows: list[dict[str, Any]] = []
        for run in range(max(1, args.repeat)):
            if args.repeat > 1:
                print(f"\n########## run {run + 1}/{args.repeat} ##########")
            for case in CASES:
                rows.append(run_case(client, case, tool_choice))
        report(rows, args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
