# 限制与 FAQ {#limits-faq}

## 已知限制 {#known-limits}

* `noul` 概率是只对两个候选字母做 softmax；阈值要在自己的标注集上校准。
* 单个问题最多 **16 个候选**，超出以 `INVALID_REQUEST` 拒绝。
* 诊断里出现 `truncated: true` 说明有候选被截断；上调 `top_logprobs`。
* 图片是对参考契约的扩展，按托管服务写的客户端仍可运行，但会忽略该字段。
* 超出后端真实能力的部分应靠水平扩展解决，而不是继续调高 `max_concurrency`。

## 校准 {#calibration}

决策模型对**校准**敏感。阈值门禁（`p < 0.7 转人工`）成立的前提是概率诚实，而激进量化
可能保住 argmax 却毁掉置信度。

用于门禁的模型优先 **Q8_0 或 F16**；Q4_K_M 按实验对待，并用
[`compare_backends.py`](scripts.zh.md#comparing-two-backends) 测量，而不是靠假设。

两个习惯能让阈值保持可信：

1. 每次换模型、换量化、换服务之后都重跑一次 `compare_backends.py`，盯 Brier 距离而不是
   argmax 一致性。
2. 留一份来自你自己流量的小标注集，核对网关给出的分数。托管 API 的校准结论不会自动
   迁移到你硬件上的 4B 模型，必须重新测量。

## FAQ {#faq}

### 为什么直连工具调用路径更慢？ {#why-is-the-direct-tool-calling-path-slower}

两条路径在同一证据、同一评分标准下的实测（Qwen3.5-0.8B + llama.cpp，单线程，6 个 SRE
分诊案例 × 3 轮，两条路径都关思考）：

| 指标（每案例 = 3 个决策）       | 网关（logprobs） | 直连工具调用 |
| ------------------------------- | ---------------- | ------------ |
| 延迟，prompt 缓存命中后         | **421 ms**       | 4547 ms      |
| 延迟，冷缓存（首轮）            | 4742 ms          | 4258 ms      |
| 输出 token                      | **3**（固定）    | ~55          |
| 输入 token（18 案例合计）       | 10 881           | 11 661       |
| 正确率（精确评分标准）          | 3/4              | 3/4          |

生成一次工具调用 JSON 每个决策要解码约 18 个 token；网关只解码 1 个。冷缓存那一行是
诚实的补充：首次请求的 prefill 两边都要付，所以优势出现在之后的每一次请求上。只有当模型
需要先自由推理再决策时才用直连路径。

### 每个问题都报 `BACKEND_PROTOCOL_ERROR`，为什么？ {#every-question-fails-with-backend_protocol_error-why}

几乎总是推理模型把思考内容当成第一个 token 输出，于是没有任何候选字母落进 top-N 窗口。
把 `backend.extra_body` 设成你的服务的关闭开关——见[推理模型](backends.zh.md#reasoning-models)。
另外两个原因是服务不返回 `logprobs.content[0].top_logprobs`，以及 chat template 永远
不产生裸字母。改任何设置之前先用 `curl` 看原始响应。

### 能给 17 个选项吗？ {#can-i-send-17-options}

不能。单题 2 – 16 个候选，17 个会以 `INVALID_REQUEST` 拒绝。字母从 `A` 排到 `P`。

### 一个问题失败会拿到部分结果吗？ {#do-i-get-partial-answers-if-one-question-fails}

不会。任一问题失败则整次请求失败，报出的是按问题顺序的第一个错误。这与参考实现一致——
一组决策只有作为整体才有意义。

### `truncated: true` 怎么办？ {#truncated-true-what-now}

说明某个候选字母落在 `top_logprobs` 窗口之外，被截断到实际返回的最低 logprob。那是下界，
不是估计值。上调 `backend.top_logprobs`（16 – 4096）并重新测量；如果服务端拒绝大窗口，
就减少候选数。

### 问题真的是并行的吗？ {#do-the-questions-really-run-in-parallel}

是的，上限为 `backend.max_concurrency`（默认 32）。无论完成顺序如何，响应都保持请求里的
问题顺序；`request.total_timeout_seconds` 约束整次请求，而不是每个问题。

### 我怎么知道这些概率不是编出来的？ {#how-do-i-know-the-probabilities-are-not-made-up}

跑 `diagnose_severity.py` 和 `probe_position_bias.py`。如果打乱候选顺序只改变胜出的*字母*，
而概率质量跟着证据走，说明分布反映的是模型的判断。如果*标签*随着位置翻转，模型读的是
位置而不是含义，这些数字就不该拿来设阈值。

### 能配推理模型用吗？ {#can-i-use-it-with-a-reasoning-model}

可以，用 `backend.extra_body` 关掉思考即可——各服务端的写法见
[推理模型](backends.zh.md#reasoning-models)。DeepSeek 的 `deepseek-reasoner` 没有关闭开关，请改用
`deepseek-chat`。

### 多大模型够用？ {#which-model-size-is-enough}

0.6B – 4B 是这个模式开始划算的区间，因为全部成本就是一次前向传播。更大的模型在这里不
自动等于更好的*分类器*——参考基准上最大的收益来自改写判据，而不是放大模型。

### 网关为什么不流式？ {#why-does-the-gateway-not-stream}

没有东西可以流式返回。一个决策只解码一个 token，流式只会增加复杂度而不带来信息。
始终发送 `stream: false`。

### 发给后端的 prompt 可以看吗？ {#is-the-prompt-sent-to-the-backend-readable}

可以，而且这是有意为之——整个设计就是可审计的。`fused` 与参考网关逐字节兼容，system
prompt 只有一条常量。想要可缓存的前缀而不是字节兼容时，用 `request.prompt_layout: split`。

### 我该怎么扩容？ {#how-do-i-scale-it}

在负载均衡器后面跑多个网关实例。把 `max_concurrency` 提到后端真实容量之上，只是把队列
从你的进程挪进推理服务，那里更难观测。

## 许可 {#license}

Apache-2.0。请求/响应契约与 logprob 分类的思路参考了
[David-Lolly/Jev-Compatible](https://github.com/David-Lolly/Jev-Compatible)。
