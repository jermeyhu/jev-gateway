# 配置参考 {#configuration}

网关只读一个 YAML 文件，默认是 `./config.yaml`；用 `JEV_GATEWAY_CONFIG` 环境变量可以改路径。

```yaml
server:
  host: 0.0.0.0
  port: 8000

backend:
  type: openai
  base_url: http://127.0.0.1:8080
  model: null
  api_key: null
  timeout_seconds: 30.0
  max_concurrency: 32
  top_logprobs: 128
  supports_images: null
  extra_headers: {}
  extra_body: {}

request:
  max_questions: 64
  total_timeout_seconds: 60.0
  prompt_layout: fused

multimodal:
  enabled: true
  max_images: 4
  max_image_bytes: 5242880
  allow_remote_urls: false
  allowed_mime_prefixes: ["image/"]

logging:
  level: INFO
```

**未知键直接报错**，拼写错误在启动时失败而不是被忽略。

## 完整对照 {#reference}

| 段           | 键                                          | 默认值                    | 说明                                    |
| ------------ | ------------------------------------------- | ------------------------- | --------------------------------------- |
| `server`     | `host` / `port`                             | `0.0.0.0` / `8000`        |                                         |
| `backend`    | `type`                                      | `openai`                  | 唯一支持的协议                          |
| `backend`    | `base_url` / `model` / `api_key`            | — / `null` / `null`       | `model: null` 时用后端自报模型名        |
| `backend`    | `timeout_seconds`                           | `30.0`                    | 单问题超时                              |
| `backend`    | `max_concurrency`                           | `32`                      | 单网关实例并发上限                      |
| `backend`    | `top_logprobs`                              | `128`                     | 16 – 4096；窗口外候选按下界截断         |
| `backend`    | `supports_images`                           | `null`（自动）            | `true` / `false` 强制指定              |
| `backend`    | `extra_headers` / `extra_body`              | `{}`                      | 云 API key、租户标识；见「推理模型」    |
| `request`    | `max_questions`                             | `64`                      | 单次请求 1 – 64 个问题                  |
| `request`    | `total_timeout_seconds`                     | `60.0`                    | 整次请求预算                            |
| `request`    | `prompt_layout`                             | `fused`                   | `fused` \| `split`                     |
| `multimodal` | `enabled` / `max_images` / `max_image_bytes` | `true` / `4` / `5242880`  | `allow_remote_urls: false`              |
| `logging`    | `level`                                     | `INFO`                    |                                         |

`base_url` 末尾的斜杠会被去掉，所以 `http://127.0.0.1:8080/` 和 `http://127.0.0.1:8080`
等价。

## 各段说明 {#sections}

### `server`

监听地址。默认 `0.0.0.0:8000`，容器镜像和 Docker Compose 模板用的就是这个。

### `backend`

`type` 恒为 `openai`——网关只说一种协议。不同服务之间只有 `base_url` 会变，见
[后端](backends.zh.md#base_url-by-server)。

`model: null` 表示自动探测：网关会问服务的 `/v1/models` 并取它报告的第一个模型。会报出
多个模型的服务，或要求客户端指明用哪个的服务（vLLM、开了多个 served model 的 SGLang），
应显式固定。

`top_logprobs` 是 next-token 窗口的大小。两到四个候选时默认的 `128` 已经相当宽裕；看到
诊断里的 `truncated: true` 就上调，服务端拒绝大窗口时才下调。取值范围 16 – 4096。

`supports_images: null` 即「自动」：假定后端能接收图片，图片缺失时就不投递。模型没有
视觉塔时显式设为 `false`，图片请求会以 `BACKEND_CAPABILITY_UNSUPPORTED` 快速失败，而
不会打到后端。

`extra_headers` 会作为 HTTP 头合并进每个请求——云 API key、租户 id、项目名。`extra_body`
会合并进 JSON body；服务端「关思考」的开关就写在这里。两者都无法覆盖 `messages`、
`max_tokens`、`logprobs` 和 `top_logprobs`。

### `request`

`max_questions` 限制单次请求能带多少问题（1 – 64）。超出的请求以 `INVALID_REQUEST` 拒绝。

`total_timeout_seconds` 是整次请求的总预算，所有问题加起来。由于问题并发执行，一个 64
问题、60 秒预算的请求，耗时大致仍等于最慢的那一个问题——除非后端已经饱和。

`prompt_layout` 可选 `fused`（默认，与参考网关逐字节兼容）或 `split`（对缓存友好）。
两者都在[工作方式](how-it-works.zh.md#prompt-layouts)里有说明。

### `multimodal`

| 键                       | 默认值               | 含义                                |
| ------------------------ | -------------------- | ----------------------------------- |
| `enabled`                | `true`               | 为 `false` 时任何 `images` 都报 400 |
| `max_images`             | `4`                  | 单次请求的图片数                    |
| `max_image_bytes`        | `5242880`（5 MiB）   | 单图，base64 解码后计算            |
| `allow_remote_urls`      | `false`               | 开启后才接受 `https://` 图片引用   |
| `allowed_mime_prefixes`  | `["image/"]`          | 接受的媒体类型                      |

`allow_remote_urls` 默认为 `false`，因为远程 URL 会让网关去取调用方指定的任意地址——这是
一个服务端请求伪造面。只有两端都归你所有时才应该打开。

### `logging`

`level` 接受标准 Python 日志级别：`DEBUG`、`INFO`、`WARNING`、`ERROR`。默认 `INFO`。
每个响应本来就带 `x-request-id`，日志用的也是同一个 id，排查时直接 grep 它即可。

## Prompt 布局一句话版 {#prompt-layouts-in-one-line}

* `fused`——单条 user message 含 `{evidence, criterion, options}`，与参考网关逐字节兼容，
  在托管 API 上测出的校准结论可以直接迁移。
* `split`——证据（及图片）放第一条 user message，指令与选项放第二条。第一条在同一次请求
  的所有问题之间完全相同，llama.cpp 可复用视觉编码和状态 prefill。延迟优先于字节兼容时用。
