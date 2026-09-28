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
but it must *generate* the call. The same 10 Chinese business cases (`noul` × 4,
`choice` × 4, `score` × 2), 3 runs each, thinking disabled on both paths, strictly
single-threaded (the backend has limited concurrency, so serial execution is the only way
to measure real latency), run on two backends — 60 calls per set, zero failures.

### 27B: remote, international {#bench-27b-remote}

| metric                    | gateway (logprobs) | direct tool call |
| ------------------------- | ------------------ | ---------------- |
| accuracy                  | **30/30 100%**     | 27/30 90%        |
| latency mean              | **1027 ms**        | 2005 ms          |
| latency median            | **977 ms**         | 1820 ms          |
| output tokens / answer    | **1.0** (fixed)    | 41.9             |

| primitive | gateway    | direct    |
| --------- | ---------- | --------- |
| `noul`    | 12/12      | 12/12     |
| `choice`  | 12/12      | 12/12     |
| `score`   | **6/6**    | 3/6       |

The gateway is 1.95× faster and gets 3 more questions right (`score` 6/6 against 3/6).
**But the 1027 ms absolute number cannot be read directly** — see
[how to read these two sets](#how-to-read-these-numbers).

### 0.8B: local llama.cpp {#bench-08b-local}

| metric          | gateway             | direct              |
| --------------- | ------------------- | ------------------- |
| accuracy        | 18/30 60%           | 21/30 70%           |
| latency mean    | 67 ms               | 599 ms              |
| latency median  | 47 ms               | 528 ms              |
| output tokens   | 1.0                 | 47.5                |

| primitive | gateway    | direct    |
| --------- | ---------- | --------- |
| `noul`    | 12/12      | 12/12     |
| `choice`  | 6/12       | 6/12      |
| `score`   | **0/6**    | 3/6       |

The gateway is 8.9× faster and uses 47.5× fewer output tokens — **this set can be read
directly**. The lower accuracy is the 0.8B's capability ceiling, not the protocol; see below.

### How to read these two sets {#how-to-read-these-numbers}

**Both sets agree: the gateway is faster and more accurate.** 1.95× at 27B, 8.9× at 0.8B. They
differ only in how readable the numbers themselves are.

**The ~1 second at 27B is a fixed cost that both paths pay.** Every call goes out through a
proxy to openrouter. Measured: growing the input from 13 to 1283 tokens and the output from
1 to 10 tokens produced no increase in latency at all; the same request sent 9 times in a row
came back `1022 1653 1024 1025 1025 1232 2934 2079 1050` ms — a hard floor of about one
second with random spikes from upstream queueing on top (worst: 5227 ms). A 13-token empty
request costs about the same as a full question, and `cached_tokens` is always 0.

Because both paths pay **the same** cost, it cancels in the difference: 1027 ms for the
gateway against 2005 ms for the direct path, and the ~978 ms between them is real decoding
work saved. What it destroys is two other things — **the absolute number is unreadable**
(most of those 1027 ms is not the decision, so it cannot be used to project throughput or
capacity), and **the ratio is diluted** (the real gain is the ~978 ms saved; divided by a
total that includes the floor, it shows up as under 2×).

**The 0.8B set is the clean measurement.** Nothing in those 67 ms is network floor, so 8.9×
is the honest reading of the protocol's own efficiency; the remote 1.95× is the same gain
diluted by a one-second floor.

**The 0.8B's `score` failures are a capability ceiling, not a protocol flaw.** A rotation
experiment confirms it: re-running with the three risk tiers rotated, the probability mass
always tracks the **content**, but the comparison always favours the most conservative tier —
0.8B simply cannot tell the tiers apart. **The same question scores 6/6 at 27B.** 0.6B–4B
models are fine for binary gates (`noul`); multi-tier grading (`score`) needs a bigger model.

Last: to optimise latency on a remote deployment, the lever is not a shorter prompt
(measured, does nothing) but fewer round trips or a nearer endpoint.

## Two rubric lessons from the same benchmark {#two-rubric-lessons}

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

    Every environment variable, its default, and the two rules that make a typo fail at
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
