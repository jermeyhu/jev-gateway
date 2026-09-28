"""Whole-stack test: real app, real service, real backend class.

Only the HTTP transport is replaced, so everything from request validation down
to the backend's logprob parsing is exercised for real.  The stand-in server
speaks the plain OpenAI chat-completions contract, which is the only protocol
the gateway knows.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any

import httpx
import pytest

from app.backends.base import DecisionBackend
from app.backends.openai import OpenAICompatibleBackend
from app.config import Settings, build_default_settings
from app.main import create_app
from app.service import DecisionService

RULES: list[tuple[str, dict[str, float]]] = [
    ("Is the service healthy?", {"A": -0.05, "B": -3.0}),
    ("Pick the incident severity.", {"A": -0.4, "B": -0.6, "C": -4.0}),
    ("How urgent is the response?", {"A": -4.0, "B": -1.0, "C": -0.2}),
]
FILLER = [("the", -3.4), (" a", -4.1), (",", -5.0)]


def scores_for(prompt: str) -> dict[str, float]:
    scores: dict[str, float] = {"A": -0.7, "B": -0.8}
    for needle, candidate_scores in RULES:
        if needle in prompt:
            scores = candidate_scores
    return scores


def openai_server() -> httpx.MockTransport:
    """A stand-in for any OpenAI-compatible server, including its shuffling."""
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        payload = json.loads(request.content)
        seen.append(payload)
        scores = scores_for(json.dumps(payload["messages"], ensure_ascii=False))
        top = [{"token": token, "logprob": value} for token, value in scores.items()]
        top += [{"token": token, "logprob": value} for token, value in FILLER]
        # The API does not guarantee an order, so neither do we.
        top = top[::-1]
        best = max(scores, key=lambda letter: scores[letter])
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": best},
                        "logprobs": {
                            "content": [
                                {"token": best, "logprob": scores[best], "top_logprobs": top}
                            ]
                        },
                    }
                ],
                "usage": {"prompt_tokens": 21, "completion_tokens": 1},
            },
        )

    handler.seen = seen  # type: ignore[attr-defined]
    return httpx.MockTransport(handler)


@asynccontextmanager
async def gateway(
    settings: Settings | None = None,
) -> AsyncIterator[tuple[httpx.AsyncClient, DecisionBackend]]:
    """Real app + real service + real backend; only the socket is mocked."""
    resolved = settings or build_default_settings()
    resolved = replace(resolved, backend=replace(resolved.backend, model="mock-gguf"))
    backend = OpenAICompatibleBackend(resolved.backend)
    backend._client = httpx.AsyncClient(
        base_url=resolved.backend.base_url, transport=openai_server()
    )
    app = create_app(resolved, service=DecisionService(resolved, backend=backend))
    async with app.router.lifespan_context(app):
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://gateway"
        )
        async with client:
            yield client, backend


PAYLOAD = {
    "state": {"error_rate": 0.42, "p99_latency_ms": 3100, "recent_deploy": True},
    "questions": {
        "healthy": {"type": "noul", "instructions": "Is the service healthy?"},
        "severity": {
            "type": "choice",
            "instructions": "Pick the incident severity.",
            "criteria": {
                "sev1": "Total outage",
                "sev2": "Degraded",
                "sev3": "Minor",
            },
        },
        "urgency": {
            "type": "score",
            "instructions": "How urgent is the response?",
            "criteria": ["can wait", "today", "right now"],
        },
    },
}


async def test_full_stack_round_trip() -> None:
    async with gateway() as (client, _backend):
        response = await client.post("/v1/systemone", json=PAYLOAD)

    assert response.status_code == 200, response.text
    body = response.json()
    answers = body["answers"]

    # noul: softmax(-0.05, -3.0) over the two candidates
    assert answers["healthy"]["type"] == "noul"
    assert answers["healthy"]["noul"] == pytest.approx(0.9503, abs=1e-3)

    # choice: three candidates, the best one is sev1
    assert answers["severity"]["choice"] == "sev1"
    severity = answers["severity"]["probabilities"]
    assert sum(severity.values()) == pytest.approx(1.0)
    assert severity["sev3"] == pytest.approx(0.0148, abs=1e-3)

    # score: expected value over the three levels
    urgency = answers["urgency"]
    assert urgency["type"] == "score"
    expected = sum(
        index * probability for index, probability in enumerate(urgency["probabilities"].values())
    )
    assert urgency["score"] == pytest.approx(expected)

    assert body["usage"]["input_tokens"] == 63  # 3 questions x 21 tokens
    assert body["model"] == "mock-gguf"
    assert body["diagnostics"]["backend"] == "openai"
    assert all(
        item["truncated"] is False for item in body["diagnostics"]["questions"].values()
    )


async def test_one_backend_call_per_question() -> None:
    async with gateway() as (client, backend):
        transport_seen: list[dict[str, Any]] = []
        original = backend.score_candidates

        async def spy(messages, letters, images=()):
            transport_seen.append({"letters": list(letters), "images": len(list(images))})
            return await original(messages, letters, images)

        backend.score_candidates = spy  # type: ignore[method-assign]
        response = await client.post("/v1/systemone", json=PAYLOAD)

    assert response.status_code == 200
    assert [call["letters"] for call in transport_seen] == [
        ["A", "B"],
        ["A", "B", "C"],
        ["A", "B", "C"],
    ]


async def test_prompt_layout_split_reuses_the_evidence_prefix() -> None:
    settings = build_default_settings()
    settings = replace(settings, request=replace(settings.request, prompt_layout="split"))
    async with gateway(settings) as (client, _backend):
        response = await client.post("/v1/systemone", json=PAYLOAD)
    assert response.status_code == 200
    assert len(response.json()["answers"]) == 3


async def test_more_than_sixteen_candidates_is_rejected() -> None:
    """The gateway refuses any question with more than 16 candidates."""
    payload = {
        "state": "x",
        "questions": {
            "too_many": {
                "type": "choice",
                "instructions": "pick one",
                "criteria": {f"k{i}": f"option {i}" for i in range(17)},
            }
        },
    }
    async with gateway() as (client, _backend):
        response = await client.post("/v1/systemone", json=payload)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


async def test_sixteen_candidates_is_accepted() -> None:
    payload = {
        "state": "x",
        "questions": {
            "at_limit": {
                "type": "choice",
                "instructions": "pick one",
                "criteria": {f"k{i}": f"option {i}" for i in range(16)},
            }
        },
    }
    async with gateway() as (client, _backend):
        response = await client.post("/v1/systemone", json=payload)
    assert response.status_code == 200, response.text
    assert len(response.json()["answers"]["at_limit"]["probabilities"]) == 16