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

[快速开始 :material-arrow-right:](quick-start.md){ .md-button .md-button--primary }
[接口参考](api.md){ .md-button }
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

替代方案——让模型调用工具再读参数——也可行，但必须**生成**调用内容。同一组 10 条中文
业务用例（`noul` × 4、`choice` × 4、`score` × 2）各跑 3 轮，两条路径都关思考、严格单线程
（后端并发有限，串行才测得到真实延迟），在两个后端上各跑一遍——每组 60 次调用，零失败。

### 27B：远端，跨境 {#bench-27b-remote}

| 指标             | 网关（logprobs） | 直连工具调用 |
| ---------------- | ---------------- | ------------ |
| 准确率           | **30/30 100%**   | 27/30 90%    |
| 延迟 mean        | **1027 ms**      | 2005 ms      |
| 延迟 median      | **977 ms**       | 1820 ms      |
| 输出 token / 答案 | **1.0**（固定） | 41.9         |

| primitive | 网关      | 直连      |
| --------- | --------- | --------- |
| `noul`    | 12/12     | 12/12     |
| `choice`  | 12/12     | 12/12     |
| `score`   | **6/6**   | 3/6       |

网关快 1.95 倍、准 3 题（`score` 6/6 对 3/6）。**但 1027 ms 这个绝对值不能直接拿来读**——
原因见[怎么读这两组数据](#how-to-read-these-numbers)。

### 0.8B：本地 llama.cpp {#bench-08b-local}

| 指标         | 网关                 | 直连                 |
| ------------ | -------------------- | -------------------- |
| 准确率       | 18/30 60%            | 21/30 70%            |
| 延迟 mean    | 67 ms                | 599 ms               |
| 延迟 median  | 47 ms                | 528 ms               |
| 输出 token   | 1.0                  | 47.5                 |

| primitive | 网关      | 直连      |
| --------- | --------- | --------- |
| `noul`    | 12/12     | 12/12     |
| `choice`  | 6/12      | 6/12      |
| `score`   | **0/6**   | 3/6       |

网关快 8.9 倍、输出 token 少 47.5 倍——**这组数字可以直接读**。准确率偏低是 0.8B 的能力
上限，不是协议水平，原因见下。

### 怎么读这两组数据 {#how-to-read-these-numbers}

**结论两边一致：网关更快，也更准。** 27B 上快 1.95 倍，0.8B 上快 8.9 倍。区别只在数字
本身的可解读程度。

**27B 那 ~1 秒是固定开销，两条路径都要付。** 每次调用都经代理出境到 openrouter。实测把
输入从 13 token 放大到 1283 token、输出从 1 token 放大到 10 token，延迟都没有上升趋势；
同一请求连发 9 次是 `1022 1653 1024 1025 1025 1232 2934 2079 1050` ms——一个约 1 秒的
硬地板，上面叠着上游排队造成的随机尖峰（最长一次 5227 ms）。13 token 的空请求和跑完整
题目的耗时基本一样，`cached_tokens` 恒为 0。

因为两条路径付的是**同一笔**开销，它在作差时抵消掉了：网关 1027 ms、直连 2005 ms，中间
差的 ~978 ms 是真实省下的解码工作。它毁掉的是另外两件事——**绝对值不可解读**（1027 ms 里
绝大部分不是决策本身，推不出吞吐或容量），**比值被摊薄**（真实收益是省下的 ~978 ms，除以
含地板的总时延就只显出不到 2 倍）。

**0.8B 那组是干净的测量。** 67 ms 里没有任何网络底噪，8.9 倍就是协议自身效率的诚实读数；
远端那个 1.95 倍是同一笔收益被 1 秒地板摊薄后的样子。

**0.8B 的 `score` 全错是模型能力上限，不是协议缺陷。** 排列实验证实：把三档风险标签轮换
位置重跑，概率质量始终跟着**内容**走，但比较结果永远偏向最保守的那一档——0.8B 根本没法
区分档位。**同一个问题在 27B 上是 6/6。** 0.6B–4B 适合做二值门禁（`noul`），多档分级
（`score`）要更大的模型。

最后一条：远端部署要优化延迟，方向不是压 prompt（实测压了没用），而是减少往返次数或换
更近的接入点。

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

    [:octicons-arrow-right-24: 开始使用](quick-start.md)

-   :material-function-variant: **工作方式**

    ---

    分类技巧、两种 prompt 布局、logprob 解析规则，以及为什么一次前向传播就够了。

    [:octicons-arrow-right-24: 理解设计](how-it-works.md)

-   :material-api: **接口参考**

    ---

    `POST /v1/systemone` 的请求响应结构、健康检查端点、错误表和 request-id 约定。

    [:octicons-arrow-right-24: 查看接口](api.md)

-   :material-tune: **配置参考**

    ---

    每个环境变量、默认值，以及让拼写错误在启动时失败的两条规则。

    [:octicons-arrow-right-24: 配置网关](configuration.md)

-   :material-server-network: **后端**

    ---

    对接任意 OpenAI 兼容服务，以及用 `extra_body` 关掉推理模型思考的配方。

    [:octicons-arrow-right-24: 连接后端](backends.md)

-   :material-image-multiple: **图片**

    ---

    支持的图片形式、限额，以及为什么图片挂在第一条 user message 的最前面。

    [:octicons-arrow-right-24: 接入图片](images.md)

-   :material-alert-circle-outline: **限制与 FAQ**

    ---

    已知限制、校准注意事项，以及在信任一个概率之前会问到的那些问题。

    [:octicons-arrow-right-24: 了解限制](limits.md)

</div>

## 致谢 {#acknowledgements}

请求/响应契约与 logprob 分类的思路参考了
[David-Lolly/Jev-Compatible](https://github.com/David-Lolly/Jev-Compatible)——感谢这个
本网关保持兼容的参考实现。
