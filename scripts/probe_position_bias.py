"""One-shot diagnostic: is the gateway's severity answer positional bias?

Same evidence, same rubric, but the candidate order is shuffled.  If the answer
follows the *letter* (always A) instead of the *label*, the logprob path is
reading position, not meaning.
"""

from __future__ import annotations

import httpx

STATE = {
    "service": "checkout",
    "error_rate": 0.38,
    "p99_latency_ms": 8200,
    "payment_success_rate": 0.61,
    "notes": "payment provider major outage confirmed",
}

RUBRIC = {
    "sev1": "Total outage or revenue actively being lost",
    "sev2": "Degraded service, a meaningful share of users affected",
    "sev3": "Minor issue, workaround exists",
}

ORDERS = [
    ("original A=sev1", ["sev1", "sev2", "sev3"]),
    ("reversed A=sev3", ["sev3", "sev2", "sev1"]),
    ("rotated A=sev2", ["sev2", "sev3", "sev1"]),
]


def main() -> None:
    with httpx.Client(timeout=60) as client:
        for label, keys in ORDERS:
            payload = {
                "state": STATE,
                "questions": {
                    "severity": {
                        "type": "choice",
                        "instructions": "Pick the incident severity.",
                        "criteria": {key: RUBRIC[key] for key in keys},
                    }
                },
            }
            body = client.post("http://127.0.0.1:8000/v1/systemone", json=payload).json()
            answer = body["answers"]["severity"]
            probabilities = answer["probabilities"]
            shown = "  ".join(f"{k}={probabilities[k]:.3f}" for k in keys)
            print(f"{label:<18} -> {answer['choice']:<5} [{shown}]")


if __name__ == "__main__":
    main()
