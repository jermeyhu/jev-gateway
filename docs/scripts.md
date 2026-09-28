# Scripts

Every script lives in `scripts/`, takes `--help`, and talks to a gateway over HTTP — none
of them import the application. That keeps them usable against a container, a remote box or
a colleague's machine.

```bash
python scripts/<name>.py --help
```

## The list

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

## Smoke test

The first thing to run after starting the gateway.

```bash
python scripts/smoke_test.py --url http://127.0.0.1:8000
python scripts/smoke_test.py --url http://127.0.0.1:8000 --image screenshot.png
python scripts/smoke_test.py --url http://127.0.0.1:8000 --json
```

It hits `/healthz`, `/readyz` and `/v1/systemone`, validates that every answer has the
correct shape, and prints the usage and diagnostics blocks. `--json` emits machine-readable
output for CI.

## Comparing two backends

The most useful script in the repository. It runs the same case set against several
servers and reports what actually matters:

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

`--backend` takes `NAME=URL[+key:value]`, where the optional values are `model`, `api_key`,
`top_logprobs`, `supports_images`. A `truncated: true` in any observation is reported as a
warning, because a floored probability is a lower bound rather than an estimate.

## Accuracy: gateway vs direct tool calling

```bash
python scripts/bench_triage.py --url http://127.0.0.1:8000 --repeat 3
```

Six SRE triage cases with ground truth, run through both paths. It reports accuracy,
latency and token counts side by side. On the reference benchmark (Qwen3.5-0.8B on
llama.cpp, single-threaded, thinking disabled on both paths) both paths scored 3/4 with a
precise rubric; the gateway emitted 3 output tokens against ~55 for the direct path.

## Speed and tokens

```bash
python scripts/bench_speed_tokens.py --url http://127.0.0.1:8000 --repeat 3
```

Warms both caches, then reports p50 / p95 / mean / min / max latency for the gateway and
for the direct path, plus the speed ratio and the output-token ratio.

## Diagnosing the rubric

When accuracy is poor, the question is usually *the wording*, not the gateway.

```bash
python scripts/diagnose_severity.py --url http://127.0.0.1:8000
```

Runs a vague rubric (`Total outage / Degraded / Minor`) against a precise one (thresholds
decidable from the state) on both the gateway and the direct path, and includes two
trivially-green cases. On the reference benchmark the vague rubric pushed both paths near
chance — the direct path even answered a trivially-green case `sev1` — while the precise
rubric fixed both at once. If this script shows a large gap, rewrite the criteria before
touching any gateway setting.

## Position bias

```bash
python scripts/probe_position_bias.py --url http://127.0.0.1:8000
```

Asks the same question with the candidates in three different orders. If reordering changes
which *letter* wins but not which *label* wins, the model is judging semantics and the
distribution can be trusted. If the *label* flips, the model is reading position rather
than meaning.

## Boundary probes

```bash
python scripts/probe_live.py --url http://127.0.0.1:8000
```

Checks the documented limits against a live gateway: candidate counts at 2, 16 and 17,
image limits, the unknown-key rejection, and the error codes. Useful after changing
`max_questions` or the `multimodal` block.

## Making a test image

```bash
python scripts/make_test_png.py
```

Writes a tiny valid PNG using only the standard library, so vision paths can be tested
without Pillow or a sample image lying around.
