"""FastAPI application for the Jev / System One compatible gateway."""

from __future__ import annotations

import base64
import gzip
import io
import json
import logging
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

import zstandard
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.datastructures import UploadFile

from app import __version__
from app.config import Settings, load_settings
from app.schemas.request import SystemOneRequest
from app.schemas.response import SystemOneResponse
from app.service import DecisionService
from app.utils.errors import (
    GatewayError,
    InvalidRequest,
    PayloadTooLarge,
    UnsupportedMediaType,
)
from app.utils.imaging import _sniff_mime

logger = logging.getLogger("jev_gateway")

REQUEST_ID_HEADER = "x-request-id"
MAX_REQUEST_BYTES = 32 * 1024 * 1024


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
            status_code=422,
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
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/SystemOneRequest"}
                    },
                    "multipart/form-data": {
                        "schema": {
                            "type": "object",
                            "required": ["request"],
                            "properties": {
                                "request": {
                                    "type": "string",
                                    "contentMediaType": "application/json",
                                },
                            },
                            "additionalProperties": {"type": "string", "format": "binary"},
                        }
                    },
                },
            }
        },
    )
    async def systemone(request: Request) -> SystemOneResponse:
        payload = await _parse_systemone_request(request)
        images_enabled = (
            request.headers.get("TypeSafe-Danger-Zone-Images", "").lower() == "true"
        )
        if payload.assets and not images_enabled:
            raise InvalidRequest(
                "image requests require TypeSafe-Danger-Zone-Images: true",
            )
        return await get_service(request).systemone(payload)

    def openapi() -> dict[str, Any]:
        if application.openapi_schema:
            return application.openapi_schema
        schema = get_openapi(
            title=application.title,
            version=application.version,
            openapi_version=application.openapi_version,
            summary=application.summary,
            routes=application.routes,
        )
        request_schema = SystemOneRequest.model_json_schema(
            ref_template="#/components/schemas/{model}"
        )
        definitions = request_schema.pop("$defs", {})
        schemas = schema.setdefault("components", {}).setdefault("schemas", {})
        schemas.update(definitions)
        schemas["SystemOneRequest"] = request_schema
        application.openapi_schema = schema
        return schema

    application.openapi = openapi  # type: ignore[method-assign]

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


async def _parse_systemone_request(request: Request) -> SystemOneRequest:
    request_body = await _read_request_body(request)
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        parts = list(form.multi_items())
        if not parts or parts[0][0] != "request":
            raise InvalidRequest("the first multipart part must be named request")
        request_part = parts[0][1]
        if isinstance(request_part, UploadFile):
            if request_part.content_type != "application/json":
                raise InvalidRequest("multipart request part must be application/json")
            try:
                request_text = (await request_part.read()).decode("utf-8")
            except UnicodeDecodeError as exc:
                raise InvalidRequest("multipart request part must be UTF-8 JSON") from exc
        elif isinstance(request_part, str):
            request_text = request_part
        else:
            raise InvalidRequest("multipart request part is invalid")
        try:
            body = json.loads(request_text)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InvalidRequest("multipart request part is invalid JSON") from exc
        if not isinstance(body, dict):
            raise InvalidRequest("request body must be a JSON object")

        files: dict[str, UploadFile] = {}
        for name, value in parts[1:]:
            if not isinstance(value, UploadFile) or name in files:
                raise InvalidRequest("multipart file parts must be unique")
            files[name] = value
        assets = body.get("assets") or {}
        if not isinstance(assets, dict):
            raise InvalidRequest("assets must be an object")
        referenced_parts: set[str] = set()
        for reference, asset in assets.items():
            if not isinstance(asset, dict) or not isinstance(asset.get("part"), str):
                continue
            part_name = asset["part"]
            if part_name in referenced_parts or part_name not in files:
                raise InvalidRequest(f"asset {reference!r} has a missing or duplicate part")
            referenced_parts.add(part_name)
            upload = files[part_name]
            media_type = (upload.content_type or "").lower()
            allowed_types = {
                "",
                "application/octet-stream",
                "image/png",
                "image/jpeg",
                "image/jpg",
            }
            if media_type not in allowed_types:
                raise InvalidRequest(f"asset part {part_name!r} has an unsupported media type")
            image_bytes = await upload.read()
            mime = _sniff_mime(image_bytes)
            if media_type in {"image/png", "image/jpeg", "image/jpg"}:
                expected = "image/png" if media_type == "image/png" else "image/jpeg"
                if mime != expected:
                    raise InvalidRequest(f"asset part {part_name!r} does not match its media type")
                mime = expected
            if mime not in {"image/png", "image/jpeg"}:
                raise InvalidRequest(f"asset part {part_name!r} is not a supported image")
            asset["format"] = "png" if mime == "image/png" else "jpg"
            asset["data_base64"] = base64.b64encode(image_bytes).decode("ascii")
        if set(files) != referenced_parts:
            raise InvalidRequest("every multipart file must belong to one asset")
    else:
        try:
            body = json.loads(request_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InvalidRequest("request body must be UTF-8 JSON") from exc

    try:
        return SystemOneRequest.model_validate(body)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors(), body=body) from exc


async def _read_request_body(request: Request) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_REQUEST_BYTES:
                raise PayloadTooLarge("request body exceeds 32 MiB")
        except ValueError as exc:
            raise InvalidRequest("invalid Content-Length header") from exc

    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_REQUEST_BYTES:
            raise PayloadTooLarge("request body exceeds 32 MiB")
        chunks.append(chunk)
    body = b"".join(chunks)

    encoding = request.headers.get("content-encoding")
    if encoding is None:
        request._body = body
        return body
    encoding = encoding.strip().lower()
    try:
        if encoding in {"gzip", "x-gzip"}:
            with gzip.GzipFile(fileobj=io.BytesIO(body)) as stream:
                decoded = stream.read(MAX_REQUEST_BYTES + 1)
        elif encoding == "zstd":
            decompressor = zstandard.ZstdDecompressor(max_window_size=8192)
            with decompressor.stream_reader(io.BytesIO(body)) as stream:
                decoded = stream.read(MAX_REQUEST_BYTES + 1)
        else:
            raise UnsupportedMediaType(f"unsupported Content-Encoding: {encoding}")
    except (gzip.BadGzipFile, EOFError, OSError, zstandard.ZstdError) as exc:
        raise InvalidRequest("request body has invalid compressed data") from exc
    if len(decoded) > MAX_REQUEST_BYTES:
        raise PayloadTooLarge("decompressed request body exceeds 32 MiB")
    request._body = decoded
    return decoded


app = create_app()
