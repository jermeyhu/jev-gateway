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

用于门禁的模型优先 **Q8_0 或 F16**；Q4_K_M 按实验对待，并用[对比两个后端](backends.md#comparing-two-backends)
里那套方法测量，而不是靠假设。

两个习惯能让阈值保持可信：

1. 每次换模型、换量化、换服务之后都重跑一次对比，盯 Brier 距离而不是 argmax 一致性。
2. 留一份来自你自己流量的小标注集，核对网关给出的分数。托管 API 的校准结论不会自动
   迁移到你硬件上的 4B 模型，必须重新测量。

## FAQ {#faq}

### 为什么直连工具调用路径更慢？ {#why-is-the-direct-tool-calling-path-slower}

因为它必须**生成**内容：每个问题要解码一整段工具调用 JSON。同一组 10 条用例（`noul` × 4、
`choice` × 4、`score` × 2）× 3 轮，两条路径都关思考，严格单线程：

| 后端                          | 路径 | 准确率    | 延迟 mean | 延迟 median | 输出 tok |
| ----------------------------- | ---- | --------- | --------- | ----------- | -------- |
| 27B（远端，跨境）              | 网关 | **30/30** | 1027 ms   | 977 ms      | **1.0**  |
|                               | 直连 | 27/30     | 2005 ms   | 1820 ms     | 41.9     |
| 0.8B（本地 llama.cpp，有缓存） | 网关 | 18/30     | 67 ms     | 47 ms       | **1.0**  |
|                               | 直连 | 21/30     | 599 ms    | 528 ms      | 47.5     |

两条路径**都付同一笔跨境固定开销**，所以它作差时抵消了：网关 1027 ms、直连 2005 ms，中间
差的 ~978 ms 是真实省下的解码。**远端上网关确实更快也更准**（30/30 对 27/30，快 1.95 倍）。

这笔固定开销毁掉的是另外两件事：**绝对值不可解读**（1027 ms 里绝大部分不是决策，不能拿它
推算吞吐），以及**比值被摊薄**（真实收益是省下的 ~978 ms，除以含地板的总时延就只显出不到
2 倍）。实测输入放大 100 倍、输出放大 10 倍，延迟都不变。远端部署要优化延迟，方向不是压
prompt，而是减少往返次数或换更近的接入点。

**本地 0.8B 是干净的测量。** 单次 67 ms，无网络底噪，**网关快 8.9 倍、输出 token 少 47.5
倍**——这才是协议自身效率的诚实读数，远端那个 1.95 倍是同一收益被 1 秒地板摊薄后的样子。
但它的 60% / 70% 反映的是 0.8B 的能力上限，不是协议水平（同一题 27B 上网关 6/6）。

### 每个问题都报 `BACKEND_PROTOCOL_ERROR`，为什么？ {#every-question-fails-with-backend_protocol_error-why}

两种最常见的原因**都会静默失败**——后端返回 `HTTP 200`，日志里没有错误，只有
`logprobs` 是 `null`：

1. 推理模型没关思考，思考内容吃掉了 `max_tokens: 1` 里唯一那个 token（响应里会出现
   `reasoning` 字段）；
2. `top_logprobs` 超过服务端上限（默认 128，而 OpenAI 规范只允许 20）。

两步诊断和对应的修法见[静默失败](backends.md#silent-logprobs-null)。第三种原因是服务不
返回 `logprobs.content[0].top_logprobs`，或者 chat template 永远不产生裸字母。改任何设置
之前先用 `curl` 看原始响应。

### 能给 17 个选项吗？ {#can-i-send-17-options}

不能。单题 2 – 16 个候选，17 个会以 `INVALID_REQUEST` 拒绝。字母从 `A` 排到 `P`。

### 一个问题失败会拿到部分结果吗？ {#do-i-get-partial-answers-if-one-question-fails}

不会。任一问题失败则整次请求失败，报出的是按问题顺序的第一个错误。这与参考实现一致——
一组决策只有作为整体才有意义。

### `truncated: true` 怎么办？ {#truncated-true-what-now}

说明某个候选字母落在 `top_logprobs` 窗口之外，被截断到实际返回的最低 logprob。那是下界，
不是估计值。上调 `backend.top_logprobs`（16 – 4096）并重新测量。

上调之前先看[静默失败](backends.md#silent-logprobs-null)那节：服务端有自己的上限，超限
不报错、只是把 `logprobs` 变成 `null`。用网关当前的窗口先去探一下，确认服务端接受得
了，再谈调大。

### 问题真的是并行的吗？ {#do-the-questions-really-run-in-parallel}

是的，上限为 `backend.max_concurrency`（默认 32）。无论完成顺序如何，响应都保持请求里的
问题顺序；`request.total_timeout_seconds` 约束整次请求，而不是每个问题。

### 我怎么知道这些概率不是编出来的？ {#how-do-i-know-the-probabilities-are-not-made-up}

自己构造一组对照：一份评分标准写得精确，一份写得模糊；再打乱候选顺序各跑一轮。如果打乱
候选顺序只改变胜出的*字母*，而概率质量跟着证据走，说明分布反映的是模型的判断。如果*标签*
随着位置翻转，模型读的是位置而不是含义，这些数字就不该拿来设阈值。

### 能配推理模型用吗？ {#can-i-use-it-with-a-reasoning-model}

可以，用 `backend.extra_body` 关掉思考即可——各服务端的写法见
[推理模型](backends.md#reasoning-models)。DeepSeek 的 `deepseek-reasoner` 没有关闭开关，请改用
`deepseek-chat`。

### 多大模型够用？ {#which-model-size-is-enough}

0.6B – 4B 适合**二值门禁**。同一组用例在 0.8B 上 `noul` 是 12/12，但 `score` 是 0/6——
排列实验显示模型根本区分不了档位，只会把概率压向最保守的一档；同一题在 27B 上是 6/6。
参考基准上更大的收益来自改写判据，而不是放大模型，但**多档分级确实需要更大的模型**。
要知道协议本身的上限，看远端 27B 那组；0.8B 的低准确率只是模型能力到顶了。

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
