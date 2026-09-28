# 后端 {#backends}

## 只有一种协议 {#one-protocol}

网关发送 `POST /v1/chat/completions`，带 `max_tokens: 1`、`logprobs: true` 和
`top_logprobs: N`，然后从 `choices[0].logprobs.content[0].top_logprobs` 读取 next-token
分布。

这就是全部契约。任何实现了带 logprobs 的 OpenAI chat-completions API 的服务都能用——
llama.cpp、vLLM、SGLang、Ollama、LM Studio、TGI、托管 API——而且**绝不会发送任何引擎
专属参数**。换后端就是改一行 `base_url`。

## 按服务填 `base_url` {#base_url-by-server}

`backend.type` 恒为 `openai`；只有 `backend.base_url` 会变：

| 服务      | 宿主机 `backend.base_url` | compose 内部               | `backend.model`             |
| --------- | ------------------------ | -------------------------- | --------------------------- |
| llama.cpp | `http://127.0.0.1:8080`  | `http://llama-server:8080` | `null`（自动探测）          |
| vLLM      | `http://127.0.0.1:8001`  | `http://vllm:8000`         | 对应 `--served-model-name`  |
| SGLang    | `http://127.0.0.1:8002`  | `http://sglang:30000`      | 对应 `--served-model-name`  |

容器里的 `base_url` 绝不会是 `127.0.0.1`（那指向网关容器自己）；要访问宿主机上的服务用
`http://host.docker.internal:8080`。

vLLM 和 SGLang 的 `backend.model` 必须与 `--served-model-name` 一致；留 `null` 则从
`/v1/models` 自动探测。

!!! warning "仓库中未附带的 Compose 模板"

    仓库里只有 `docker-compose.yml`（仅网关）和 `docker-compose.llamacpp.yml`
    （网关 + llama-server）。vLLM 和 SGLang 请自行启动推理服务，再把 `base_url` 指过去。

## 读取 top-N 窗口 {#reading-the-top-n-window}

每个返回的 token 都会映射到一个候选字母。`"A"` 和 `" A"` 都算 `A`，取其中概率最高的
变体。落在 top-N 窗口外的候选会被截断到实际返回的最低 logprob，并在诊断中把该问题标记为
`truncated`。

如果出现 `truncated: true`，上调 `backend.top_logprobs`（最高 4096）并重新测量。被截断的
概率是**下界**，不是估计值——建立在它之上的阈值并不可信。

!!! note "logprob 约定"

    网关只对候选重新归一化，词表其余部分的质量被丢弃。不同服务的 logprob 刻度可能不同
    （有的是自然对数，有的是 log10），但实践中各家都用自然对数，分布是可比的——
    [`compare_backends.py`](scripts.zh.md#comparing-two-backends) 测的正是这件事。

## `supports_images`

`backend.supports_images: null` 即「自动」：假定后端能接收图片，图片缺失时不投递。模型
没有视觉塔时显式设为 `false`，带图请求会直接快速失败，而不会打到后端。

## 推理模型 {#reasoning-models}

*推理*模型（Qwen3 / Qwen3.5、DeepSeek-R1、OpenAI o 系列……）会把思考内容作为第一个 token
输出，于是没有任何字母落进 top-N 窗口，每个问题都以 `BACKEND_PROTOCOL_ERROR` 失败。网关
不会替你猜怎么关；把 `backend.extra_body` 设成你的服务认识的写法即可：

| 后端                             | `backend.extra_body`                             |
| -------------------------------- | ------------------------------------------------ |
| llama.cpp / vLLM / SGLang（Qwen3） | `chat_template_kwargs: {enable_thinking: false}` |
| OpenAI o 系列 / GPT-5            | `reasoning_effort: none`                         |
| OpenRouter（任意推理模型）       | `reasoning: {enabled: false}`                    |
| DeepSeek `deepseek-reasoner`     | *（无关闭开关——改用 `deepseek-chat`）*          |

写成 YAML：

```yaml
backend:
  base_url: http://127.0.0.1:8080
  extra_body:
    chat_template_kwargs:
      enable_thinking: false
```

`extra_body` 会合并进每个请求，但永远不会覆盖 `messages`、`max_tokens`、`logprobs`、
`top_logprobs`——契约字段总是胜出，所以 `extra_body` 写错也无法悄悄破坏决策路径。非推理
模型保持 `{}` 即可。

!!! tip "诊断协议错误"

    每个问题都报 `BACKEND_PROTOCOL_ERROR`，几乎总是三种原因之一：推理模型没关思考、服务
    不返回 `logprobs.content[0].top_logprobs`、或者模型的 chat template 永远不会吐出
    裸字母。改任何网关配置之前，先用 `curl` 看一下
    `POST /v1/chat/completions` 的原始响应。

## 对比两个后端 {#comparing-two-backends}

不同服务、不同量化、不同 `top_logprobs`，即使 argmax 一致，概率向量也会不同。测量这件事
的脚本：

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

* **argmax 一致性**：排序是否保住；
* **mean|dp|**：概率向量整体移动了多远；
* **Brier 距离**：两个概率向量的均方差距。要盯的是这个数——argmax 一致但 Brier 距离大，
  意味着换量化或换后端已经悄悄改坏了阈值。

完整脚本列表见[脚本](scripts.zh.md#comparing-two-backends)。
