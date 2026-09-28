# jev-gateway

[![Documentation](https://img.shields.io/badge/docs-jermeyhu.github.io%2Fjev--gateway-3f51b5?logo=material%2Ffor-linux)](https://jermeyhu.github.io/jev-gateway/)
[![License](https://img.shields.io/badge/license-Apache--2.0-3f51b5)](LICENSE)

English · [简体中文](https://github.com/jermeyhu/jev-gateway/blob/main/README.md)

> Full documentation: **[jermeyhu.github.io/jev-gateway](https://jermeyhu.github.io/jev-gateway/)**
> — also in [简体中文](https://jermeyhu.github.io/jev-gateway/)

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
curl -s http://127.0.0.1:8000/readyz
```

Point it at a backend with one environment variable — `JEV_BACKEND_TYPE` is always `openai`,
only `JEV_BACKEND_BASE_URL` changes:

| server    | `JEV_BACKEND_BASE_URL` on the host | inside compose            | `JEV_BACKEND_MODEL`         |
| --------- | ---------------------------------- | ------------------------- | --------------------------- |
| llama.cpp | `http://127.0.0.1:8080`            | `http://llama-server:8080` | empty (auto-detected)       |
| vLLM      | `http://127.0.0.1:8001`            | `http://vllm:8000`         | the `--served-model-name`  |
| SGLang    | `http://127.0.0.1:8002`            | `http://sglang:30000`      | the `--served-model-name`  |

```bash
JEV_BACKEND_BASE_URL=http://127.0.0.1:8080 python -m app
```

Or with Docker:

```bash
docker compose up -d                                   # gateway alone
docker compose -f docker-compose.llamacpp.yml up -d    # gateway + llama-server
```

Configuration is entirely `JEV_`-prefixed environment variables — there is no config file.
`cp .env.example .env` keeps them together; the full table is in the
[configuration reference](docs/configuration.en.md). To reach vLLM or SGLang, copy the
compose file and change `JEV_BACKEND_BASE_URL` and `JEV_BACKEND_MODEL` in the `environment:`
block — only the llama.cpp stack ships with the repository.

## 3. How it works

A Jev call is a *classification* problem, not a generation problem. For every question the
gateway renders the evidence plus lettered options (`A`, `B`, `C`, …), asks the backend for
**exactly one** next token with logprobs (`max_tokens: 1`), reads each candidate letter's
logprob, and softmaxes the candidates into a probability distribution. No text is generated,
so a decision costs one forward pass over the prompt. That is what makes a small local
model (0.6B–4B) viable as a router, a gate or a triage classifier.

Measured against direct tool calling on the same 10 cases (`noul` × 4, `choice` × 4,
`score` × 2, 3 runs each, thinking disabled on both paths, strictly single-threaded):

| backend                     | path     | accuracy      | latency mean | output tokens |
| --------------------------- | -------- | ------------- | ------------ | ------------- |
| 27B (remote, international) | gateway  | **30/30 100%** | 1027 ms      | **1.0**       |
|                             | direct   | 27/30 90%     | 2005 ms      | 41.9          |
| 0.8B (local llama.cpp)      | gateway  | 18/30 60%     | 67 ms        | **1.0**       |
|                             | direct   | 21/30 70%     | 599 ms       | 47.5          |

Both sets point at the same conclusion: **the gateway is faster and more accurate** (1.95×
at 27B, 8.9× at 0.8B). They differ only in how readable the numbers are. The ~1 second at
27B is a fixed cost of going out through the proxy that **both paths pay**, so it cancels in
the difference — the ~978 ms between them is real decoding saved — but it destroys the
absolute number (nothing can be projected from it) and dilutes the ratio (1.95× is the
watered-down view of the same gain). The local 0.8B has no such floor, so 8.9× is the honest
reading of the protocol's efficiency; but its 60% / 70% accuracy is the capability ceiling of
a 0.8B, not the protocol, since the same question scores 6/6 through the gateway at 27B.

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
(简体中文: [/](https://jermeyhu.github.io/jev-gateway/)), built from `docs/` with MkDocs
Material: quick start, how it works, API reference, configuration, backends, images,
limits & FAQ, and development.

## Acknowledgements

The request/response contract and the logprob-classification idea are modelled on
[David-Lolly/Jev-Compatible](https://github.com/David-Lolly/Jev-Compatible).

## License

[Apache-2.0](LICENSE).
