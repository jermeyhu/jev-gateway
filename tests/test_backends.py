"""Backend protocol tests: the gateway is only as good as its logprob parsing.

There is exactly one backend -- the generic OpenAI-compatible client -- so these
tests pin down the one wire shape it must understand.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.backends.base import DecisionBackend
from app.backends.openai import OpenAICompatibleBackend, normalise_letter
from app.config import BackendSettings

MESSAGES = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]


def make(handler: Callable[[httpx.Request], httpx.Response], **overrides):
    settings = BackendSettings(
        type="openai",
        base_url="http://backend",
        model="test-model",
        **overrides,
    )
    backend = OpenAICompatibleBackend(settings)
    backend._client = httpx.AsyncClient(
        base_url="http://backend",
        transport=httpx.MockTransport(handler),
    )
    return backend


def chat_body(top: list[tuple[str, float]]) -> dict[str, Any]:
    return {
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "A"},
                "logprobs": {
                    "content": [
                        {
                            "token": "A",
                            "logprob": top[0][1],
                            "bytes": [65],
                            "top_logprobs": [
                                {"token": token, "logprob": value} for token, value in top
                            ],
                        }
                    ]
                },
            }
        ],
        "usage": {"prompt_tokens": 42, "completion_tokens": 1},
    }


async def test_reads_candidate_logprobs() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=chat_body([("A", -0.1), ("B", -2.0), ("the", -3.0)]))

    backend = make(handler)
    result = await backend.score_candidates(MESSAGES, ["A", "B"])
    assert result.logprobs == {"A": -0.1, "B": -2.0}
    assert result.truncated is False
    assert result.input_tokens == 42

    payload = json.loads(seen[0].content)
    assert payload["logprobs"] is True
    assert payload["top_logprobs"] == 128
    assert payload["max_tokens"] == 1
    assert payload["stream"] is False
    # Nothing engine-specific may leak onto the wire.
    assert "chat_template_kwargs" not in payload
    assert "logprob_token_ids" not in payload
    assert "cache_prompt" not in payload


async def test_extra_body_is_merged_into_the_payload() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=chat_body([("A", -0.1), ("B", -2.0)]))

    backend = make(
        handler,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    await backend.score_candidates(MESSAGES, ["A", "B"])

    payload = json.loads(seen[0].content)
    assert payload["chat_template_kwargs"] == {"enable_thinking": False}
    # The contract fields are still present and unchanged.
    assert payload["logprobs"] is True
    assert payload["max_tokens"] == 1


async def test_extra_body_cannot_override_contract_fields() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=chat_body([("A", -0.1), ("B", -2.0)]))

    backend = make(
        handler,
        extra_body={"logprobs": False, "max_tokens": 99, "top_logprobs": 1},
    )
    await backend.score_candidates(MESSAGES, ["A", "B"])

    payload = json.loads(seen[0].content)
    assert payload["logprobs"] is True
    assert payload["max_tokens"] == 1
    assert payload["top_logprobs"] == 128


async def test_floors_and_flags_missing_candidates() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=chat_body([("A", -0.1), ("B", -1.0), ("C", -7.5), ("D", -9.0)]),
        )

    backend = make(handler)
    result = await backend.score_candidates(MESSAGES, ["A", "B", "Z"])
    assert result.logprobs["Z"] == -9.0
    assert result.truncated is True
    probabilities = result.probabilities
    assert probabilities["Z"] < 0.001


async def test_keeps_the_best_variant_of_a_letter() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=chat_body([("A", -0.2), (" A", -0.05), ("B", -4.0)]))

    backend = make(handler)
    result = await backend.score_candidates(MESSAGES, ["A", "B"])
    assert result.logprobs["A"] == -0.05


async def test_without_logprobs_is_a_protocol_error() -> None:
    from app.utils.errors import BackendProtocolError

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "A"}}]})

    backend = make(handler)
    with pytest.raises(BackendProtocolError, match="logprobs"):
        await backend.score_candidates(MESSAGES, ["A"])


async def test_upstream_error_becomes_backend_unavailable() -> None:
    from app.utils.errors import BackendUnavailable

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="model loading")

    backend = make(handler)
    with pytest.raises(BackendUnavailable, match="503"):
        await backend.score_candidates(MESSAGES, ["A"])


async def test_rejects_images_when_disabled() -> None:
    from app.utils.errors import BackendCapabilityUnsupported

    from .conftest import IMAGE

    backend = make(
        lambda request: httpx.Response(200, json={}),
        supports_images=False,
    )
    with pytest.raises(BackendCapabilityUnsupported):
        await backend.score_candidates(MESSAGES, ["A"], [IMAGE])


def test_normalise_letter() -> None:
    assert normalise_letter("A") == "A"
    assert normalise_letter(" a") == "A"
    assert normalise_letter("AB") is None
    assert normalise_letter("the") is None
    assert normalise_letter("。") is None
    assert normalise_letter(65) is None


async def test_health_check_uses_health_endpoint() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, json={"status": "ok"})

    backend = make(handler)
    await backend.check_ready()
    assert paths == ["/health"]


async def test_health_check_falls_back_to_models() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(404, text="not found")
        return httpx.Response(200, json={"data": []})

    backend = make(handler)
    await backend.check_ready()


async def test_connection_failure_is_backend_unavailable() -> None:
    from app.utils.errors import BackendUnavailable

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    backend = make(handler)
    with pytest.raises(BackendUnavailable):
        await backend.check_ready()


# --- usage robustness ----------------------------------------------------
# Token accounting is reporting metadata only; it never feeds the softmax, so a
# malformed block must not turn a usable answer into a 502.


@pytest.mark.parametrize(
    "usage",
    [
        "a bare string",
        [1, 2, 3],
        None,
        {"prompt_tokens": "not a number"},
        {"prompt_tokens": None},
        {"prompt_tokens": [7]},
        {"prompt_tokens": {"nested": 1}},
        {"prompt_tokens": True},
        {"prompt_tokens": -5},
        {"completion_tokens": "nope"},
    ],
    ids=[
        "str",
        "list",
        "null",
        "str-count",
        "null-count",
        "list-count",
        "dict-count",
        "bool-count",
        "negative",
        "bad-completion",
    ],
)
async def test_malformed_usage_never_raises(usage: Any) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = chat_body([("A", -0.1), ("B", -1.0)])
        body["usage"] = usage
        return httpx.Response(200, json=body)

    backend = make(handler)
    scores = await backend.score_candidates(MESSAGES, ["A", "B"])
    assert set(scores.logprobs) == {"A", "B"}
    assert scores.input_tokens >= 0
    assert scores.output_tokens >= 0


@pytest.mark.parametrize(
    ("value", "expected"),
    [(21, 21), ("21", 21), (21.4, 21), (0, 0), (-3, 0), (True, 0)],
)
def test_token_count_normalises_sensibly(value: Any, expected: int) -> None:
    assert DecisionBackend._token_count({"prompt_tokens": value}, "prompt_tokens") == expected


@pytest.mark.parametrize("value", [None, "x", [], {}, float("nan"), float("inf")])
def test_token_count_never_raises(value: Any) -> None:
    assert DecisionBackend._token_count({"prompt_tokens": value}, "prompt_tokens") == 0


def test_token_count_tolerates_a_non_dict_block() -> None:
    assert DecisionBackend._token_count("garbage", "prompt_tokens") == 0
    assert DecisionBackend._token_count(None, "prompt_tokens") == 0