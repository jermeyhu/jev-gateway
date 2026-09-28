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
curl -s http://127.0.0.1:8000/readyz
```

对接后端只需设一个环境变量——`JEV_BACKEND_TYPE` 恒为 `openai`，只有
`JEV_BACKEND_BASE_URL` 会变：

| 服务      | 宿主机 `JEV_BACKEND_BASE_URL` | compose 内部               | `JEV_BACKEND_MODEL`           |
| --------- | ----------------------------- | -------------------------- | ----------------------------- |
| llama.cpp | `http://127.0.0.1:8080`       | `http://llama-server:8080` | 留空（自动探测）              |
| vLLM      | `http://127.0.0.1:8001`       | `http://vllm:8000`         | 对应 `--served-model-name`    |
| SGLang    | `http://127.0.0.1:8002`       | `http://sglang:30000`      | 对应 `--served-model-name`    |

```bash
JEV_BACKEND_BASE_URL=http://127.0.0.1:8080 python -m app
```

或者用 Docker：

```bash
docker compose up -d                                   # 仅网关
docker compose -f docker-compose.llamacpp.yml up -d    # 网关 + llama-server
```

配置全是 `JEV_` 前缀的环境变量，没有配置文件；`cp .env.example .env` 可以集中管理，
变量表见[配置参考](docs/configuration.md)。要接 vLLM 或 SGLang，复制 compose 文件并改掉
`environment:` 里的 `JEV_BACKEND_BASE_URL` 和 `JEV_BACKEND_MODEL` —— 仓库里只随附
llama.cpp 那套。

## 3. 原理说明

Jev 的一次调用是分类问题，不是生成问题。每个问题：渲染证据加带字母的选项（`A`、`B`、
`C`…），向后端请求**恰好一个** next token 的 logprobs（`max_tokens: 1`），读出每个候选
字母的 logprob，对候选做 softmax 得到概率分布。不生成任何文本，一次决策的成本就是
prompt 上的一次前向传播。这正是 0.6B–4B 小模型能充当路由、门禁和分诊分类器的原因。

与直连工具调用在同一组 10 条用例下的实测（`noul` × 4、`choice` × 4、`score` × 2，每题 3 轮，
两条路径都关思考，严格单线程）：

| 后端 | 路径 | 准确率 | 延迟 mean | 输出 token |
| ---- | ---- | ------ | --------- | ---------- |
| 27B（远端，跨境） | 网关 | **30/30 100%** | 1027 ms | **1.0** |
|                     | 直连 | 27/30 90% | 2005 ms | 41.9 |
| 0.8B（本地 llama.cpp） | 网关 | 18/30 60% | 67 ms | **1.0** |
|                     | 直连 | 21/30 70% | 599 ms | 47.5 |

两组数字都指向同一个结论：**网关更快也更准**（27B 快 1.95 倍，0.8B 快 8.9 倍）。区别只在
可解读程度。27B 那 ~1 秒是经代理出境的固定开销，**两条路径都要付**，所以作差时抵消了——
中间差的 ~978 ms 是真实省下的解码；但它毁掉的是绝对值（不能推算吞吐）和比值（1.95 倍
是同一收益“含水”的版本）。本地 0.8B 没有这笔地板，8.9 倍就是协议效率的诚实读数；但它的
60% / 70% 反映的是 0.8B 的能力上限，不是协议水平——同一题 27B 上网关是 6/6。

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
构建：快速开始、工作方式、接口参考、配置参考、后端、图片、限制与 FAQ、开发。

## 致谢

请求/响应契约与 logprob 分类的思路参考了
[David-Lolly/Jev-Compatible](https://github.com/David-Lolly/Jev-Compatible)。

## 许可

[Apache-2.0](LICENSE)。
