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
    （有的是自然对数，有的是 log10），但实践中各家都用自然对数，分布是可比的——这正是
    [对比两个后端](#comparing-two-backends)那节测量的东西。

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
| OpenRouter，`openrouter/qwen/*`   | `reasoning: {effort: "none"}`                    |
| DeepSeek `deepseek-reasoner`     | *（无关闭开关——改用 `deepseek-chat`）*          |

!!! danger "OpenRouter 的 `reasoning.enabled: false` 无效"

    `reasoning: {enabled: false}` 会被接受、不报错，但**完全没有作用**——这是最坑的一种
    失败：配置看起来完全正确，思考照旧。在 `openrouter/qwen/qwen3.8-27b` 上实测，只有
    `reasoning: {effort: "none"}` 能真正关掉思考。

    如果你不确定后端认哪种写法，**把两种都发进去**：不认识的 key 会被静默忽略，认识的会生效。

    ```bash
    JEV_BACKEND_EXTRA_BODY='{"chat_template_kwargs": {"enable_thinking": false}, "reasoning": {"effort": "none"}}'
    ```

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

## 静默失败：`HTTP 200` 但 `logprobs` 是 `null` {#silent-logprobs-null}

**这是换后端时最容易卡住的一种故障**：请求返回 `200`，网关报
`BACKEND_PROTOCOL_ERROR: backend response contained no logprobs object`，但后端日志里
什么错都没有。绝大多数情况是下面两条之一，而它们**没有任何一条会给出错误提示**。

### 1. 思考没关掉，唯一的 token 被思考吃光

`max_tokens: 1` 只允许模型吐一个字。思考没关掉时，这一个字就是思考片段，响应长这样：

```json
{"message": {"content": "", "reasoning": "We",
             "reasoning_details": [{"type": "reasoning.text", "text": "We"}]},
 "finish_reason": "length", "logprobs": null}
```

判据很简单：**响应里出现了 `reasoning` 或 `reasoning_details` 字段，就是没关掉**。按
[推理模型](#reasoning-models)那节改 `extra_body`。

### 2. `top_logprobs` 超过服务端上限

OpenAI 规范把 `top_logprobs` 上限定在 **20**，而网关的默认值是 **128**。超过上限时，后端
**不报 400**，只是把 `logprobs` 整个变成 `null`——和上面那条症状一模一样。

在 `192.168.1.200:8080` 的 `openrouter/qwen/qwen3.8-27b` 上实测：

| `top_logprobs` | 5  | 20 | 24 | 32 | 64 | 128 |
| -------------- | -- | -- | -- | -- | -- | --- |
| `logprobs`      | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |

这个上限**因后端而异**，不能假定。同一台服务器上的 `openrouter/qwen/qwen3.7-flash` 上限
只有 **5**。

!!! warning "小探针健康 ≠ 网关健康"

    手写 `curl` 探测时习惯用 `top_logprobs: 20`，它**完全正常**；网关用默认的 128，
    于是每个请求都挂。两者症状一样，所以很容易得出「后端没问题」的错误结论。
    换新后端时**一定要用网关的默认窗口去测**。

### 诊断顺序

```bash
curl -s http://<后端>/v1/chat/completions \
  -H "content-type: application/json" \
  -H "authorization: Bearer $KEY" \
  -d '{"model":"<模型 id>","messages":[{"role":"user","content":"Reply with the single letter A."}],
       "max_tokens":1,"temperature":0,"logprobs":true,"top_logprobs":128,
       "reasoning":{"effort":"none"}}'
```

看三件事：

1. 响应里有没有 `reasoning` 字段 → 有就是没关思考；
2. `logprobs` 是不是 `null`，`content` 是不是空数组 → 是就继续往下；
3. 把 `top_logprobs` 从 20、24、32、64、128 逐个试 → 找出能用的最大值，写进
   `JEV_BACKEND_TOP_LOGPROBS`。

!!! tip "其他诊断协议错误"

    每个问题都报 `BACKEND_PROTOCOL_ERROR`，除了上面两条，还可能是服务根本不返回
    `logprobs.content[0].top_logprobs`，或者模型的 chat template 永远不会吐出裸字母。
    改任何网关配置之前，先用 `curl` 看一下 `POST /v1/chat/completions` 的原始响应。

## 对比两个后端 {#comparing-two-backends}

不同服务、不同量化、不同 `top_logprobs`，即使 argmax 一致，概率向量也会不同。要量这件事，
把同一个网关指向不同的 `JEV_BACKEND_BASE_URL`/`JEV_BACKEND_TOP_LOGPROBS` 各跑一轮，把
`answers.<q>.logprobs` 抄下来对比：

```bash
# 后端 A
curl -s http://127.0.0.1:8000/v1/systemone -H 'content-type: application/json' -d @case.json

# 后端 B：改 JEV_BACKEND_BASE_URL / JEV_BACKEND_TOP_LOGPROBS，重启后再跑一次
curl -s http://127.0.0.1:8000/v1/systemone -H 'content-type: application/json' -d @case.json
```

然后对每个问题看三件事：

* **argmax 一致性**：排序是否保住；
* **mean|dp|**：概率向量整体移动了多远；
* **Brier 距离**：两个概率向量的均方差距。要盯的是这个数——argmax 一致但 Brier 距离大，
  意味着换量化或换后端已经悄悄改坏了阈值。
