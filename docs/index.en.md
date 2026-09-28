---
title: Home
hide:
  - toc
---

# Jev Gateway

<div class="jev-hero" markdown>

A self-hosted, **Jev / System One compatible** decision gateway. It exposes the single
`POST /v1/systemone` endpoint you already program against, but runs the decision layer on
your own inference server instead of the closed TypeSafe API — and extends the contract
with local **image input**.

The gateway speaks exactly one wire protocol: the **OpenAI chat-completions API**
(`POST /v1/chat/completions` with `logprobs`). Any server that implements it works —
llama.cpp, vLLM, SGLang, Ollama, LM Studio, TGI, a hosted API — and no engine-specific
option is ever sent.

</div>

<div class="jev-actions" markdown>

[Get started :material-arrow-right:](quick-start){ .md-button .md-button--primary }
[View the API reference](api){ .md-button }
[Source on GitHub](https://github.com/jermeyhu/jev-gateway){ .md-button }

</div>

## Why it exists

The official Jev endpoint is text only (`No image, audio, or video input`), and its
Pydantic AI client raises `UserError: Files are not supported by this model`. This gateway
is the drop-in replacement for the text path, and the same process can additionally score
evidence that includes screenshots or camera frames.

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

## Two rubric lessons from the same benchmark

**Write decidable criteria.** With vague labels (`Total outage / Degraded / Minor`) both
paths scored near chance — the direct path even answered a trivially-green case `sev1`.
Restating each candidate with thresholds decidable from the state (`error_rate ≥ 0.20, OR a
region down, …`) fixed both paths at once, so the bottleneck was the wording, not the
gateway.

**The softmax is honest.** Reordering the candidates changes which *letter* wins but not
which *label* wins, and the probability mass tracks the evidence (a global outage scores
`sev1 ≈ 0.85`). Trust the distribution; calibrate thresholds on your own labelled set.

## At a glance

| | |
| --- | --- |
| Endpoints | `POST /v1/systemone`, `GET /healthz`, `GET /readyz`, `GET /v1/models` |
| Protocols | OpenAI chat-completions with `logprobs` |
| Question types | `noul`, `choice`, `score` |
| Candidates per question | 2 – 16 |
| Questions per request | 1 – 64 |
| Images | data URLs, opt-in remote URLs, raw base64 |
| Concurrency | questions run in parallel, bounded by `backend.max_concurrency` |
| License | Apache-2.0 |

## Where to next

<div class="grid cards" markdown>

-   :material-rocket-launch: **Quick start**

    ---

    Install, configure and run the gateway against a local llama.cpp, vLLM or SGLang
    server, with a smoke test you can paste into your terminal.

    [:octicons-arrow-right-24: Get started](quick-start)

-   :material-function-variant: **How it works**

    ---

    The classification trick, the two prompt layouts, the logprob parsing rules, and why
    one forward pass is enough.

    [:octicons-arrow-right-24: Understand the design](how-it-works)

-   :material-api: **API reference**

    ---

    Request and response shapes for `POST /v1/systemone`, the health endpoints, the error
    table and the request-id contract.

    [:octicons-arrow-right-24: Read the API](api)

-   :material-tune: **Configuration**

    ---

    Every key in `config.yaml`, its default, and the two rules that make a typo fail at
    startup instead of silently.

    [:octicons-arrow-right-24: Configure it](configuration)

-   :material-server-network: **Backends**

    ---

    Point the gateway at any OpenAI-compatible server, and the `extra_body` recipe that
    switches thinking off in reasoning models.

    [:octicons-arrow-right-24: Connect a backend](backends)

-   :material-image-multiple: **Images**

    ---

    The accepted image forms, the limits, and why images are attached to the front of the
    first user message.

    [:octicons-arrow-right-24: Add images](images)

-   :material-flask-outline: **Scripts**

    ---

    Smoke tests, A/B backend comparison, triage accuracy, speed and token benchmarks, and
    the probes that separate a wording problem from a design problem.

    [:octicons-arrow-right-24: Run the scripts](scripts)

-   :material-alert-circle-outline: **Limits & FAQ**

    ---

    The known limits, the calibration caveats, and answers to the questions that come up
    before you trust a probability.

    [:octicons-arrow-right-24: Know the limits](limits)

</div>

## Acknowledgements

The request/response contract and the logprob-classification idea are modelled on
[David-Lolly/Jev-Compatible](https://github.com/David-Lolly/Jev-Compatible) — thanks for
the reference implementation this gateway stays compatible with.
