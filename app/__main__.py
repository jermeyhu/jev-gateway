"""``python -m app`` / ``jev-gateway`` entry point."""

from __future__ import annotations

import logging

import uvicorn

from app.config import load_settings


def main() -> None:
    settings = load_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    uvicorn.run(
        "app.main:app",
        host=settings.server.host,
        port=settings.server.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
