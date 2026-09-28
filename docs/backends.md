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
    [`compare_backends.py`](scripts.md#comparing-two-backends) measures.

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
| OpenRouter (any reasoning model)  | `reasoning: {enabled: false}`                    |
| DeepSeek `deepseek-reasoner`      | *(no off switch — use `deepseek-chat` instead)*  |

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

!!! tip "Diagnosing a protocol error"

    A `BACKEND_PROTOCOL_ERROR` on every question is almost always one of three things:
    thinking not disabled on a reasoning model, a server that does not return
    `logprobs.content[0].top_logprobs`, or a model that never emits a bare letter because
    its chat template wraps answers. Check the raw response from
    `POST /v1/chat/completions` with `curl` before changing any gateway setting.

## Comparing two backends

Different servers, different quantisations and different `top_logprobs` values produce
different probability vectors even when the argmax agrees. The script that measures this:

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

See [Scripts](scripts.md#comparing-two-backends) for the full script list.
