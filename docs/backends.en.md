# Backends

## One protocol

The gateway sends `POST /v1/chat/completions` with `max_tokens: 1`, `logprobs: true` and
`top_logprobs: N`, then reads the next-token distribution from
`choices[0].logprobs.content[0].top_logprobs`.

That is the entire contract. Any server implementing the OpenAI chat-completions API with
logprobs works — llama.cpp, vLLM, SGLang, Ollama, LM Studio, TGI, a hosted API — and **no
engine-specific option is ever sent**. Swapping a backend is a one-line `base_url` change.

## `base_url` by server

`backend.type` is always `openai`; only `backend.base_url` changes:

| server    | `backend.base_url` on the host | inside compose             | `backend.model`           |
| --------- | ------------------------------ | -------------------------- | ------------------------- |
| llama.cpp | `http://127.0.0.1:8080`        | `http://llama-server:8080` | `null` (auto-detected)    |
| vLLM      | `http://127.0.0.1:8001`        | `http://vllm:8000`         | the `--served-model-name` |
| SGLang    | `http://127.0.0.1:8002`        | `http://sglang:30000`      | the `--served-model-name` |

Inside a container `base_url` is never `127.0.0.1` — that is the gateway's own loopback.
To reach a server on the Docker host use `http://host.docker.internal:8080`.

For vLLM and SGLang, `backend.model` must match `--served-model-name`; leave it `null` to
auto-detect from `/v1/models`.

!!! warning "Compose templates that are not shipped"

    Only `docker-compose.yml` (gateway alone) and `docker-compose.llamacpp.yml`
    (gateway + llama-server) exist in the repository. For vLLM and SGLang, start the
    inference server yourself and point `base_url` at it.

## Reading the top-N window

Every returned token is mapped onto a candidate letter. `"A"` and `" A"` both count as
`A`; the most likely variant wins. A candidate outside the top-N window is floored at the
least likely logprob actually returned, and the question is flagged `truncated` in the
diagnostics.

If `truncated: true` shows up, raise `backend.top_logprobs` (up to 4096) and re-measure. A
floored candidate is a *lower bound*, not an estimate — a threshold built on top of a
floored probability is not trustworthy.

!!! note "Logprob conventions"

    The gateway renormalises over the candidates only; the rest of the vocabulary's mass
    is discarded. Logprob scales differ between servers (some report natural log, some
    report log10), but softmax is scale-invariant in the sense that a *monotone* transform
    of all logprobs would change the result. In practice servers agree on natural log and
    the distributions are comparable — which is exactly what
    [Comparing two backends](#comparing-two-backends) measures.

## `supports_images`

`backend.supports_images: null` means "auto": assume the backend accepts images and never
advertise them if they are absent. Set it to `false` when the model has no vision tower,
so image requests fail fast instead of reaching the backend.

## Reasoning models

A *reasoning* model (Qwen3 / Qwen3.5, DeepSeek-R1, OpenAI o-series, …) emits its thinking
as the first token, so no candidate letter lands in the top-N window and every question
fails with `BACKEND_PROTOCOL_ERROR`. The gateway does not guess the fix; set
`backend.extra_body` to the switch your server understands:

| backend                           | `backend.extra_body`                             |
| --------------------------------- | ------------------------------------------------ |
| llama.cpp / vLLM / SGLang (Qwen3) | `chat_template_kwargs: {enable_thinking: false}` |
| OpenAI o-series / GPT-5           | `reasoning_effort: none`                         |
| OpenRouter, `openrouter/qwen/*`   | `reasoning: {effort: "none"}`                    |
| DeepSeek `deepseek-reasoner`      | *(no off switch — use `deepseek-chat` instead)*  |

!!! danger "OpenRouter's `reasoning.enabled: false` does nothing"

    `reasoning: {enabled: false}` is accepted without error but has **no effect** — the
    worst kind of failure, because the configuration looks completely correct while
    thinking carries on regardless. Measured against
    `openrouter/qwen/qwen3.8-27b`, only `reasoning: {effort: "none"}` actually turns
    thinking off.

    If you are unsure which spelling your server honours, **send both**: unknown keys are
    silently ignored, the one it knows takes effect.

    ```bash
    JEV_BACKEND_EXTRA_BODY='{"chat_template_kwargs": {"enable_thinking": false}, "reasoning": {"effort": "none"}}'
    ```

In YAML:

```yaml
backend:
  base_url: http://127.0.0.1:8080
  extra_body:
    chat_template_kwargs:
      enable_thinking: false
```

`extra_body` is merged into every request but can never override `messages`, `max_tokens`,
`logprobs` or `top_logprobs` — the contract fields always win, so a mistyped
`extra_body` cannot silently break the decision path. Leave it `{}` for a non-reasoning
model.

## Silent failure: `HTTP 200` with `logprobs: null` {#silent-logprobs-null}

**This is the failure that stalls people the longest when swapping backends.** The request
returns `200`, the gateway reports
`BACKEND_PROTOCOL_ERROR: backend response contained no logprobs object`, and the backend
log contains no error at all. In almost every case it is one of the two causes below, and
**neither of them raises an error**.

### 1. Thinking was not disabled, so the single token was spent on it

`max_tokens: 1` allows the model to emit a single token. With thinking left on, that one
token is a thinking fragment and the response looks like this:

```json
{"message": {"content": "", "reasoning": "We",
             "reasoning_details": [{"type": "reasoning.text", "text": "We"}]},
 "finish_reason": "length", "logprobs": null}
```

The test is trivial: **a `reasoning` or `reasoning_details` field in the response means
thinking is still on.** Fix `extra_body` per
[Reasoning models](#reasoning-models).

### 2. `top_logprobs` exceeded the server's limit

The OpenAI spec caps `top_logprobs` at **20**, while the gateway's default is **128**. Past
the cap the backend does **not** return 400 — it simply turns `logprobs` into `null`, which
is the same symptom as the case above.

Measured on `openrouter/qwen/qwen3.8-27b` at `192.168.1.200:8080`:

| `top_logprobs` | 5  | 20 | 24 | 32 | 64 | 128 |
| -------------- | -- | -- | -- | -- | -- | --- |
| `logprobs`      | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |

The cap **varies by backend** and must not be assumed. `openrouter/qwen/qwen3.7-flash` on the
same server caps out at **5**.

!!! warning "A healthy probe is not a healthy gateway"

    Hand-written `curl` probes tend to use `top_logprobs: 20`, which works perfectly; the
    gateway uses the default 128 and therefore fails on every request. Both produce the
    same symptom, so it is easy to conclude "the backend is fine". **Test with the
    gateway's default window** when moving to a new backend.

### Diagnostic order

```bash
curl -s http://<backend>/v1/chat/completions \
  -H "content-type: application/json" \
  -H "authorization: Bearer $KEY" \
  -d '{"model":"<model id>","messages":[{"role":"user","content":"Reply with the single letter A."}],
       "max_tokens":1,"temperature":0,"logprobs":true,"top_logprobs":128,
       "reasoning":{"effort":"none"}}'
```

Check three things:

1. Is there a `reasoning` field in the response? Then thinking is not off.
2. Is `logprobs` `null` and `content` an empty array? Keep going.
3. Try `top_logprobs` at 20, 24, 32, 64, 128 — find the largest value that works and put
   it in `JEV_BACKEND_TOP_LOGPROBS`.

!!! tip "Other protocol errors"

    A `BACKEND_PROTOCOL_ERROR` on every question can also mean the server never returns
    `logprobs.content[0].top_logprobs`, or that the model's chat template never emits a
    bare letter. Check the raw response from `POST /v1/chat/completions` with `curl`
    before changing any gateway setting.

## Comparing two backends

Different servers, different quantisations and different `top_logprobs` values produce
different probability vectors even when the argmax agrees. To measure this, point the same
gateway at each `JEV_BACKEND_BASE_URL` / `JEV_BACKEND_TOP_LOGPROBS` pair, run one round
each, and keep the `answers.<q>.logprobs` vectors:

```bash
# backend A
curl -s http://127.0.0.1:8000/v1/systemone -H 'content-type: application/json' -d @case.json

# backend B: change JEV_BACKEND_BASE_URL / JEV_BACKEND_TOP_LOGPROBS, restart, run again
curl -s http://127.0.0.1:8000/v1/systemone -H 'content-type: application/json' -d @case.json
```

Then look at three things per question:

* **argmax agreement** — did the *ranking* survive?
* **mean|dp|** — how far the probability vectors moved.
* **Brier distance** — the mean squared gap between them. This is the number to watch:
  high argmax agreement with a large Brier distance means a quantisation or backend change
  quietly broke your thresholds.
