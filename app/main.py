"""FastAPI application for the Jev / System One compatible gateway."""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app import __version__
from app.config import Settings, load_settings
from app.schemas.request import SystemOneRequest
from app.schemas.response import SystemOneResponse
from app.service import DecisionService
from app.utils.errors import GatewayError

logger = logging.getLogger("jev_gateway")

REQUEST_ID_HEADER = "x-request-id"


def create_app(
    settings: Settings | None = None, *, service: DecisionService | None = None
) -> FastAPI:
    resolved = settings or load_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        application.state.service = service or DecisionService(resolved)
        try:
            yield
        finally:
            await application.state.service.aclose()

    application = FastAPI(
        title="Jev Gateway",
        version=__version__,
        summary="System One compatible decision gateway over any OpenAI-compatible server",
        lifespan=lifespan,
    )

    def get_service(request: Request) -> DecisionService:
        return request.app.state.service

    @application.middleware("http")
    async def request_id_middleware(request: Request, call_next: Any) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response

    @application.exception_handler(GatewayError)
    async def gateway_error_handler(request: Request, exc: GatewayError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        if exc.status_code >= 500:
            logger.warning("%s: %s", exc.code, exc.message)
        else:
            logger.info("%s: %s", exc.code, exc.message)
        return JSONResponse(
            status_code=exc.status_code, content=exc.to_payload(request_id)
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "INVALID_REQUEST",
                    "message": "request validation failed",
                    "detail": _sanitise_errors(exc.errors()),
                    "request_id": request_id,
                }
            },
        )

    @application.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, Any]:
        return {"status": "ok", "version": __version__}

    @application.get("/readyz", tags=["ops"])
    async def readyz(request: Request) -> JSONResponse:
        try:
            await get_service(request).check_ready()
        except GatewayError as exc:
            return JSONResponse(
                status_code=503, content={"status": "not_ready", "reason": exc.code}
            )
        return JSONResponse(
            status_code=200,
            content={
                "status": "ready",
                "backend": resolved.backend.type,
                "model": get_service(request).model_name,
            },
        )

    @application.get("/v1/models", tags=["ops"])
    async def models(request: Request) -> dict[str, Any]:
        instance = get_service(request)
        return {
            "object": "list",
            "data": [
                {"id": instance.model_name, "object": "model", "owned_by": "jev-gateway"}
            ],
        }

    @application.post(
        "/v1/systemone",
        response_model=SystemOneResponse,
        tags=["decision"],
        summary="Score one request against many questions",
    )
    async def systemone(
        payload: SystemOneRequest, request: Request
    ) -> SystemOneResponse:
        return await get_service(request).systemone(payload)

    return application


def _sanitise_errors(errors: Sequence[Any]) -> list[dict[str, Any]]:
    """Drop pydantic's non-serialisable ``ctx`` values and keep it readable."""
    cleaned: list[dict[str, Any]] = []
    for error in errors:
        item = {key: value for key, value in error.items() if key != "ctx"}
        message = item.get("msg")
        if isinstance(message, str):
            item["msg"] = message.removeprefix("Value error, ")
        cleaned.append(item)
    return cleaned


app = create_app()
