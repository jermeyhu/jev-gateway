# Limits & FAQ

## Known limits

* The `noul` probability is a softmax over the two candidate letters only; calibrate
  thresholds against your own labelled set.
* A question carries at most **16 candidates**; more is rejected with `INVALID_REQUEST`.
* `truncated: true` in diagnostics means a candidate was floored; raise `top_logprobs`.
* Images extend the reference contract; clients written against the hosted service still
  work but ignore the field.
* Scale horizontally behind a load balancer rather than raising `max_concurrency` beyond
  the backend's real capacity.

## Calibration

Decision models are **calibration sensitive**. Threshold gating — `if p < 0.7: ask a
human` — only works if the probabilities are honest, and aggressive quantisation can
preserve the argmax while destroying the confidence.

Prefer **Q8_0 or F16** for gate models. Treat Q4_K_M as an experiment, and measure it with
[`compare_backends.py`](scripts.md#comparing-two-backends) rather than assuming.

Two habits keep a threshold trustworthy:

1. Re-run `compare_backends.py` after every model swap, quantisation change or server
   change, and watch the Brier distance rather than the argmax agreement.
2. Keep a small labelled set from your own traffic and check the score the gateway assigns
   against it. The hosted API's calibration does not transfer to a 4B model on your
   hardware without re-measurement.

## FAQ

### Why is the direct tool-calling path slower?

Both paths were measured on the same evidence and rubric (Qwen3.5-0.8B on llama.cpp,
single-threaded, 6 SRE triage cases × 3 runs, thinking disabled on both paths):

| metric (per case = 3 decisions) | gateway (logprobs) | direct tool call |
| ------------------------------- | ------------------ | ---------------- |
| latency, warm prompt cache      | **421 ms**         | 4547 ms          |
| latency, cold cache (first run) | 4742 ms            | 4258 ms          |
| output tokens                   | **3** (fixed)      | ~55              |
| input tokens (18 cases, total)  | 10 881             | 11 661           |
| accuracy, precise rubric        | 3/4                | 3/4              |

Generating a tool-call JSON costs ~18 decode steps per decision; the gateway decodes
exactly one token. The cold-cache row is the honest counterpoint: the first request pays
the prefill either way, so the win shows up on every request after it. Use the direct path
only when the model must reason in free text before deciding.

### Every question fails with `BACKEND_PROTOCOL_ERROR`. Why?

Almost always a reasoning model emitting its thinking as the first token, so no candidate
letter lands in the top-N window. Set `backend.extra_body` to your server's off switch —
see [Reasoning models](backends.md#reasoning-models). The other two causes are a server
that does not return `logprobs.content[0].top_logprobs`, and a chat template that never
produces a bare letter. Check the raw response with `curl` before changing any setting.

### Can I send 17 options?

No. 2 – 16 candidates per question; 17 is rejected with `INVALID_REQUEST`. Letters run
`A`…`P`.

### Do I get partial answers if one question fails?

No. Any failing question fails the whole request, and the first error in question order is
the one reported. This matches the reference implementation — a decision set is only
meaningful as a whole.

### `truncated: true` — what now?

A candidate letter fell outside the `top_logprobs` window and was floored at the least
likely logprob actually returned. That is a lower bound, not an estimate. Raise
`backend.top_logprobs` (16 – 4096) and re-measure; if the server refuses a large window,
reduce the candidate count instead.

### Do the questions really run in parallel?

Yes, bounded by `backend.max_concurrency` (default 32). The response preserves the
request's question order regardless of completion order, and `request.total_timeout_seconds`
bounds the whole request rather than each question.

### How do I know the probabilities are not made up?

Run `diagnose_severity.py` and `probe_position_bias.py`. If reordering the candidates
changes only which *letter* wins and the probability mass tracks the evidence, the
distribution is reflecting the model's judgement. If the *label* flips with position, the
model is reading position rather than meaning and the numbers should not be thresholded.

### Can I use it with a reasoning model?

Yes, with thinking disabled via `backend.extra_body` — the recipes per server are in
[Reasoning models](backends.md#reasoning-models). DeepSeek's `deepseek-reasoner` has no
off switch; use `deepseek-chat`.

### Which model size is enough?

0.6B – 4B is the range where this pattern pays off, because one forward pass is the whole
cost. A larger model is not automatically a better *classifier* here — the benchmark's
biggest wins came from rewording the criteria, not from scaling the model.

### Why does the gateway not stream?

There is nothing to stream. A decision decodes exactly one token, so a streaming response
would add complexity and no information. `stream: false` is always sent.

### Is the prompt sent to the backend readable?

Yes, and that is intentional — the whole design is auditable. `fused` is byte-compatible
with the reference gateway, and the system prompt is a single constant. Use
`request.prompt_layout: split` when you want the cacheable prefix instead of the
byte-compatibility.

### How do I scale it?

Run more gateway instances behind a load balancer. Raising `max_concurrency` past the
backend's real capacity just moves the queue from your process into the inference server,
where it is harder to observe.

## License

Apache-2.0. The request/response contract and the logprob-classification idea are modelled
on [David-Lolly/Jev-Compatible](https://github.com/David-Lolly/Jev-Compatible).
