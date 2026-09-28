# jev-gateway

[![Documentation](https://img.shields.io/badge/docs-jermeyhu.github.io%2Fjev--gateway-3f51b5?logo=material%2Ffor-linux)](https://jermeyhu.github.io/jev-gateway/)
[![License](https://img.shields.io/badge/license-Apache--2.0-3f51b5)](LICENSE)

> Full documentation: **[jermeyhu.github.io/jev-gateway](https://jermeyhu.github.io/jev-gateway/)**
> — also in [简体中文](https://jermeyhu.github.io/jev-gateway/zh/)

## 1. What this is

A self-hosted, **Jev / System One compatible** decision gateway. It exposes the single
`POST /v1/systemone` endpoint you already program against, but runs the decision layer on
your own inference server instead of the closed TypeSafe API — and extends the contract
with local **image input**.

The official Jev endpoint is text only, and its Pydantic AI client rejects file parts
outright, so screenshots and camera frames could not be scored at all. This gateway is the
drop-in replacement for the text path, and the same process can additionally score evidence
that includes images.

The gateway speaks exactly one wire protocol: the **OpenAI chat-completions API**
(`POST /v1/chat/completions` with `logprobs`). Any server that implements it works —
llama.cpp, vLLM, SGLang, Ollama, LM Studio, TGI, a hosted API — and no engine-specific
option is ever sent.

## 2. Quick start

```bash
pip install -e ".[dev]"
python -m app                              # listens on 0.0.0.0:8000
python scripts/smoke_test.py --url http://127.0.0.1:8000
```

Point it at a backend by editing `config.yaml` — `backend.type` is always `openai`, only
`backend.base_url` changes:

| server    | `base_url` on the host | inside compose            | `backend.model`           |
| --------- | ---------------------- | ------------------------- | ------------------------- |
| llama.cpp | `http://127.0.0.1:8080` | `http://llama-server:8080` | `null` (auto-detected)   |
| vLLM      | `http://127.0.0.1:8001` | `http://vllm:8000`         | the `--served-model-name` |
| SGLang    | `http://127.0.0.1:8002` | `http://sglang:30000`      | the `--served-model-name` |

Or with Docker:

```bash
docker compose up -d                                   # gateway alone
docker compose -f docker-compose.llamacpp.yml up -d    # gateway + llama-server
```

`JEV_GATEWAY_CONFIG` is the only environment variable read; it defaults to `./config.yaml`.
To reach vLLM or SGLang, copy the compose file and change `base_url` and `backend.model` —
only the llama.cpp stack ships with the repository.

## 3. How it works

A Jev call is a *classification* problem, not a generation problem. For every question the
gateway renders the evidence plus lettered options (`A`, `B`, `C`, …), asks the backend for
**exactly one** next token with logprobs (`max_tokens: 1`), reads each candidate letter's
logprob, and softmaxes the candidates into a probability distribution. No text is generated,
so a decision costs one forward pass over the prompt. That is what makes a small local
model (0.6B–4B) viable as a router, a gate or a triage classifier.

Measured against direct tool calling on the same evidence and rubric (Qwen3.5-0.8B on
llama.cpp, single-threaded, 6 SRE triage cases × 3 runs, thinking disabled on both paths):

| metric (per case = 3 decisions) | gateway (logprobs) | direct tool call |
| ------------------------------- | ------------------ | ---------------- |
| latency, warm prompt cache      | **421 ms**         | 4547 ms          |
| output tokens                   | **3** (fixed)      | ~55              |
| accuracy, precise rubric        | 3/4                | 3/4              |

With the prompt cache warm the gateway is **~11× faster** and emits **~18× fewer output
tokens** at the same accuracy. Generating a tool-call JSON costs ~18 decode steps per
decision; the gateway decodes exactly one token. Use the direct path only when the model
must reason in free text before deciding.

Two lessons worth keeping:

* **Write decidable criteria.** With vague labels ("Total outage / Degraded / Minor") both
  paths scored near chance. Restating each candidate with thresholds decidable from the
  state ("error_rate ≥ 0.20, OR a region down, …") fixed both paths at once, so the
  bottleneck was the wording, not the gateway.
* **The softmax is honest.** Reordering candidates changes which *letter* wins but not which
  *label* wins, and the probability mass tracks the evidence (a global outage scores
  `sev1 ≈ 0.85`). Trust the distribution; calibrate thresholds on your own labelled set.

A *reasoning* model (Qwen3 / Qwen3.5, DeepSeek-R1, OpenAI o-series, …) emits its thinking as
the first token, so no candidate letter lands in the top-N window and every question fails
with `BACKEND_PROTOCOL_ERROR`. Turn thinking off through `backend.extra_body` — see the
[backends page](https://jermeyhu.github.io/jev-gateway/backends/).

## Documentation

The full reference lives at **[jermeyhu.github.io/jev-gateway](https://jermeyhu.github.io/jev-gateway/)**
(简体中文: [/zh/](https://jermeyhu.github.io/jev-gateway/zh/)), built from `docs/` with MkDocs
Material: quick start, how it works, API reference, configuration, backends, images, scripts,
limits & FAQ, and development.

## Acknowledgements

The request/response contract and the logprob-classification idea are modelled on
[David-Lolly/Jev-Compatible](https://github.com/David-Lolly/Jev-Compatible).

## License

[Apache-2.0](LICENSE).
