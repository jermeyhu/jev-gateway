from __future__ import annotations

import gzip
import json
import math
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any

import httpx
import pytest
import zstandard

from app.backends.base import GeneratedJSON
from app.config import Settings, build_default_settings
from app.main import create_app
from app.service import DecisionService
from app.utils.errors import BackendUnavailable

from .conftest import PNG, FakeBackend


def service(backend: FakeBackend, **request_overrides: Any) -> DecisionService:
    settings: Settings = build_default_settings()
    if request_overrides:
        settings = replace(settings, request=replace(settings.request, **request_overrides))
    return DecisionService(settings, backend=backend)


@asynccontextmanager
async def client_for(
    backend: FakeBackend, **request_overrides: Any
) -> AsyncIterator[httpx.AsyncClient]:
    instance = service(backend, **request_overrides)
    app = create_app(build_default_settings(), service=instance)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://gateway") as client:
            yield client


def noul_payload(**extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": "fake-model",
        "state": "the deployment finished without errors",
        "questions": {
            "did_it_work": {"type": "noul", "instructions": "Did the task succeed?"}
        },
    }
    payload.update(extra)
    return payload


async def test_noul_request_round_trip() -> None:
    backend = FakeBackend(default={"A": math.log(0.82), "B": math.log(0.18)})
    async with client_for(backend) as client:
        response = await client.post("/v1/systemone", json=noul_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["model"] == "fake-model"
    assert body["answers"]["did_it_work"] == {"type": "noul", "noul": pytest.approx(0.82)}
    assert body["usage"] == {"input_tokens": 11, "output_tokens": 1}
    assert set(body) == {"model", "answers", "usage"}


@pytest.mark.parametrize(
    ("encoding", "compress"),
    [
        ("gzip", gzip.compress),
        ("zstd", zstandard.ZstdCompressor().compress),
    ],
)
async def test_compressed_json_requests_are_supported(encoding, compress) -> None:
    backend = FakeBackend(default={"A": 0.0, "B": -1.0})
    body = json.dumps(noul_payload()).encode("utf-8")
    async with client_for(backend) as client:
        response = await client.post(
            "/v1/systemone",
            content=compress(body),
            headers={
                "content-type": "application/json",
                "content-encoding": encoding,
            },
        )
    assert response.status_code == 200


async def test_unsupported_content_encoding_returns_415() -> None:
    backend = FakeBackend(default={"A": 0.0, "B": -1.0})
    async with client_for(backend) as client:
        response = await client.post(
            "/v1/systemone",
            content=b"{}",
            headers={"content-encoding": "br"},
        )
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"


async def test_malformed_compressed_body_returns_400() -> None:
    backend = FakeBackend(default={"A": 0.0, "B": -1.0})
    async with client_for(backend) as client:
        response = await client.post(
            "/v1/systemone",
            content=b"not-gzip",
            headers={"content-encoding": "gzip"},
        )
    assert response.status_code == 400


async def test_request_body_over_limit_returns_413() -> None:
    backend = FakeBackend(default={"A": 0.0, "B": -1.0})
    async with client_for(backend) as client:
        response = await client.post(
            "/v1/systemone",
            content=b"{}",
            headers={"content-length": str(32 * 1024 * 1024 + 1)},
        )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"
    assert response.headers["x-request-id"]


async def test_request_id_is_echoed() -> None:
    backend = FakeBackend(default={"A": 0.0, "B": -1.0})
    async with client_for(backend) as client:
        response = await client.post(
            "/v1/systemone", json=noul_payload(), headers={"x-request-id": "abc123"}
        )
    assert response.headers["x-request-id"] == "abc123"


async def test_choice_and_score_in_one_request() -> None:
    backend = FakeBackend(
        {
            "pick a severity": {"A": -0.1, "B": -3.0},
            "rate the urgency": {"A": -2.0, "B": -0.2, "C": -1.0},
        }
    )
    payload = {
        "model": "fake-model",
        "state": "disk 95% full on prod-1",
        "questions": {
            "sev": {
                "type": "choice",
                "instructions": "pick a severity",
                "criteria": {"low": "nothing to do", "high": "page someone"},
            },
            "urgency": {
                "type": "score",
                "instructions": "rate the urgency",
                "criteria": ["whenever", "today", "now"],
            },
        },
    }
    async with client_for(backend) as client:
        response = await client.post("/v1/systemone", json=payload)

    answers = response.json()["answers"]
    assert answers["sev"]["choice"] == "low"
    assert answers["urgency"]["type"] == "score"
    assert answers["urgency"]["legend"] == {
        "0": "whenever",
        "1": "today",
        "2": "now",
    }
    assert answers["urgency"]["score"] == pytest.approx(
        0 * answers["urgency"]["probabilities"]["0"]
        + 1 * answers["urgency"]["probabilities"]["1"]
        + 2 * answers["urgency"]["probabilities"]["2"]
    )


async def test_images_reach_the_backend() -> None:
    backend = FakeBackend(default={"A": 0.0, "B": -1.0})
    async with client_for(backend) as client:
        response = await client.post(
            "/v1/systemone",
            json=noul_payload(
                state={"photo": "photo-ref"},
                assets={
                    "photo-ref": {"type": "image", "format": "png", "data_base64": PNG}
                },
            ),
            headers={"TypeSafe-Danger-Zone-Images": "true"},
        )
    assert response.status_code == 200
    assert len(backend.calls[0][2]) == 1
    content = backend.calls[0][0][1]["content"]
    assert isinstance(content, list)
    assert any(part["type"] == "image_url" for part in content)


async def test_multipart_asset_upload_matches_reference_contract() -> None:
    backend = FakeBackend(default={"A": 0.0, "B": -1.0})
    payload = {
        "model": "fake-model",
        "state": {"photo": "photo-ref"},
        "assets": {"photo-ref": {"type": "image", "part": "photo"}},
        "questions": {
            "visible": {"type": "noul", "instructions": "Is `photo` visible?"}
        },
    }
    async with client_for(backend) as client:
        response = await client.post(
            "/v1/systemone",
            files=[
                ("request", (None, json.dumps(payload), "application/json")),
                (
                    "photo",
                    ("frame.png", bytes.fromhex("89504e470d0a1a0a") + b"0" * 16, "image/png"),
                ),
            ],
            headers={"TypeSafe-Danger-Zone-Images": "true"},
        )
    assert response.status_code == 200, response.text
    assert len(backend.calls[0][2]) == 1


async def test_multimodal_can_be_disabled_by_config() -> None:
    backend = FakeBackend(default={"A": 0.0, "B": -1.0})
    settings = build_default_settings()
    settings = replace(
        settings, multimodal=replace(settings.multimodal, enabled=False)
    )
    instance = DecisionService(settings, backend=backend)
    app = create_app(settings, service=instance)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://gateway") as client:
            response = await client.post(
                "/v1/systemone",
                json=noul_payload(
                    state={"photo": "photo-ref"},
                    assets={
                        "photo-ref": {
                            "type": "image",
                            "format": "png",
                            "data_base64": PNG,
                        }
                    },
                ),
                headers={"TypeSafe-Danger-Zone-Images": "true"},
            )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


async def test_question_order_is_preserved() -> None:
    backend = FakeBackend(default={"A": -0.5, "B": -0.6})
    payload = noul_payload(
        questions={
            "first": {"type": "noul", "instructions": "a"},
            "second": {"type": "noul", "instructions": "b"},
            "third": {"type": "noul", "instructions": "c"},
        }
    )
    async with client_for(backend) as client:
        response = await client.post("/v1/systemone", json=payload)
    assert list(response.json()["answers"]) == ["first", "second", "third"]
    assert response.json()["usage"]["input_tokens"] == 33


async def test_backend_error_fails_the_whole_request() -> None:
    backend = FakeBackend(default={"A": 0.0, "B": -1.0})
    backend.error = BackendUnavailable("llama-server is restarting")
    async with client_for(backend) as client:
        response = await client.post("/v1/systemone", json=noul_payload())
    assert response.status_code == 502
    body = response.json()["error"]
    assert body["code"] == "BACKEND_UNAVAILABLE"
    assert "restarting" in body["message"]


async def test_one_failing_question_fails_everything() -> None:
    backend = FakeBackend({"good question": {"A": -0.1, "B": -2.0}})
    backend.error = None
    payload = {
        "model": "fake-model",
        "state": "x",
        "questions": {
            "ok": {"type": "noul", "instructions": "good question"},
            "bad": {"type": "noul", "instructions": "other question"},
        },
    }
    original = backend.score_candidates

    async def flaky(messages, letters, images=(), model=None):
        if "other question" in str(messages):
            backend.error = BackendUnavailable("boom")
        return await original(messages, letters, images, model)

    backend.score_candidates = flaky  # type: ignore[method-assign]
    async with client_for(backend) as client:
        response = await client.post("/v1/systemone", json=payload)
    assert response.status_code == 502
    assert "answers" not in response.json()


async def test_validation_error_shape() -> None:
    backend = FakeBackend(default={"A": 0.0, "B": -1.0})
    async with client_for(backend) as client:
        response = await client.post("/v1/systemone", json={"state": "x"})
    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "INVALID_REQUEST"
    assert body["detail"]
    assert "ctx" not in body["detail"][0]


async def test_image_asset_must_be_referenced() -> None:
    backend = FakeBackend(default={"A": 0.0, "B": -1.0})
    async with client_for(backend) as client:
        response = await client.post(
            "/v1/systemone",
            json=noul_payload(
                assets={
                    "unused": {"type": "image", "format": "png", "data_base64": PNG}
                }
            ),
            headers={"TypeSafe-Danger-Zone-Images": "true"},
        )
    assert response.status_code == 400
    assert "unreferenced image assets" in response.json()["error"]["message"]


async def test_images_require_opt_in_header() -> None:
    backend = FakeBackend(default={"A": 0.0, "B": -1.0})
    async with client_for(backend) as client:
        response = await client.post(
            "/v1/systemone",
            json=noul_payload(
                state={"photo": "photo-ref"},
                assets={
                    "photo-ref": {"type": "image", "format": "png", "data_base64": PNG}
                },
            ),
        )
    assert response.status_code == 400
    assert "TypeSafe-Danger-Zone-Images" in response.json()["error"]["message"]


async def test_box_answer_uses_normalized_edges() -> None:
    backend = FakeBackend(default={"A": 0.0, "B": -1.0})

    async def generate_json(messages, images=(), model=None):
        return GeneratedJSON(
            value={
                "box": {"top": 0.2, "bottom": 0.8, "left": 0.1, "right": 0.9},
                "confidence": 0.92,
            },
            input_tokens=8,
            output_tokens=4,
            model="jev-1.14.0",
        )

    backend.generate_json = generate_json  # type: ignore[method-assign]
    payload = noul_payload(
        state={"photo": "photo-ref"},
        assets={"photo-ref": {"type": "image", "format": "png", "data_base64": PNG}},
        questions={"region": {"type": "box", "instructions": "the object in `photo`"}},
    )
    async with client_for(backend) as client:
        response = await client.post(
            "/v1/systemone",
            json=payload,
            headers={"TypeSafe-Danger-Zone-Images": "true"},
        )
    assert response.status_code == 200
    assert response.json()["answers"]["region"] == {
        "type": "box",
        "box": {"top": 0.2, "bottom": 0.8, "left": 0.1, "right": 0.9},
        "confidence": 0.92,
    }
    assert response.json()["model"] == "jev-1.14.0"


async def test_advanced_json_instructions_and_criteria_are_preserved() -> None:
    backend = FakeBackend(default={"A": 0.0, "B": -1.0})
    payload = noul_payload(
        state={"case": {"status": "open"}},
        questions={
            "route": {
                "type": "choice",
                "instructions": {"question": "Which queue?", "context": ["urgent"]},
                "criteria": {
                    "billing": {"team": "payments"},
                    "general": None,
                },
            }
        },
    )
    async with client_for(backend) as client:
        response = await client.post("/v1/systemone", json=payload)
    assert response.status_code == 200
    prompt = str(backend.calls[0][0])
    assert '"question":"Which queue?"' in prompt
    assert '"team":"payments"' in prompt


async def test_empty_state_is_rejected() -> None:
    backend = FakeBackend(default={"A": 0.0, "B": -1.0})
    async with client_for(backend) as client:
        response = await client.post("/v1/systemone", json=noul_payload(state=""))
    assert response.status_code == 422
    assert "state" in json.dumps(response.json())


async def test_max_questions_is_enforced_from_config() -> None:
    backend = FakeBackend(default={"A": 0.0, "B": -1.0})
    questions = {
        f"q{i}": {"type": "noul", "instructions": f"q{i}"} for i in range(4)
    }
    async with client_for(backend, max_questions=2) as client:
        response = await client.post(
            "/v1/systemone",
            json={"model": "fake-model", "state": "x", "questions": questions},
        )
    assert response.status_code == 400
    assert "too many questions" in response.json()["error"]["message"]


async def test_total_timeout_is_enforced() -> None:
    backend = FakeBackend(default={"A": 0.0, "B": -1.0})
    backend.latency = 0.5
    async with client_for(backend, total_timeout_seconds=0.05) as client:
        response = await client.post("/v1/systemone", json=noul_payload())
    assert response.status_code == 504
    assert response.json()["error"]["code"] == "REQUEST_TIMEOUT"


async def test_health_and_models_endpoints() -> None:
    backend = FakeBackend(default={"A": 0.0, "B": -1.0})
    async with client_for(backend) as client:
        health = await client.get("/healthz")
        models = await client.get("/v1/models")
        ready = await client.get("/readyz")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert models.json()["data"][0]["id"] == "fake-model"
    assert ready.status_code == 200


def test_openapi_exposes_json_and_multipart_contracts() -> None:
    app = create_app()
    schema = app.openapi()
    request_body = schema["paths"]["/v1/systemone"]["post"]["requestBody"]
    json_schema = request_body["content"]["application/json"]["schema"]
    assert json_schema["$ref"] == "#/components/schemas/SystemOneRequest"
    assert "SystemOneRequest" in schema["components"]["schemas"]
    assert "multipart/form-data" in request_body["content"]
    questions = schema["components"]["schemas"]["Questions"]
    assert "box" in str(questions)


async def test_readyz_reports_backend_down() -> None:
    backend = FakeBackend(default={"A": 0.0, "B": -1.0})
    backend.ready = False
    async with client_for(backend) as client:
        response = await client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"


async def test_backend_is_closed_on_shutdown() -> None:
    backend = FakeBackend(default={"A": 0.0, "B": -1.0})
    async with client_for(backend):
        pass
    assert backend.closed is True
