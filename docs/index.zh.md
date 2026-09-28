---
title: 首页
hide:
  - toc
---

# Jev Gateway {#jev-gateway}

<div class="jev-hero" markdown>

自托管的 **Jev / System One 兼容**决策网关。对外只暴露你已经在调用的 `POST /v1/systemone`，
决策层跑在自己的推理服务上，不依赖闭源的 TypeSafe API，并在同一契约上扩展了本地图片输入。

网关只说一种协议：**OpenAI chat-completions API**（`POST /v1/chat/completions` 带
`logprobs`）。任何实现了它的服务都能用——llama.cpp、vLLM、SGLang、Ollama、LM Studio、
TGI、托管 API——且不会发送任何引擎专属参数。

</div>

<div class="jev-actions" markdown>

[快速开始 :material-arrow-right:](quick-start.zh.md){ .md-button .md-button--primary }
[接口参考](api.zh.md){ .md-button }
[GitHub 源码](https://github.com/jermeyhu/jev-gateway){ .md-button }

</div>

## 它解决什么问题 {#why-it-exists}

官方 Jev 接口只接受文本（文档明确 `No image, audio, or video input`），Pydantic AI 客户端
遇到非文本 part 直接抛 `UserError: Files are not supported by this model`。本项目替换文本
路径，同时支持带截图 / 相机帧的证据打分。

Jev 的一次调用是分类问题，不是生成问题。每个问题：渲染证据加带字母的选项（`A`、`B`、
`C`…），向后端请求**恰好一个** next token 的 logprobs（`max_tokens: 1`），读出每个候选
字母的 logprob，对候选做 softmax 得到概率分布。不生成任何文本，一次决策的成本就是
prompt 上的一次前向传播。这正是 0.6B–4B 小模型能充当路由、门禁和分诊分类器的原因。

## 网关 vs 直连工具调用 {#gateway-vs-direct-tool-calling}

替代方案——让模型调用工具再读参数——也可行，但必须**生成**调用内容。同一证据、同一评分
标准下的实测（Qwen3.5-0.8B + llama.cpp，单线程，6 个 SRE 分诊案例 × 3 轮，两条路径都关思考）：

| 指标（每案例 = 3 个决策）       | 网关（logprobs） | 直连工具调用 |
| ------------------------------- | ---------------- | ------------ |
| 延迟，prompt 缓存命中后         | **421 ms**       | 4547 ms      |
| 延迟，冷缓存（首轮）            | 4742 ms          | 4258 ms      |
| 输出 token                      | **3**（固定）    | ~55          |
| 输入 token（18 案例合计）       | 10 881           | 11 661       |
| 正确率（精确评分标准）          | 3/4              | 3/4          |

prompt 缓存命中后网关**快约 11 倍**、输出 token **少约 18 倍**，正确率相同。生成一次工具
调用 JSON 每个决策要解码约 18 个 token，而网关只解码 1 个。只有当模型需要先自由推理再
决策时才用直连。

## 同一基准得出的两条评分标准经验 {#two-rubric-lessons}

**把判据写成可判定的。** 用模糊标签（`Total outage / Degraded / Minor`）时两条路径都
接近随机——直连甚至把一个全绿的平凡案例判成 `sev1`。把每个候选改写成能从 state 直接
验证的阈值（`error_rate ≥ 0.20`，或整区宕机……）后，两条路径同时改善，说明瓶颈在措辞，
不在网关。

**softmax 是诚实的。** 打乱候选顺序只会改变哪个*字母*胜出，不会改变哪个*标签*胜出，
概率质量跟着证据走（全局宕机时 `sev1 ≈ 0.85`）。可以信任分布本身；阈值在自己的标注集
上校准。

## 一览 {#at-a-glance}

| | |
| --- | --- |
| 端点 | `POST /v1/systemone`、`GET /healthz`、`GET /readyz`、`GET /v1/models` |
| 协议 | 带 `logprobs` 的 OpenAI chat-completions |
| 问题类型 | `noul`、`choice`、`score` |
| 单题候选数 | 2 – 16 |
| 单次请求问题数 | 1 – 64 |
| 图片 | data URL、需开启的远程 URL、裸 base64 |
| 并发 | 问题并行执行，上限 `backend.max_concurrency` |
| 许可 | Apache-2.0 |

## 接下来看什么 {#where-to-next}

<div class="grid cards" markdown>

-   :material-rocket-launch: **快速开始**

    ---

    安装、配置，并对接本地 llama.cpp / vLLM / SGLang 服务，附可直接粘贴的冒烟测试。

    [:octicons-arrow-right-24: 开始使用](quick-start.zh.md)

-   :material-function-variant: **工作方式**

    ---

    分类技巧、两种 prompt 布局、logprob 解析规则，以及为什么一次前向传播就够了。

    [:octicons-arrow-right-24: 理解设计](how-it-works.zh.md)

-   :material-api: **接口参考**

    ---

    `POST /v1/systemone` 的请求响应结构、健康检查端点、错误表和 request-id 约定。

    [:octicons-arrow-right-24: 查看接口](api.zh.md)

-   :material-tune: **配置参考**

    ---

    `config.yaml` 的每个键、默认值，以及让拼写错误在启动时失败的两条规则。

    [:octicons-arrow-right-24: 配置网关](configuration.zh.md)

-   :material-server-network: **后端**

    ---

    对接任意 OpenAI 兼容服务，以及用 `extra_body` 关掉推理模型思考的配方。

    [:octicons-arrow-right-24: 连接后端](backends.zh.md)

-   :material-image-multiple: **图片**

    ---

    支持的图片形式、限额，以及为什么图片挂在第一条 user message 的最前面。

    [:octicons-arrow-right-24: 接入图片](images.zh.md)

-   :material-flask-outline: **脚本**

    ---

    冒烟测试、后端 A/B 对比、分诊正确率、速度与 token 基准，以及区分措辞问题与设计问题的
    探针脚本。

    [:octicons-arrow-right-24: 运行脚本](scripts.zh.md)

-   :material-alert-circle-outline: **限制与 FAQ**

    ---

    已知限制、校准注意事项，以及在信任一个概率之前会问到的那些问题。

    [:octicons-arrow-right-24: 了解限制](limits.zh.md)

</div>

## 致谢 {#acknowledgements}

请求/响应契约与 logprob 分类的思路参考了
[David-Lolly/Jev-Compatible](https://github.com/David-Lolly/Jev-Compatible)——感谢这个
本网关保持兼容的参考实现。
