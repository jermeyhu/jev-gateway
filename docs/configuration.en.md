# Configuration reference {#configuration}

Every setting in the gateway is an environment variable prefixed with `JEV_`.
**There is no config file** — a variable that is not set simply takes its
default, so the gateway starts in an empty environment. The committed,
commented template is `.env.example`; `cp .env.example .env` to get started, and
`.env` is in `.gitignore` so the endpoints and keys you fill in stay out of
version control.

```bash
cp .env.example .env
$EDITOR .env
```

Docker Compose reads a `.env` next to the compose file automatically. For a
local run, export the values into your shell:

```bash
set -a; . ./.env; set +a
python -m app
```

Or skip the file entirely and pass them on the command line:

```bash
JEV_BACKEND_BASE_URL=http://192.168.1.10:8080 JEV_BACKEND_MODEL=qwen3-0.6b python -m app
```

**A blank value means unset**, so `JEV_BACKEND_MODEL=` behaves exactly like
leaving the variable out. **An unusable value fails at startup**, and the error
names the variable — a typo is a crash, never a silent fallback.

## Full reference {#reference}

| Variable                                  | Default                 | Notes                                     |
| ----------------------------------------- | ----------------------- | ----------------------------------------- |
| `JEV_SERVER_HOST`                         | `0.0.0.0`               |                                           |
| `JEV_SERVER_PORT`                         | `8000`                  |                                           |
| `JEV_BACKEND_TYPE`                        | `openai`                | The only supported protocol               |
| `JEV_BACKEND_BASE_URL`                    | `http://127.0.0.1:8080` |                                           |
| `JEV_BACKEND_MODEL`                       | empty (auto-detect)     | Empty means "use the backend's own model" |
| `JEV_BACKEND_API_KEY`                     | empty                   | Hosted providers only                      |
| `JEV_BACKEND_TIMEOUT_SECONDS`             | `30.0`                  | Per-question timeout                      |
| `JEV_BACKEND_MAX_CONCURRENCY`             | `32`                    | Per-gateway instance ceiling              |
| `JEV_BACKEND_TOP_LOGPROBS`                | `128`                   | 16 – 4096; out-of-window candidates floor |
| `JEV_BACKEND_SUPPORTS_IMAGES`             | `auto`                  | `true` / `false` to force it              |
| `JEV_BACKEND_EXTRA_HEADERS`               | `{}`                    | JSON object; cloud key, tenant id         |
| `JEV_BACKEND_EXTRA_BODY`                  | `{}`                    | JSON object; the "no thinking" switch     |
| `JEV_REQUEST_MAX_QUESTIONS`               | `64`                    | 1 – 64 questions per request              |
| `JEV_REQUEST_TOTAL_TIMEOUT_SECONDS`       | `60.0`                  | Whole-request budget                      |
| `JEV_REQUEST_PROMPT_LAYOUT`               | `fused`                 | `fused` \| `split`                       |
| `JEV_MULTIMODAL_ENABLED`                  | `true`                  | `false` rejects any `images` with 400     |
| `JEV_MULTIMODAL_MAX_IMAGES`               | `4`                     | Images per request                        |
| `JEV_MULTIMODAL_MAX_IMAGE_BYTES`          | `5242880` (5 MiB)       | Per image, measured after base64 decode   |
| `JEV_MULTIMODAL_ALLOW_REMOTE_URLS`        | `false`                 | Required before `https://` images are read |
| `JEV_MULTIMODAL_ALLOWED_MIME_PREFIXES`    | `image/`                | Comma-separated                           |
| `JEV_LOGGING_LEVEL`                       | `INFO`                  |                                           |

A trailing slash on `JEV_BACKEND_BASE_URL` is stripped, so
`http://127.0.0.1:8080/` and `http://127.0.0.1:8080` are equivalent.

Booleans accept `true` / `false`, `1` / `0`, `yes` / `no`, `on` / `off`, in any
case. `JEV_BACKEND_SUPPORTS_IMAGES` additionally accepts `auto`.

## By group {#sections}

### Server

`JEV_SERVER_HOST` / `JEV_SERVER_PORT` are the listen address, default
`0.0.0.0:8000` — what the container image and the Compose templates use.

### Backend `JEV_BACKEND_*` {#backend}

`TYPE` is always `openai`; the gateway speaks exactly one protocol. Only
`BASE_URL` changes between servers, see
[Backends](backends.md#base_url-by-server).

Leaving `MODEL` empty means auto-detect: the gateway asks the server's
`/v1/models` and takes the first model it reports. A server that reports several
models, or that insists the client name one (vLLM, SGLang with multiple served
models), should be pinned explicitly.

`TOP_LOGPROBS` is the size of the next-token window. With two to four candidates
the default of `128` is already generous; raise it when diagnostics show
`truncated: true`. Valid range is 16 – 4096.

!!! warning "Servers have their own cap, and the default of 128 can fail every request"

    The OpenAI spec caps `top_logprobs` at 20, and plenty of servers cap it
    lower still. Past the cap the backend does **not** error — it simply sets
    `logprobs` to `null`, which surfaces as
    `BACKEND_PROTOCOL_ERROR: backend response contained no logprobs object`.
    This is the hardest failure when swapping backends, because it returns
    `HTTP 200` and logs no error at all.

    Probe a new backend at `20 / 24 / 32 / 64 / 128` to find the limit (see
    [Backends](backends.md#silent-logprobs-null)), then put the largest value
    that works here.

`SUPPORTS_IMAGES=auto` means "assume images work, and simply don't send them if
the backend has no vision tower". Set it to `false` for a model with no vision
tower so image requests fail fast with `BACKEND_CAPABILITY_UNSUPPORTED` instead
of reaching the backend.

`EXTRA_HEADERS` is merged into every request as HTTP headers — a cloud API key,
tenant id, project name. The value is a JSON object, so quote it in the shell:

```bash
JEV_BACKEND_EXTRA_HEADERS='{"X-Tenant": "acme"}'
```

`EXTRA_BODY` is merged into the JSON body the same way; this is where a server's
"stop thinking" switch belongs:

```bash
JEV_BACKEND_EXTRA_BODY='{"chat_template_kwargs": {"enable_thinking": false}}'
```

A `reasoning` field leaking into the response consumes the single token
`max_tokens: 1` allows, which turns `logprobs` into `null`. Each server
recognises a different spelling (llama.cpp / vLLM take `chat_template_kwargs`;
OpenRouter's `openrouter/qwen/*` only takes `reasoning: {effort: "none"}`), so
when in doubt **write both** — unknown keys are silently ignored:

```bash
JEV_BACKEND_EXTRA_BODY='{"chat_template_kwargs": {"enable_thinking": false}, "reasoning": {"effort": "none"}}'
```

Neither can override `messages`, `max_tokens`, `logprobs` or `top_logprobs`.

### Request `JEV_REQUEST_*` {#request}

`MAX_QUESTIONS` caps how many questions one request may carry (1 – 64).
Anything beyond is rejected with `INVALID_REQUEST`.

`TOTAL_TIMEOUT_SECONDS` is the budget for the whole request, summed across
questions. Because questions run concurrently, a 64-question request on a
60-second budget still finishes in roughly the time of the slowest single
question — unless the backend is already saturated.

`PROMPT_LAYOUT` is `fused` (default, byte compatible with the reference gateway)
or `split` (cache friendly). Both are described in
[How it works](how-it-works.md#prompt-layouts).

### Multimodal `JEV_MULTIMODAL_*` {#multimodal}

| Variable                          | Default             | Meaning                                    |
| --------------------------------- | ------------------- | ------------------------------------------ |
| `ENABLED`                         | `true`              | `false` rejects any `images` with 400      |
| `MAX_IMAGES`                      | `4`                 | Images per request                         |
| `MAX_IMAGE_BYTES`                 | `5242880` (5 MiB)   | Per image, after base64 decode             |
| `ALLOW_REMOTE_URLS`               | `false`             | Required before `https://` images are read |
| `ALLOWED_MIME_PREFIXES`           | `image/`            | Comma-separated MIME prefixes              |

`ALLOW_REMOTE_URLS` defaults to `false` because a remote URL makes the gateway
fetch an arbitrary address chosen by the caller — a server-side request forgery
surface. Only turn it on when both ends are yours.

### Logging `JEV_LOGGING_LEVEL` {#logging}

Accepts the standard Python levels: `DEBUG`, `INFO`, `WARNING`, `ERROR`.
Default `INFO`. Every response already carries `x-request-id` and the logs use
the same id, so grep that when debugging.

## Prompt layouts in one line {#prompt-layouts-in-one-line}

- `fused` — a single user message holding `{evidence, criterion, options}`, byte
  compatible with the reference gateway, so calibration measured against a hosted
  API carries over unchanged.
- `split` — evidence (and images) go in the first user message, the instructions
  and options in the second. The first is byte-identical across every question
  in one request, so llama.cpp can reuse the vision encoding and the state
  prefill. Use it when latency matters more than byte compatibility.
