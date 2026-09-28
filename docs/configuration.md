# Configuration

The gateway reads one YAML file. By default that is `./config.yaml`; override the path
with the `JEV_GATEWAY_CONFIG` environment variable.

```yaml
server:
  host: 0.0.0.0
  port: 8000

backend:
  type: openai
  base_url: http://127.0.0.1:8080
  model: null
  api_key: null
  timeout_seconds: 30.0
  max_concurrency: 32
  top_logprobs: 128
  supports_images: null
  extra_headers: {}
  extra_body: {}

request:
  max_questions: 64
  total_timeout_seconds: 60.0
  prompt_layout: fused

multimodal:
  enabled: true
  max_images: 4
  max_image_bytes: 5242880
  allow_remote_urls: false
  allowed_mime_prefixes: ["image/"]

logging:
  level: INFO
```

**Unknown keys are a hard error**, so a typo fails at startup instead of being ignored.

## Reference

| section      | key                                          | default                 | notes                                                            |
| ------------ | -------------------------------------------- | ----------------------- | ---------------------------------------------------------------- |
| `server`     | `host` / `port`                              | `0.0.0.0` / `8000`      |                                                                  |
| `backend`    | `type`                                       | `openai`                | the only supported protocol                                      |
| `backend`    | `base_url` / `model` / `api_key`             | — / `null` / `null`     | `model: null` reports the backend's own model name                |
| `backend`    | `timeout_seconds`                            | `30.0`                  | per question                                                     |
| `backend`    | `max_concurrency`                            | `32`                    | per gateway instance                                             |
| `backend`    | `top_logprobs`                               | `128`                   | 16 – 4096; candidates outside the window are floored             |
| `backend`    | `supports_images`                            | `null` (auto)           | `true` / `false` to force                                        |
| `backend`    | `extra_headers` / `extra_body`               | `{}`                    | cloud API keys, tenant ids; see *Reasoning models*                |
| `request`    | `max_questions`                              | `64`                    | 1 – 64 questions per request                                      |
| `request`    | `total_timeout_seconds`                      | `60.0`                  | whole-request budget                                              |
| `request`    | `prompt_layout`                              | `fused`                 | `fused` \| `split`                                                |
| `multimodal` | `enabled` / `max_images` / `max_image_bytes` | `true` / `4` / `5242880` | `allow_remote_urls: false`                                       |
| `logging`    | `level`                                      | `INFO`                  |                                                                  |

A trailing slash on `base_url` is trimmed, so `http://127.0.0.1:8080/` and
`http://127.0.0.1:8080` are equivalent.

## Sections

### `server`

The bind address. `0.0.0.0:8000` by default, which is what the container image and the
Docker Compose templates use.

### `backend`

`type` is always `openai` — the gateway speaks exactly one protocol. What changes between
servers is only `base_url`; see [Backends](backends.md#base_url-by-server).

`model: null` means auto-detect: the gateway asks the server's `/v1/models` and uses the
first model it reports. Servers that name several models, or that require the client to
state which one it wants (vLLM, SGLang with multiple served models), should be pinned
explicitly.

`top_logprobs` is the size of the next-token window. The default `128` is generous for two
to four candidates; raise it if you see `truncated: true` in the diagnostics, lower it if
the server refuses large windows. The accepted range is 16 – 4096.

`supports_images: null` is "auto": assume the backend accepts images and simply do not
send any when they are absent. Set it to `false` when the model has no vision tower, so
image requests fail fast with `BACKEND_CAPABILITY_UNSUPPORTED` instead of reaching the
backend.

`extra_headers` is merged into every request as HTTP headers — a cloud API key, a tenant
id, a project name. `extra_body` is merged into the JSON body; it is where a server's
"turn thinking off" switch belongs. Neither can override `messages`, `max_tokens`,
`logprobs` or `top_logprobs`.

### `request`

`max_questions` caps how many questions one request may carry (1 – 64). Requests above the
cap are rejected with `INVALID_REQUEST`.

`total_timeout_seconds` is the budget for the entire request, all questions together. The
questions run concurrently, so a request with 64 questions and a 60-second budget still
finishes in roughly the time of the slowest single question — unless the backend is
saturated.

`prompt_layout` is `fused` (default, byte-compatible with the reference gateway) or
`split` (cache-friendly). Both are described in
[How it works](how-it-works.md#prompt-layouts).

### `multimodal`

| key                     | default            | meaning                                        |
| ----------------------- | ------------------ | ---------------------------------------------- |
| `enabled`               | `true`             | when `false`, any `images` field is a 400      |
| `max_images`            | `4`                | images per request                              |
| `max_image_bytes`       | `5242880` (5 MiB)  | per image, after base64 decoding                |
| `allow_remote_urls`     | `false`            | opt in to `https://` image references           |
| `allowed_mime_prefixes` | `["image/"]`       | accepted media types                            |

`allow_remote_urls: false` is the default because a remote URL makes the gateway fetch
whatever the caller points at — a server-side request forgery surface. Turn it on only
when you control both ends.

### `logging`

`level` accepts the standard Python levels: `DEBUG`, `INFO`, `WARNING`, `ERROR`. `INFO` is
the default. Every response already carries an `x-request-id`; the log lines use the same
id, so grep for it when correlating a report with the logs.

## Prompt layouts in one line

* `fused` — one user message with `{evidence, criterion, options}`; byte compatible with
  the reference gateway, so calibration measured against the hosted API transfers.
* `split` — evidence (and images) in the first user message, criterion and options in a
  second. The first message is identical for every question, so llama.cpp reuses the vision
  encoder work and the state prefill. Use it when latency matters more than
  byte-compatibility.
