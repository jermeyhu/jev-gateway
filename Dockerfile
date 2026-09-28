FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir .

# There is deliberately nothing to copy for configuration: every setting is an
# environment variable, so the image carries no config at all.  The container
# starts on the built-in defaults (a backend on 127.0.0.1:8080) and is pointed
# elsewhere with JEV_BACKEND_BASE_URL and friends -- see the compose files.

RUN useradd --create-home --uid 10001 gateway
USER gateway

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"

CMD ["python", "-m", "app"]
