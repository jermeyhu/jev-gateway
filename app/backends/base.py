"""Backend contract.

Every backend turns a chat prompt plus a set of candidate letters into the
next-token logprob of each letter.  The decision engine then re-normalises those
logprobs with a softmax restricted to the candidates, so no backend ever has to
sample or generate text.
"""

from __future__ import annotations

import abc
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

import httpx

from app.config import BackendSettings
from app.utils.errors import (
    BackendCapabilityUnsupported,
    BackendProtocolError,
    BackendTimeout,
    BackendUnavailable,
)
from app.utils.imaging import ImageRef
from app.utils.softmax import stable_softmax

if TYPE_CHECKING:  # avoids a cycle: decision.engine imports this module
    from app.decision.prompt import Message


@dataclass(frozen=True, slots=True)
class CandidateScores:
    logprobs: dict[str, float]
    truncated: bool
    input_tokens: int
    output_tokens: int

    @property
    def probabilities(self) -> dict[str, float]:
        keys = list(self.logprobs)
        values = stable_softmax([self.logprobs[key] for key in keys])
        return dict(zip(keys, values, strict=True))


class DecisionBackend(abc.ABC):
    """Async client for one inference server."""

    type: ClassVar[str] = "base"

    def __init__(self, settings: BackendSettings) -> None:
        self.settings = settings
        self._discovered_model = ""
        headers = {"content-type": "application/json"}
        if settings.api_key:
            headers["authorization"] = f"Bearer {settings.api_key}"
        headers.update({k.lower(): v for k, v in settings.extra_headers.items()})
        connect = min(10.0, settings.timeout_seconds)
        self._client = httpx.AsyncClient(
            base_url=settings.base_url,
            headers=headers,
            timeout=httpx.Timeout(settings.timeout_seconds, connect=connect),
        )

    @property
    def model(self) -> str:
        return self.settings.model or self._discovered_model

    @property
    def supports_images(self) -> bool:
        return self.settings.images_enabled

    def ensure_images_supported(self, images: Sequence[ImageRef]) -> None:
        if images and not self.supports_images:
            raise BackendCapabilityUnsupported(
                f"the {self.type} backend is configured without image support"
            )

    @abc.abstractmethod
    async def score_candidates(
        self,
        messages: list[Message],
        letters: Sequence[str],
        images: Sequence[ImageRef] = (),
    ) -> CandidateScores:
        """Return the logprob of each candidate letter at the next token position."""

    @abc.abstractmethod
    async def check_ready(self) -> None:
        """Raise a GatewayError if the backend cannot serve a request right now."""

    async def aclose(self) -> None:
        await self._client.aclose()

    # -- HTTP helpers -----------------------------------------------------
    async def _get(self, path: str, **params: Any) -> httpx.Response:
        try:
            response = await self._client.get(path, params=params or None)
        except httpx.TimeoutException as exc:
            raise BackendTimeout(f"{self.type} backend timed out on {path}") from exc
        except httpx.HTTPError as exc:
            raise BackendUnavailable(f"{self.type} backend is unreachable: {exc}") from exc
        self._check_status(response, path)
        return response

    async def _post(self, path: str, payload: dict[str, Any]) -> httpx.Response:
        try:
            response = await self._client.post(path, json=payload)
        except httpx.TimeoutException as exc:
            raise BackendTimeout(f"{self.type} backend timed out on {path}") from exc
        except httpx.HTTPError as exc:
            raise BackendUnavailable(f"{self.type} backend is unreachable: {exc}") from exc
        self._check_status(response, path)
        return response

    def _check_status(self, response: httpx.Response, path: str) -> None:
        if response.status_code == 200:
            return
        snippet = response.text[:400]
        if response.status_code >= 500:
            raise BackendUnavailable(
                f"{self.type} backend returned {response.status_code} on {path}: {snippet}"
            )
        raise BackendProtocolError(
            f"{self.type} backend returned {response.status_code} on {path}: {snippet}"
        )

    def _json(self, response: httpx.Response, path: str) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise BackendProtocolError(
                f"{self.type} backend returned non-JSON data on {path}"
            ) from exc
        if not isinstance(payload, dict):
            raise BackendProtocolError(
                f"{self.type} backend returned {type(payload).__name__}, expected an object "
                f"on {path}"
            )
        return payload

    @staticmethod
    def _token_count(block: Any, key: str) -> int:
        """Read a token count from a usage/meta block without ever raising.

        Token accounting is pure reporting metadata -- it never feeds the
        softmax -- so a server that ships a malformed ``usage`` block (a bare
        string or list, a stringified count, ``null``, NaN) must not turn a
        perfectly usable answer into a 502.  Anything unparseable reads as 0.
        """
        if not isinstance(block, dict):
            return 0
        value = block.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            return 0
        try:
            return max(round(float(value)), 0)
        except (TypeError, ValueError, OverflowError):
            return 0

    async def _require_health(self) -> None:
        try:
            await self._get("/health")
        except BackendProtocolError:
            # Servers that only expose /v1/models still count as ready.
            await self._get("/v1/models")
        if not self._discovered_model:
            await self._discover_model()

    async def _discover_model(self) -> None:
        """Best-effort read of the backend's own model name.

        ``config.yaml`` documents ``model: null`` as "use the backend's own",
        so the name is picked up from ``/v1/models`` instead of being reported
        as ``unknown``.  A server that does not implement the endpoint is not an
        error: the name simply stays empty.
        """
        if self.settings.model:
            return
        try:
            body = self._json(await self._get("/v1/models"), "/v1/models")
        except (BackendProtocolError, BackendUnavailable):
            return
        entries = body.get("data") or body.get("models")
        if not isinstance(entries, list):
            return
        for entry in entries:
            if isinstance(entry, str):
                self._discovered_model = entry
                return
            if isinstance(entry, dict):
                name = entry.get("id") or entry.get("name") or entry.get("model")
                if isinstance(name, str) and name:
                    self._discovered_model = name
                    return
