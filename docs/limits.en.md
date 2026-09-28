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

Prefer **Q8_0 or F16** for gate models. Treat Q4_K_M as an experiment, and measure it
using the method in [Comparing two backends](backends#comparing-two-backends) rather than
assuming.

Two habits keep a threshold trustworthy:

1. Re-run the comparison after every model swap, quantisation change or server
   change, and watch the Brier distance rather than the argmax agreement.
2. Keep a small labelled set from your own traffic and check the score the gateway assigns
   against it. The hosted API's calibration does not transfer to a 4B model on your
   hardware without re-measurement.

## FAQ

### Why is the direct tool-calling path slower?

Because it has to *generate* content: a whole tool-call JSON per question. The same 10
cases (`noul` × 4, `choice` × 4, `score` × 2) × 3 runs, thinking disabled on both paths,
strictly single-threaded:

| backend                          | path    | accuracy  | latency mean | latency median | output tok |
| -------------------------------- | ------- | --------- | ------------ | -------------- | ---------- |
| 27B (remote, international)      | gateway | **30/30** | 1027 ms      | 977 ms         | **1.0**   |
|                                  | direct  | 27/30     | 2005 ms      | 1820 ms        | 41.9      |
| 0.8B (local llama.cpp, cached)   | gateway | 18/30     | 67 ms        | 47 ms          | **1.0**   |
|                                  | direct  | 21/30     | 599 ms       | 528 ms         | 47.5      |

Both paths **pay the same cross-border fixed cost**, so it cancels in the difference: 1027 ms
for the gateway against 2005 ms for the direct path, and the ~978 ms between them is real
decoding work saved. **On the remote the gateway is genuinely faster and more accurate**
(30/30 against 27/30, 1.95× faster). What the fixed cost destroys is two other things: **the
absolute number is unreadable** (most of those 1027 ms is not the decision, so it cannot be
used to project throughput), and **the ratio is diluted** (the real gain is the ~978 ms saved;
divided by a total that includes the floor it shows up as under 2×). Measured: 100× the input
and 10× the output changed nothing. To optimise latency on a remote deployment, the lever is
not a shorter prompt but fewer round trips or a nearer endpoint.

**The local 0.8B is the clean measurement.** A call takes 67 ms with no network floor, and
**the gateway is 8.9× faster and uses 47.5× fewer output tokens** — the honest reading of the
protocol's own efficiency, where the remote 1.95× is the same gain diluted by a one-second
floor. But its 60% / 70% accuracy reflects the capability ceiling of a 0.8B, not the protocol
(the same question scores 6/6 through the gateway at 27B).

### Every question fails with `BACKEND_PROTOCOL_ERROR`. Why?

### Every question fails with `BACKEND_PROTOCOL_ERROR`. Why?

The two most common causes both **fail silently** — the backend returns `HTTP 200`, logs no
error, and only `logprobs` comes back `null`:

1. A reasoning model that was not told to stop thinking, so its thinking consumed the one
   token `max_tokens: 1` allows (a `reasoning` field shows up in the response);
2. `top_logprobs` above the server's cap (the default is 128, while the OpenAI spec allows
   at most 20).

The two-step diagnosis and the fixes are in
[Silent failure](backends#silent-logprobs-null). The third cause is a server that never
returns `logprobs.content[0].top_logprobs`, or a chat template that never produces a bare
letter. Check the raw response with `curl` before changing any setting.

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
`backend.top_logprobs` (16 – 4096) and re-measure.

Before raising it, read [Silent failure](backends#silent-logprobs-null): the server has its
own cap and going past it does not error, it just turns `logprobs` into `null`. Probe with
the window the gateway is currently using to confirm the server accepts it at all.

### Do the questions really run in parallel?

Yes, bounded by `backend.max_concurrency` (default 32). The response preserves the
request's question order regardless of completion order, and `request.total_timeout_seconds`
bounds the whole request rather than each question.

### How do I know the probabilities are not made up?

Build the controls yourself: one case with a precise criterion and one with a vague one,
then re-run both with the candidate order shuffled. If reordering the candidates
changes only which *letter* wins and the probability mass tracks the evidence, the
distribution is reflecting the model's judgement. If the *label* flips with position, the
model is reading position rather than meaning and the numbers should not be thresholded.

### Can I use it with a reasoning model?

Yes, with thinking disabled via `backend.extra_body` — the recipes per server are in
[Reasoning models](backends#reasoning-models). DeepSeek's `deepseek-reasoner` has no
off switch; use `deepseek-chat`.

### Which model size is enough?

0.6B – 4B is fine for **binary gates**. On the same cases a 0.8B scores 12/12 on `noul` but
0/6 on `score` — a rotation experiment shows the model cannot tell the tiers apart at all
and simply pushes probability toward the most conservative one, while the same question
scores 6/6 at 27B. The benchmark's biggest wins came from rewording the criteria rather
than scaling the model, but **multi-tier grading does need a bigger model**. To see what the
protocol itself can do, read the remote 27B set; the 0.8B's low accuracy is just its ceiling.

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
