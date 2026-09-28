# Quick start

## Install

```bash
pip install -e ".[dev]"           # or: pip install -e .
```

Python 3.11 or newer is required. The runtime dependencies are `fastapi`, `httpx`,
`pydantic`, `pyyaml` and `uvicorn[standard]`.

## Configure

```bash
cp config.yaml config.local.yaml  # optional, keeps the shipped file pristine
$EDITOR config.local.yaml
```

`JEV_GATEWAY_CONFIG` is the only environment variable the gateway reads; it defaults to
`./config.yaml`. The server then listens on `server.host:server.port` (default
`0.0.0.0:8000`).

The one line that matters is `backend.base_url` — the address of your OpenAI-compatible
server:

```yaml
backend:
  type: openai
  base_url: http://127.0.0.1:8080
  model: null          # null = auto-detect from the server's /v1/models
```

## Run

```bash
JEV_GATEWAY_CONFIG=config.local.yaml python -m app
```

The console script `jev-gateway` is equivalent and needs no config path on the command
line if the environment variable is set.

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

Each template mounts the same `./config.yaml`; edit the `command` block to change the
model, context length or port. Put a `.gguf` in `./models` before starting a backend
stack. Vision models also need the projector: add
`--mmproj /models/mmproj.gguf` to the `llama-server` command and check
`GET /props` → `modalities.vision` is `true`.

## Verify end to end

```bash
python scripts/smoke_test.py --url http://127.0.0.1:8000
python scripts/smoke_test.py --url http://127.0.0.1:8000 --image screenshot.png
```

The smoke test hits `/healthz`, `/readyz` and `/v1/systemone`, validates that every answer
has the right shape, and prints the usage and diagnostics blocks.

!!! note "vLLM and SGLang templates"

    The README also shows `docker-compose.vllm.yml` and `docker-compose.sglang.yml`
    commands. Those two files are not shipped in the repository; point `backend.base_url`
    at your own vLLM / SGLang service instead. See [Backends](backends.md).

## Where to go next

* [How it works](how-it-works.md) — the classification trick and the prompt layouts.
* [API reference](api.md) — every field, endpoint and error code.
* [Backends](backends.md) — the `base_url` table for llama.cpp, vLLM and SGLang.
