# jev-gateway

[![文档](https://img.shields.io/badge/docs-jermeyhu.github.io%2Fjev--gateway-3f51b5?logo=material%2Ffor-linux)](https://jermeyhu.github.io/jev-gateway/)
[![许可](https://img.shields.io/badge/license-Apache--2.0-3f51b5)](LICENSE)

[English](https://github.com/jermeyhu/jev-gateway/blob/main/README.en.md) · 简体中文

> 完整文档：**[jermeyhu.github.io/jev-gateway](https://jermeyhu.github.io/jev-gateway/)**
> （English: [/en/](https://jermeyhu.github.io/jev-gateway/en/)）

## 1. 系统简介

自托管的 Jev / System One 兼容决策网关。对外只暴露你已经在调用的 `POST /v1/systemone`，
决策层跑在自己的推理服务上，不依赖闭源的 TypeSafe API，并在同一契约上扩展了本地图片
输入。

官方 Jev 接口只接受文本，Pydantic AI 客户端遇到文件 part 直接抛
`UserError: Files are not supported by this model`，所以截图、相机帧这类证据根本无法参与
打分。本项目是文本路径的直接替代品，同一个进程还能对带图片的证据额外打分。

网关只说一种协议：**OpenAI chat-completions API**（`POST /v1/chat/completions` 带
`logprobs`）。任何实现了它的服务都能用——llama.cpp、vLLM、SGLang、Ollama、LM Studio、
TGI、托管 API——且不会发送任何引擎专属参数。

## 2. 快速启动

```bash
pip install -e ".[dev]"
python -m app                              # 监听 0.0.0.0:8000
python scripts/smoke_test.py --url http://127.0.0.1:8000
```

对接后端只需改 `config.yaml`——`backend.type` 恒为 `openai`，只有 `backend.base_url` 会变：

| 服务      | 宿主机 `base_url`      | compose 内部               | `backend.model`             |
| --------- | ---------------------- | -------------------------- | --------------------------- |
| llama.cpp | `http://127.0.0.1:8080` | `http://llama-server:8080` | `null`（自动探测）          |
| vLLM      | `http://127.0.0.1:8001` | `http://vllm:8000`         | 对应 `--served-model-name`  |
| SGLang    | `http://127.0.0.1:8002` | `http://sglang:30000`      | 对应 `--served-model-name`  |

或者用 Docker：

```bash
docker compose up -d                                   # 仅网关
docker compose -f docker-compose.llamacpp.yml up -d    # 网关 + llama-server
```

`JEV_GATEWAY_CONFIG` 是唯一读取的环境变量，默认 `./config.yaml`。要接 vLLM 或 SGLang，
复制 compose 文件并改掉 `base_url` 和 `backend.model` —— 仓库里只随附 llama.cpp 那套。

## 3. 原理说明

Jev 的一次调用是分类问题，不是生成问题。每个问题：渲染证据加带字母的选项（`A`、`B`、
`C`…），向后端请求**恰好一个** next token 的 logprobs（`max_tokens: 1`），读出每个候选
字母的 logprob，对候选做 softmax 得到概率分布。不生成任何文本，一次决策的成本就是
prompt 上的一次前向传播。这正是 0.6B–4B 小模型能充当路由、门禁和分诊分类器的原因。

与直连工具调用在同一证据、同一评分标准下的实测（Qwen3.5-0.8B + llama.cpp，单线程，6 个
SRE 分诊案例 × 3 轮，两条路径都关思考）：

| 指标（每案例 = 3 个决策） | 网关（logprobs） | 直连工具调用 |
| ------------------------- | ---------------- | ------------ |
| 延迟，prompt 缓存命中后   | **421 ms**       | 4547 ms      |
| 输出 token                | **3**（固定）    | ~55          |
| 正确率（精确评分标准）    | 3/4              | 3/4          |

prompt 缓存命中后网关**快约 11 倍**、输出 token **少约 18 倍**，正确率相同。生成一次工具调用
JSON 每个决策要解码约 18 个 token，而网关只解码 1 个。只有当模型需要先自由推理再决策时才用
直连。

两条值得记住的经验：

* **把判据写成可判定的。** 用模糊标签（"Total outage / Degraded / Minor"）时两条路径都
  接近随机。把每个候选改写成能从 state 直接验证的阈值（"error_rate ≥ 0.20，或整区宕
  机…"）后，两条路径同时改善，说明瓶颈在措辞，不在网关。
* **softmax 是诚实的。** 打乱候选顺序只会改变哪个*字母*胜出，不会改变哪个*标签*胜出，
  概率质量跟着证据走（全局宕机时 `sev1 ≈ 0.85`）。可以信任分布本身；阈值在自己的标注集
  上校准。

*推理*模型（Qwen3 / Qwen3.5、DeepSeek-R1、OpenAI o 系列……）会把思考内容作为第一个
token 输出，于是没有任何字母落进 top-N 窗口，每个问题都以 `BACKEND_PROTOCOL_ERROR`
失败。关掉思考的方式见[后端页面](https://jermeyhu.github.io/jev-gateway/backends/)。

## 文档

完整参考在 **[jermeyhu.github.io/jev-gateway](https://jermeyhu.github.io/jev-gateway/)**
（English: [/en/](https://jermeyhu.github.io/jev-gateway/en/)），由 `docs/` 用 MkDocs Material
构建：快速开始、工作方式、接口参考、配置参考、后端、图片、脚本、限制与 FAQ、开发。

## 致谢

请求/响应契约与 logprob 分类的思路参考了
[David-Lolly/Jev-Compatible](https://github.com/David-Lolly/Jev-Compatible)。

## 许可

[Apache-2.0](LICENSE)。
