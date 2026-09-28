from __future__ import annotations

from typing import Any


class GatewayError(Exception):
    """Base class for every error the gateway reports as structured JSON."""

    code = "INTERNAL_ERROR"
    status_code = 500

    def __init__(self, message: str, *, detail: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail

    def to_payload(self, request_id: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.detail is not None:
            payload["detail"] = self.detail
        if request_id is not None:
            payload["request_id"] = request_id
        return {"error": payload}


class InvalidRequest(GatewayError):
    code = "INVALID_REQUEST"
    status_code = 400


class BackendNotReady(GatewayError):
    code = "BACKEND_NOT_READY"
    status_code = 503


class BackendProtocolError(GatewayError):
    code = "BACKEND_PROTOCOL_ERROR"
    status_code = 502


class BackendUnavailable(GatewayError):
    code = "BACKEND_UNAVAILABLE"
    status_code = 502


class BackendCapabilityUnsupported(GatewayError):
    code = "BACKEND_CAPABILITY_UNSUPPORTED"
    status_code = 400


class BackendTimeout(GatewayError):
    code = "BACKEND_TIMEOUT"
    status_code = 504


class RequestTimeout(GatewayError):
    code = "REQUEST_TIMEOUT"
    status_code = 504
