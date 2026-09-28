# jev-gateway

[![Documentation](https://img.shields.io/badge/docs-jermeyhu.github.io%2Fjev--gateway-3f51b5?logo=material%2Ffor-linux)](https://jermeyhu.github.io/jev-gateway/)
[![PyPI](https://img.shields.io/pypi/v/jev-gateway?color=3f51b5)](https://pypi.org/project/jev-gateway/)
[![License](https://img.shields.io/badge/license-Apache--2.0-3f51b5)](LICENSE)
[![Python](https://img.shields.io/badge/python-%3E%3D3.11-3f51b5)](https://www.python.org/)

> Full documentation: **[jermeyhu.github.io/jev-gateway](https://jermeyhu.github.io/jev-gateway/)**
> — also in [简体中文](https://jermeyhu.github.io/jev-gateway/zh/)

A self-hosted, **Jev / System One compatible** decision gateway. It exposes the single
`POST /v1/systemone` endpoint you already program against, but runs the decision layer on
your own inference server instead of the closed TypeSafe API — and extends the contract
with local **image input**.

The gateway speaks exactly one wire protocol: the **OpenAI chat-completions API**
(`POST /v1/chat/completions` with `logprobs`). Any server that implements it works —
llama.cpp, vLLM, SGLang, Ollama, LM Studio, TGI, a hosted API — and no engine-specific
option is ever sent.

The official Jev endpoint is text only (`No image, audio, or video input`), and its
Pydantic AI client raises `UserError: Files are not supported by this model`. This gateway
is the drop-in replacement for the text path, and the same process can additionally score
evidence that includes screenshots or camera frames.

## How it works

A Jev call is a *classification* problem, not a generation problem. For every question the
gateway renders the evidence plus lettered options (`A`, `B`, `C`, …), asks the backend for
**exactly one** next token with logprobs (`max_tokens: 1`), reads each candidate letter's
logprob, and softmaxes the candidates into a probability distribution. No text is
generated, so a decision costs one forward pass over the prompt. That is what makes a
small local model (0.6B–4B) viable as a router, a gate and a triage classifier.

## Gateway vs direct tool calling

The alternative — asking the model to call a tool and reading the arguments — works too,
but it must *generate* the call. Measured on the same evidence and rubric (Qwen3.5-0.8B on
llama.cpp, single-threaded, 6 SRE triage cases × 3 runs, thinking disabled on both paths):

| metric (per case = 3 decisions) | gateway (logprobs) | direct tool call |
| ------------------------------- | ------------------ | ---------------- |
| latency, warm prompt cache      | **421 ms**         | 4547 ms          |
| latency, cold cache (first run) | 4742 ms            | 4258 ms          |
| output tokens                   | **3** (fixed)      | ~55              |
| input tokens (18 cases, total)  | 10 881             | 11 661           |
| accuracy, precise rubric        | 3/4                | 3/4              |

With the prompt cache warm the gateway is **~11× faster** and emits **~18× fewer output
tokens**; accuracy is the same. Generating a tool-call JSON costs ~18 decode steps per
decision, while the gateway decodes exactly one token. Use the direct path only when the
model must reason in free text before deciding.

Two rubric lessons from the same benchmark:

* **Write decidable criteria.** With vague labels ("Total outage / Degraded / Minor") both
  paths scored near chance — the direct path even answered a trivially-green case `sev1`.
  Restating each candidate with thresholds decidable from the state ("error_rate ≥ 0.20,
  OR a region down, …") fixed both paths at once, so the bottleneck was the wording, not
  the gateway.
* **The softmax is honest.** Reordering the candidates changes which *letter* wins but not
  which *label* wins, and the probability mass tracks the evidence (a global outage scores
  `sev1 ≈ 0.85`). Trust the distribution; calibrate thresholds on your own labelled set.

## Quick start

```bash
pip install -e ".[dev]"           # or: pip install -e .
cp config.yaml config.local.yaml  # optional
JEV_GATEWAY_CONFIG=config.local.yaml python -m app
```

`JEV_GATEWAY_CONFIG` is the only environment variable the gateway reads; it defaults to
`./config.yaml`. The server then listens on `server.host:server.port` (default
`0.0.0.0:8000`).

Docker:

```bash
docker compose up -d                                   # gateway alone
docker compose -f docker-compose.llamacpp.yml up -d    # gateway + llama-server
python scripts/smoke_test.py --url http://127.0.0.1:8000
```

Two more templates are referenced below but are **not shipped in this repository**:
`docker-compose.vllm.yml` and `docker-compose.sglang.yml`. Copy
`docker-compose.llamacpp.yml` and change the `base_url` and the `backend.model` to reach
either server; see the [backends page](https://jermeyhu.github.io/jev-gateway/backends/).

Each template mounts the same `./config.yaml`; edit the `command` block to change model,
context length or port. Put a `.gguf` in `./models` before starting a backend stack. Vision
models also need the projector: add `--mmproj /models/mmproj.gguf` to the `llama-server`
command and check `GET /props` → `modalities.vision` is `true`.

### Pointing the gateway at a backend

`backend.type` is always `openai`; only `backend.base_url` changes:

| server    | `backend.base_url` on the host | inside compose             | `backend.model`           |
| --------- | ------------------------------ | -------------------------- | ------------------------- |
| llama.cpp | `http://127.0.0.1:8080`        | `http://llama-server:8080` | `null` (auto-detected)    |
| vLLM      | `http://127.0.0.1:8001`        | `http://vllm:8000`         | the `--served-model-name` |
| SGLang    | `http://127.0.0.1:8002`        | `http://sglang:30000`      | the `--served-model-name` |

Inside a container `base_url` is never `127.0.0.1` (that is the gateway's own loopback);
to reach a server on the Docker host use `http://host.docker.internal:8080`. For vLLM and
SGLang, `backend.model` must match `--served-model-name`; leave it `null` to auto-detect.

Check it end to end against a running backend:

```bash
python scripts/smoke_test.py --url http://127.0.0.1:8000
python scripts/smoke_test.py --url http://127.0.0.1:8000 --image screenshot.png
```

## API

### `POST /v1/systemone`

```json
{
  "state": {"error_rate": 0.42, "p99_latency_ms": 3100, "recent_deploy": true},
  "questions": {
    "is_healthy": {"type": "noul", "instructions": "Is the service healthy?"},
    "severity": {
      "type": "choice",
      "instructions": "Pick the incident severity.",
      "criteria": {
        "sev1": "error_rate is 0.20 or higher, OR a whole region is down",
        "sev2": "error_rate is 0.05 or higher but below 0.20",
        "sev3": "error_rate is below 0.05 AND users are not visibly impacted"
      }
    },
    "urgency": {
      "type": "score",
      "instructions": "How urgent is the response?",
      "criteria": ["can wait", "today", "right now"]
    }
  }
}
```

Optional top-level fields: `model` (advisory only) and `images` (data URLs, remote URLs
opt-in, or raw base64). `state` accepts a non-empty string, object or list without
`NaN`/`Infinity`. Unknown keys anywhere are rejected. A question carries 2–**16**
candidates; more is rejected with `INVALID_REQUEST`.

Response:

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
  },
  "usage": {"input_tokens": 1421, "output_tokens": 3},
  "diagnostics": {
    "backend": "openai",
    "model": "Qwen3-4B-Instruct",
    "total_latency_ms": 214.7,
    "questions": {
      "is_healthy": {"latency_ms": 61.2, "truncated": false, "backend": "openai"}
    }
  }
}
```

Answer shapes:

* `noul` → `{"type": "noul", "noul": p}` — probability of `criteria.true` (or `Yes`).
* `choice` → argmax candidate plus the full distribution over candidate keys.
* `score` → probability-weighted mean of the level indices, so it interpolates between
  levels instead of collapsing to one.

Semantics matching the reference implementation: questions run **concurrently** (bounded
by `backend.max_concurrency`); **any** failing question fails the whole request;
`request.total_timeout_seconds` bounds the whole request, not each question.

### Other endpoints

| endpoint         | purpose                                      |
| ---------------- | -------------------------------------------- |
| `GET /healthz`   | liveness; never touches the backend          |
| `GET /readyz`    | readiness; probes the backend, 503 when down |
| `GET /v1/models` | OpenAI-shaped model list                     |

Every response carries an `x-request-id` header; send your own to correlate logs.

### Errors

```json
{"error": {"code": "BACKEND_TIMEOUT", "message": "...", "detail": null, "request_id": "..."}}
```

| code                          | status | when                                                  |
| ----------------------------- | ------ | ----------------------------------------------------- |
| `INVALID_REQUEST`             | 400    | schema/limit violation, bad image, unsupported feature |
| `BACKEND_CAPABILITY_UNSUPPORTED` | 400  | the model or backend cannot serve images               |
| `BACKEND_PROTOCOL_ERROR`      | 502    | backend answered with something unusable               |
| `BACKEND_UNAVAILABLE`         | 502    | connection refused / backend errored                   |
| `BACKEND_NOT_READY`           | 503    | backend not loaded or not reachable                    |
| `BACKEND_TIMEOUT`             | 504    | one question exceeded `backend.timeout_seconds`        |
| `REQUEST_TIMEOUT`             | 504    | the request exceeded `request.total_timeout_seconds`   |

## Images (extension)

Images attach to the front of the first user message, so the vision encoder result and the
evidence prefill stay cached across all questions of one request. Accepted forms:
`data:image/jpeg;base64,...`, `https://...` (only with `multimodal.allow_remote_urls:
true`), or raw base64 with the media type sniffed from magic bytes (jpeg, png, gif, bmp,
tiff, webp). Limits come from the `multimodal` config block. If the backend has no vision
support the request fails fast with `BACKEND_CAPABILITY_UNSUPPORTED`; set
`backend.supports_images: false` to disable the path entirely.

## Configuration reference

Unknown keys are a hard error, so a typo fails at startup instead of being ignored.

| section      | key                                             | default              | notes                                                            |
| ------------ | ----------------------------------------------- | -------------------- | ---------------------------------------------------------------- |
| `server`     | `host` / `port`                                 | `0.0.0.0` / `8000`   |                                                                  |
| `backend`    | `type`                                          | `openai`             | the only supported protocol                                      |
| `backend`    | `base_url` / `model` / `api_key`                | — / `null` / `null`  | `model: null` reports the backend's own model name                |
| `backend`    | `timeout_seconds`                               | `30.0`               | per question                                                     |
| `backend`    | `max_concurrency`                               | `32`                 | per gateway instance                                              |
| `backend`    | `top_logprobs`                                  | `128`                | 16–4096; candidates outside the window are floored                |
| `backend`    | `supports_images`                               | `null` (auto)        | `true` / `false` to force                                        |
| `backend`    | `extra_headers` / `extra_body`                  | `{}`                 | cloud API keys, tenant ids; see *Reasoning models*                |
| `request`    | `max_questions`                                 | `64`                 | 1–64 questions per request                                        |
| `request`    | `total_timeout_seconds`                         | `60.0`               | whole-request budget                                              |
| `request`    | `prompt_layout`                                 | `fused`              | `fused` \| `split`                                                |
| `multimodal` | `enabled` / `max_images` / `max_image_bytes`    | `true` / `4` / `5242880` | `allow_remote_urls: false`                                  |
| `logging`    | `level`                                         | `INFO`               |                                                                  |

### Prompt layouts

* `fused` — one user message with `{evidence, criterion, options}`; byte compatible with
  the reference gateway, so calibration measured against the hosted API transfers.
* `split` — evidence (and images) in the first user message, criterion and options in a
  second. The first message is identical for every question, so llama.cpp reuses the
  vision encoder work and the state prefill. Use it when latency matters more than
  byte-compatibility.

## Backends

The gateway sends `POST /v1/chat/completions` with `max_tokens: 1`, `logprobs: true` and
`top_logprobs: N`, then reads the next-token distribution from
`choices[0].logprobs.content[0].top_logprobs`. Every returned token is mapped onto a
candidate letter (`"A"` and `" A"` both count); the most likely variant wins. A candidate
outside the top-N window is floored at the least likely returned logprob and the question
is flagged `truncated` — raise `backend.top_logprobs` (up to 4096) if you see that flag.

`backend.supports_images: null` means "auto": assume the backend accepts images and never
advertise them if they are absent. Set it to `false` when the model has no vision tower,
so image requests fail fast instead of reaching the backend.

### Reasoning models

A *reasoning* model (Qwen3 / Qwen3.5, DeepSeek-R1, OpenAI o-series, …) emits its thinking
as the first token, so no candidate letter lands in the top-N window and every question
fails with `BACKEND_PROTOCOL_ERROR`. The gateway does not guess the fix; set
`backend.extra_body` to the switch your server understands:

| backend                           | `backend.extra_body`                             |
| --------------------------------- | ------------------------------------------------ |
| llama.cpp / vLLM / SGLang (Qwen3) | `chat_template_kwargs: {enable_thinking: false}` |
| OpenAI o-series / GPT-5           | `reasoning_effort: none`                         |
| OpenRouter (any reasoning model)  | `reasoning: {enabled: false}`                    |
| DeepSeek `deepseek-reasoner`      | *(no off switch — use `deepseek-chat` instead)*  |

`extra_body` is merged into every request but can never override `messages`, `max_tokens`,
`logprobs` or `top_logprobs`. Leave it `{}` for a non-reasoning model.

## Scripts

Decision models are **calibration sensitive**: threshold gating (`if p < 0.7: ask a human`)
only works if the probabilities are honest, and aggressive quantisation can preserve the
argmax while destroying the confidence. Prefer Q8_0 or F16 for gate models; treat Q4_K_M
as an experiment.

| script                   | purpose                                                                        |
| ------------------------ | ------------------------------------------------------------------------------ |
| `smoke_test.py`          | end-to-end health check against a running gateway (`--image` for vision)       |
| `compare_backends.py`    | A/B servers on the same cases; argmax agreement, mean abs delta, Brier distance |
| `bench_triage.py`        | accuracy: gateway logprobs vs direct tool calling on SRE triage cases          |
| `bench_speed_tokens.py`  | speed + output tokens: gateway vs direct tool calling (`--repeat N`)           |
| `diagnose_severity.py`   | vague vs precise rubric across both paths (isolates wording vs design)         |
| `probe_position_bias.py` | reorder candidates to tell position bias from semantic judgement               |
| `probe_live.py`          | boundary checks against a live gateway (image limits, candidate limits)        |
| `make_test_png.py`       | generate a tiny valid PNG without Pillow                                       |

`compare_backends.py` runs the same case set against several servers and reports what
actually matters:

```bash
python scripts/compare_backends.py \
    --backend llama=http://127.0.0.1:8080+top_logprobs:128 \
    --backend vllm=http://127.0.0.1:8001+top_logprobs:64 \
    --repeat 3
```

```
=== pairwise agreement ===
llama vs vllm: argmax 4/5, mean|dp| 0.0413, brier distance 0.00298

=== latency ===
llama                  n=15  p50=  62.4ms p95=  88.1ms mean=  67.0ms
vllm                   n=15  p50=  41.9ms p95=  55.3ms mean=  44.8ms
```

* **argmax agreement** — did the *ranking* survive?
* **mean|dp|** — how far the probability vectors moved.
* **Brier distance** — the mean squared gap between them. This is the number to watch:
  high argmax agreement with a large Brier distance means a quantisation or backend change
  quietly broke your thresholds.

`--backend` takes `NAME=URL[+key:value]`, where the optional values are
`model`, `api_key`, `top_logprobs`, `supports_images`.

## Development

```bash
pip install -e ".[dev]"
pytest -q
ruff check .
mypy app
```

The suite covers prompt layouts, image parsing, the logprob → answer maths, the backend
against mock transports, config validation, the HTTP surface, and an end-to-end in-process
request. No network or GPU is required.

## Documentation

The full documentation lives at **[jermeyhu.github.io/jev-gateway](https://jermeyhu.github.io/jev-gateway/)**
(简体中文: [/zh/](https://jermeyhu.github.io/jev-gateway/zh/)) and is built from `docs/`
with MkDocs Material. Pages: [quick start](https://jermeyhu.github.io/jev-gateway/quick-start/),
[how it works](https://jermeyhu.github.io/jev-gateway/how-it-works/),
[API reference](https://jermeyhu.github.io/jev-gateway/api/),
[configuration](https://jermeyhu.github.io/jev-gateway/configuration/),
[backends](https://jermeyhu.github.io/jev-gateway/backends/),
[images](https://jermeyhu.github.io/jev-gateway/images/),
[scripts](https://jermeyhu.github.io/jev-gateway/scripts/),
[limits & FAQ](https://jermeyhu.github.io/jev-gateway/limits/) and
[development](https://jermeyhu.github.io/jev-gateway/development/).

To build it locally:

```bash
pip install -r requirements-docs.txt
mkdocs serve        # http://127.0.0.1:8001
mkdocs build --strict
```

## Notes and limits

* The `noul` probability is a softmax over the two candidate letters only; calibrate
  thresholds against your own labelled set.
* A question carries at most **16 candidates**; more is rejected with `INVALID_REQUEST`.
* `truncated: true` in diagnostics means a candidate was floored; raise `top_logprobs`.
* Images extend the reference contract; clients written against the hosted service still
  work but ignore the field.
* Scale horizontally behind a load balancer rather than raising `max_concurrency` beyond
  the backend's real capacity.

## Acknowledgements

The request/response contract and the logprob-classification idea are modelled on
[David-Lolly/Jev-Compatible](https://github.com/David-Lolly/Jev-Compatible) — thanks for
the reference implementation this gateway stays compatible with.

## License

Apache-2.0.
