# How it works

## A decision is a classification

A Jev call is a *classification* problem, not a generation problem. For every question the
gateway:

1. Renders the evidence (the `state`) plus lettered options (`A`, `B`, `C`, …).
2. Asks the backend for **exactly one** next token with logprobs
   (`max_tokens: 1`, `logprobs: true`, `top_logprobs: N`).
3. Reads each candidate letter's logprob out of
   `choices[0].logprobs.content[0].top_logprobs`.
4. Softmaxes the candidate letters into a probability distribution.

No text is generated, so a decision costs one forward pass over the prompt. That is what
makes a small local model (0.6B–4B) viable as a router, a gate and a triage classifier.

The full distribution is kept, not just the argmax. For `noul` it *is* the answer; for
`choice` it is returned as `probabilities`; for `score` it is collapsed to a
probability-weighted mean so the answer interpolates between levels.

## The three question types

Each question type maps a set of candidates onto the same letter machinery.

| type     | candidates                                   | answer                                                            |
| -------- | -------------------------------------------- | ----------------------------------------------------------------- |
| `noul`   | `criteria.true` / `criteria.false`           | probability of `true` (defaults to `Yes` / `No`)                  |
| `choice` | the keys of `criteria`, in insertion order   | the argmax key plus the full distribution                          |
| `score`  | the items of `criteria`, as levels `0..n-1`  | the probability-weighted mean of the level indices, plus `legend`  |

Letters are assigned in order and run past `Z`: `A`…`P` for up to 16 candidates. Ties in
`choice` resolve to the **first** candidate, so the answer is deterministic.

## Concurrency and failure semantics

All questions in a request run **concurrently**, bounded by
`backend.max_concurrency`. The response preserves the request's question order regardless
of completion order.

* **Any** failing question fails the whole request — there is no partial answer.
* The first error in question order is the one reported.
* `request.total_timeout_seconds` bounds the **whole** request, not each question.
* `backend.timeout_seconds` bounds a single question.

## Prompt layouts

The layout is chosen by `request.prompt_layout`.

### `fused`

One user message containing `{evidence, criterion, options}` as compact JSON. The
messages are `system`, `user` — two in total.

=== "fused"

    ```json
    {"evidence":"red green blue","criterion":"pick a colour","options":[{"letter":"A","description":"the colour red"},{"letter":"B","description":"the colour green"}]}
    ```

=== "split"

    ```json
    {"evidence":"red green blue"}
    ```

    ```json
    {"criterion":"pick a colour","options":[{"letter":"A","description":"the colour red"},{"letter":"B","description":"the colour green"}]}
    ```

* `fused` is byte-compatible with the reference gateway, so calibration measured against
  the hosted API transfers.
* `split` puts the evidence (and any images) in the first user message and the criterion
  and options in a second. The first message is identical for every question in the
  request, so llama.cpp reuses the vision encoder work and the state prefill. Use it when
  latency matters more than byte-compatibility.

## Reading the logprobs

The gateway maps every returned token onto a candidate letter. `"A"` and `" A"` both count
as `A`; the most likely variant wins. A candidate that falls outside the top-N window is
floored at the least likely logprob actually returned, and the question is flagged
`truncated` in the diagnostics — raise `backend.top_logprobs` (up to 4096) if you see that
flag.

The probabilities are renormalised over the candidates only. The rest of the vocabulary's
mass is discarded, so a candidate that is unlikely *in absolute terms* can still hold a
high share of the *relative* distribution.

## Why not just call a tool

Asking the model to emit a tool call and reading the arguments is the obvious alternative,
and it works. It also has to *generate* the call: a whole tool-call JSON per question,
measured at 42–48 output tokens. The gateway decodes exactly one token.

Across 10 business cases **the gateway is faster and more accurate on both backends**: 1.95×
at 27B, 8.9× at 0.8B. The ~1 second at 27B is a fixed cost of going out through the proxy
that both paths pay, so it cancels in the difference — but it makes the absolute number
unreadable and dilutes the ratio. The 0.8B has no such floor, so 8.9× is the honest reading of
the protocol's efficiency. Choose the direct path only when the model must reason in free
text before deciding. See
[Limits & FAQ](limits#why-is-the-direct-tool-calling-path-slower).

## The system prompt

One fixed system message tells the backend to answer with a single letter. It is a
constant in the code and is identical for all question types and layouts — the criterion
and the options travel in the user message, so the system prompt stays cacheable.
