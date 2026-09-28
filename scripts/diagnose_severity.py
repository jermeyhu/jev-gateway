#!/usr/bin/env python3
"""Diagnose: is the gateway's low severity accuracy a design flaw or a vague rubric?

Runs the SAME severity question over the SAME evidence under two rubrics:

* vague   -- the original benchmark wording (no numeric thresholds)
* precise -- explicit numeric thresholds decidable from the state

across both paths (gateway logprob scoring, direct tool calling), plus two
trivial sanity cases whose answer is printed on the face of the evidence.

Reading the verdict:
  precise rubric fixes both paths   -> the original question was ambiguous
  precise rubric fixes direct only  -> gateway design issue
  precise rubric fixes neither      -> model too weak for the task
  trivial case fails on one path    -> that path has a design problem

Single-threaded, sequential.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

GATEWAY_URL = "http://127.0.0.1:8000"
BACKEND_URL = "http://127.0.0.1:8080"
TIMEOUT = 120.0

VAGUE: dict[str, str] = {
    "sev1": "Total outage or revenue actively being lost",
    "sev2": "Degraded service, a meaningful share of users affected",
    "sev3": "Minor issue, workaround exists",
}

PRECISE: dict[str, str] = {
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
        "truth": "sev3",
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
        "truth": "sev1",
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
        "truth": "sev2",
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
        "truth": "sev2",
    },
    {
        "id": "trivial_global_outage",
        "state": {
            "service": "checkout",
            "error_rate": 0.95,
            "p99_latency_ms": 30000,
            "payment_success_rate": 0.03,
            "notes": "every region failing, checkout fully down, revenue actively being lost",
        },
        "truth": "sev1",
    },
    {
        "id": "trivial_all_green",
        "state": {
            "service": "checkout",
            "error_rate": 0.0001,
            "p99_latency_ms": 95,
            "payment_success_rate": 0.9999,
            "notes": "every metric green, no deploy in days",
        },
        "truth": "sev3",
    },
]

INSTRUCTION = "Pick the incident severity."


def gateway_tool(rubric: dict[str, str]) -> dict[str, Any]:
    return {
        "type": "choice",
        "instructions": INSTRUCTION,
        "criteria": dict(rubric),
    }


def direct_tool(rubric: dict[str, str]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "set_severity",
            "description": "Report the incident severity for the evidence.",
            "parameters": {
                "type": "object",
                "properties": {
                    "severity": {
                        "type": "string",
                        "enum": list(rubric),
                        "description": " ".join(f"{k} = {v}." for k, v in rubric.items()),
                    }
                },
                "required": ["severity"],
            },
        },
    }


def call_gateway(
    client: httpx.Client, state: dict[str, Any], rubric: dict[str, str]
) -> tuple[str | None, dict[str, float], float]:
    payload = {
        "state": state,
        "questions": {"severity": gateway_tool(rubric)},
    }
    started = time.perf_counter()
    response = client.post(GATEWAY_URL + "/v1/systemone", json=payload, timeout=TIMEOUT)
    elapsed = (time.perf_counter() - started) * 1000.0
    response.raise_for_status()
    answer = response.json()["answers"]["severity"]
    return answer.get("choice"), answer.get("probabilities") or {}, elapsed


def call_direct(
    client: httpx.Client, state: dict[str, Any], rubric: dict[str, str]
) -> tuple[str | None, float]:
    body = {
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are an SRE triage assistant. Read the evidence and call "
                    "set_severity exactly once with your final decision."
                ),
            },
            {"role": "user", "content": json.dumps({"evidence": state}, ensure_ascii=False)},
        ],
        "tools": [direct_tool(rubric)],
        "tool_choice": {"type": "function", "function": {"name": "set_severity"}},
        "temperature": 0.0,
        "max_tokens": 64,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    started = time.perf_counter()
    response = client.post(BACKEND_URL + "/v1/chat/completions", json=body, timeout=TIMEOUT)
    elapsed = (time.perf_counter() - started) * 1000.0
    response.raise_for_status()
    message = response.json()["choices"][0]["message"]
    for call in message.get("tool_calls") or []:
        fn = call.get("function") or {}
        if fn.get("name") != "set_severity":
            continue
        raw = fn.get("arguments") or "{}"
        try:
            args = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except (TypeError, ValueError):
            return None, elapsed
        return args.get("severity"), elapsed
    return None, elapsed


def main() -> None:
    with httpx.Client() as client:
        rows: list[dict[str, Any]] = []
        for rubric_name, rubric in (("vague", VAGUE), ("precise", PRECISE)):
            print(f"\n########## rubric: {rubric_name} ##########")
            for case in CASES:
                gw_choice, gw_probs, gw_ms = call_gateway(client, case["state"], rubric)
                dr_choice, dr_ms = call_direct(client, case["state"], rubric)
                truth = case["truth"]
                gw_ok = gw_choice == truth
                dr_ok = dr_choice == truth
                spread = "  ".join(f"{k}={gw_probs.get(k, float('nan')):.3f}" for k in rubric)
                print(
                    f"{case['id']:<26} truth={truth}  "
                    f"gw={str(gw_choice):<5}({'OK ' if gw_ok else 'MISS'} {gw_ms:5.0f}ms)  "
                    f"dr={str(dr_choice):<5}({'OK ' if dr_ok else 'MISS'} {dr_ms:5.0f}ms)  "
                    f"[{spread}]"
                )
                rows.append(
                    {
                        "rubric": rubric_name,
                        "case": case["id"],
                        "truth": truth,
                        "gw": gw_choice,
                        "dr": dr_choice,
                        "gw_ok": gw_ok,
                        "dr_ok": dr_ok,
                        "gw_probs": gw_probs,
                        "gw_ms": gw_ms,
                        "dr_ms": dr_ms,
                    }
                )

        print("\n=== verdict ===")
        for rubric_name in ("vague", "precise"):
            subset = [r for r in rows if r["rubric"] == rubric_name]
            trivial = [r for r in subset if r["case"].startswith("trivial_")]
            real = [r for r in subset if not r["case"].startswith("trivial_")]
            gw_real = sum(r["gw_ok"] for r in real)
            dr_real = sum(r["dr_ok"] for r in real)
            gw_triv = sum(r["gw_ok"] for r in trivial)
            dr_triv = sum(r["dr_ok"] for r in trivial)
            print(
                f"{rubric_name:<8} real cases: gw {gw_real}/{len(real)}  dr {dr_real}/{len(real)}"
                f"   |   trivial: gw {gw_triv}/{len(trivial)}  dr {dr_triv}/{len(trivial)}"
            )


if __name__ == "__main__":
    main()
