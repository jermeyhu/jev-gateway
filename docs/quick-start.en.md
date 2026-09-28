# Quick start

## Install

```bash
pip install -e ".[dev]"           # or: pip install -e .
```

Python 3.11 or newer is required. The runtime dependencies are `fastapi`, `httpx`, `pydantic`
and `uvicorn[standard]`.

## Configure

Configuration is entirely `JEV_`-prefixed environment variables — **there is no config
file**. Anything you do not set falls back to its default, so the gateway starts in an
empty environment. To keep them together, use the template:

```bash
cp .env.example .env      # .env is git-ignored
$EDITOR .env
```

The one line that matters is `JEV_BACKEND_BASE_URL` — the address of your OpenAI-compatible
server:

```bash
JEV_BACKEND_BASE_URL=http://127.0.0.1:8080
JEV_BACKEND_MODEL=                      # empty = auto-detect from /v1/models
```

Every variable, default and valid range is in the
[configuration reference](configuration.en.md). The server listens on
`JEV_SERVER_HOST:JEV_SERVER_PORT` (default `0.0.0.0:8000`).

## Run

```bash
set -a; . ./.env; set +a     # export .env into this shell
python -m app
```

The console script `jev-gateway` is equivalent once the variables are exported. You can also
pass them straight on the command line and skip the file entirely:

```bash
JEV_BACKEND_BASE_URL=http://192.168.1.10:8080 python -m app
```

Check that it is alive:

```bash
curl http://127.0.0.1:8000/healthz
# {"status":"ok","version":"0.1.0"}
```

## Send a decision

```bash
curl -X POST http://127.0.0.1:8000/v1/systemone \
  -H 'content-type: application/json' \
  -d '{
    "state": {"error_rate": 0.42, "p99_latency_ms": 3100, "recent_deploy": true},
    "questions": {
      "is_healthy": {"type": "noul", "instructions": "Is the service healthy?"},
      "severity": {
        "type": "choice",
        "instructions": "Pick the incident severity.",
        "criteria": {"sev1": "a whole region is down", "sev2": "error_rate is at least 0.05", "sev3": "below 0.05 and users are unaffected"}
      },
      "urgency": {
        "type": "score",
        "instructions": "How urgent is the response?",
        "criteria": ["can wait", "today", "right now"]
      }
    }
  }'
```

```json
{
  "model": "Qwen3-4B-Instruct",
  "answers": {
    "is_healthy": {"type": "noul", "noul": 0.0314},
    "severity": {
      "type": "choice",
      "choice": "sev2",
      "probabilities": {"sev1": 0.024, "sev2": 0.921, "sev3": 0.055}
    },
    "urgency": {
      "type": "score",
      "score": 1.86,
      "legend": {"0": "can wait", "1": "today", "2": "right now"},
      "probabilities": {"0": 0.041, "1": 0.058, "2": 0.901}
    }
  }
}
```

One request, three decisions, three output tokens.

## Docker

```bash
docker compose up -d                                   # gateway alone
docker compose -f docker-compose.llamacpp.yml up -d    # gateway + llama-server
```

Each template passes configuration through `environment:` and **mounts no file at all**. To
change the backend address, change that variable; to change the model, context length or
port, edit the `command` block. Put a `.gguf` in `./models` before starting a backend
stack. Vision models also need the projector: add
`--mmproj /models/mmproj.gguf` to the `llama-server` command and check
`GET /props` → `modalities.vision` is `true`.

## Verify end to end

```bash
curl -s http://127.0.0.1:8000/healthz
curl -s http://127.0.0.1:8000/readyz

curl -s http://127.0.0.1:8000/v1/systemone \
  -H 'content-type: application/json' \
  -d '{
        "state": {"error_rate": 0.31, "p99_latency_ms": 4100},
        "questions": {
          "severity": {
            "type": "choice",
            "instructions": "Pick the severity.",
            "criteria": {
              "sev1": "error_rate >= 0.20",
              "sev2": "error_rate >= 0.05 but < 0.20",
              "sev3": "error_rate < 0.05"
            }
          }
        }
      }'
```

`/healthz` proves the process is up, `/readyz` proves the backend is ready and reports the
model it loaded, and the last call returns the answers plus the `usage` and `diagnostics`
blocks. To include an image, replace `state` with
`{"images": ["data:image/png;base64,..."]}`.

!!! note "vLLM and SGLang"

    Only the llama.cpp compose stack ships with this repository. To reach a vLLM or
    SGLang server, point `backend.base_url` at it and set `backend.model` to the name
    the server was started with. See [Backends](backends).

## Where to go next

* [How it works](how-it-works) — the classification trick and the prompt layouts.
* [API reference](api) — every field, endpoint and error code.
* [Backends](backends) — the `base_url` table for llama.cpp, vLLM and SGLang.
