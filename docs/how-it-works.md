# 工作方式 {#how-it-works}

## 决策即分类 {#a-decision-is-a-classification}

Jev 的一次调用是分类问题，不是生成问题。每个问题：

1. 渲染证据（`state`）加带字母的选项（`A`、`B`、`C`……）。
2. 向后端请求**恰好一个** next token 的 logprobs（`max_tokens: 1`、`logprobs: true`、
   `top_logprobs: N`）。
3. 从 `choices[0].logprobs.content[0].top_logprobs` 读出每个候选字母的 logprob。
4. 对候选字母做 softmax 得到概率分布。

不生成任何文本，一次决策的成本就是 prompt 上的一次前向传播。这正是 0.6B–4B 小模型能
充当路由、门禁和分诊分类器的原因。

完整分布被保留下来，而不只是 argmax。对 `noul` 来说它**就是**答案；对 `choice` 来说它
作为 `probabilities` 返回；对 `score` 来说它被折算成概率加权均值，因此答案能在档位之间
插值。

## 三种问题类型 {#the-three-question-types}

每种问题类型都把一组候选映射到同一套字母机制上。

| 类型     | 候选                                        | 答案                                              |
| -------- | ------------------------------------------- | ------------------------------------------------- |
| `noul`   | `criteria.true` / `criteria.false`          | `true` 的概率（缺省按 `Yes` / `No` 计）            |
| `choice` | `criteria` 的各个键，按插入顺序             | argmax 键加完整分布                                |
| `score`  | `criteria` 的各项，作为档位 `0..n-1`        | 档位下标的概率加权均值，另附 `legend`              |

字母按顺序分配，最多 16 个候选时从 `A` 排到 `P`。`choice` 出现并列时取**第一个**候选，
因此答案是确定的。

## 并发与失败语义 {#concurrency-and-failure-semantics}

一次请求中的所有问题**并发**执行，上限为 `backend.max_concurrency`。无论完成顺序如何，
响应都保持请求里的问题顺序。

* **任一**问题失败则整次请求失败——没有部分结果。
* 按问题顺序，第一个错误会被报出来。
* `request.total_timeout_seconds` 约束**整次**请求，不是单个问题。
* `backend.timeout_seconds` 约束单个问题。

## Prompt 布局 {#prompt-layouts}

布局由 `request.prompt_layout` 决定。

### `fused`

一条 user message，包含紧凑 JSON 的 `{evidence, criterion, options}`。消息共两条：
`system`、`user`。

=== "fused"

    ```json
    {"evidence":"red green blue","criterion":"pick a colour","options":[{"letter":"A","description":"the colour red"},{"letter":"B","description":"the colour green"}]}
    ```

=== "split"

    ```json
    {"evidence":"red green blue"}
    ```

    ```json
    {"criterion":"pick a colour","options":[{"letter":"A","description":"the colour red"},{"letter":"B","description":"the colour green"}]}
    ```

* `fused` 与参考网关逐字节兼容，在托管 API 上测出的校准结论可以直接迁移。
* `split` 把证据（及图片）放在第一条 user message，指令与选项放在第二条。第一条在同一次
  请求的所有问题之间完全相同，llama.cpp 可复用视觉编码和状态 prefill。延迟优先于字节
  兼容时用它。

## 解析 logprobs {#reading-the-logprobs}

网关把每个返回的 token 映射到一个候选字母。`"A"` 和 `" A"` 都算 `A`，取其中概率最高的
变体。落在 top-N 窗口外的候选会被截断到实际返回的最低 logprob，并在诊断中标记该问题为
`truncated`——看到这个标记就上调 `backend.top_logprobs`（最高 4096）。

概率只对候选重新归一化。词表中其余部分的质量被丢弃，因此一个*绝对*上不太可能的候选，
仍然可能在*相对*分布中占很高的份额。

## 为什么不直接调用工具 {#why-not-just-call-a-tool}

让模型输出一次工具调用、再读参数是最直观的替代方案，它也确实可行。但它必须**生成**这次
调用：每个决策约 18 步解码、约 55 个输出 token；网关只解码 1 个 token。

在同一证据和评分标准下，两条路径的正确率相同，但 prompt 缓存命中后网关快约 11 倍。
只有当模型需要先自由推理再决策时才选直连路径。见
[限制与 FAQ](limits.md#why-is-the-direct-tool-calling-path-slower)。

## System prompt {#the-system-prompt}

一条固定的 system message 告诉后端只回答一个字母。它是代码里的常量，对所有问题类型和
布局都相同——判据和选项走 user message，因此 system prompt 始终可被缓存。
