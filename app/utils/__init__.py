"""Shared helpers for the gateway."""

from app.utils.errors import (
    BackendCapabilityUnsupported,
    BackendNotReady,
    BackendProtocolError,
    BackendTimeout,
    BackendUnavailable,
    GatewayError,
    InvalidRequest,
    RequestTimeout,
)

__all__ = [
    "BackendCapabilityUnsupported",
    "BackendNotReady",
    "BackendProtocolError",
    "BackendTimeout",
    "BackendUnavailable",
    "GatewayError",
    "InvalidRequest",
    "RequestTimeout",
]
