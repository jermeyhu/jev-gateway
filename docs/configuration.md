# 配置参考 {#configuration}

网关的全部配置都是环境变量，统一以 `JEV_` 开头。**没有配置文件**——不存在的变量直接用
默认值，所以空环境下也能启动。仓库里的 `.env.example` 是带注释的模板，`cp .env.example
.env` 即可；`.env` 本身在 `.gitignore` 里，你填的地址和 key 不会进版本库。

```bash
cp .env.example .env
$EDITOR .env
```

Docker Compose 会自动读取同目录的 `.env`。本地跑的时候把变量导进 shell 即可：

```bash
set -a; . ./.env; set +a
python -m app
```

也可以完全不建文件，直接在命令行上给：

```bash
JEV_BACKEND_BASE_URL=http://192.168.1.10:8080 JEV_BACKEND_MODEL=qwen3-0.6b python -m app
```

**空值等于未设置**，所以 `JEV_BACKEND_MODEL=` 和不写这个变量效果一样。**值不合法直接
报错**，并且在错误信息里点名是哪个变量——拼写错误在启动时失败，而不是被静默忽略。

## 完整对照 {#reference}

| 变量                                     | 默认值             | 说明                                 |
| ---------------------------------------- | ------------------ | ------------------------------------ |
| `JEV_SERVER_HOST`                        | `0.0.0.0`          |                                      |
| `JEV_SERVER_PORT`                        | `8000`             |                                      |
| `JEV_BACKEND_TYPE`                       | `openai`           | 唯一支持的协议                       |
| `JEV_BACKEND_BASE_URL`                   | `http://127.0.0.1:8080` |                              |
| `JEV_BACKEND_MODEL`                      | 空（自动探测）     | 为空时用后端自报模型名               |
| `JEV_BACKEND_API_KEY`                    | 空                 | 仅托管 API 需要                      |
| `JEV_BACKEND_TIMEOUT_SECONDS`            | `30.0`             | 单问题超时                           |
| `JEV_BACKEND_MAX_CONCURRENCY`            | `32`               | 单网关实例并发上限                   |
| `JEV_BACKEND_TOP_LOGPROBS`               | `128`              | 16 – 4096；窗口外候选按下界截断      |
| `JEV_BACKEND_SUPPORTS_IMAGES`            | `auto`             | `true` / `false` 强制指定            |
| `JEV_BACKEND_EXTRA_HEADERS`              | `{}`               | JSON 对象；云 API key、租户标识      |
| `JEV_BACKEND_EXTRA_BODY`                 | `{}`               | JSON 对象；「关思考」开关写这里      |
| `JEV_REQUEST_MAX_QUESTIONS`              | `64`               | 单次请求 1 – 64 个问题               |
| `JEV_REQUEST_TOTAL_TIMEOUT_SECONDS`      | `60.0`             | 整次请求预算                         |
| `JEV_REQUEST_PROMPT_LAYOUT`              | `fused`            | `fused` \| `split`                  |
| `JEV_MULTIMODAL_ENABLED`                 | `true`             | 为 `false` 时任何 `images` 都报 400  |
| `JEV_MULTIMODAL_MAX_IMAGES`              | `4`                | 单次请求的图片数                     |
| `JEV_MULTIMODAL_MAX_IMAGE_BYTES`         | `5242880`（5 MiB） | 单图，base64 解码后计算             |
| `JEV_MULTIMODAL_ALLOW_REMOTE_URLS`       | `false`            | 开启后才接受 `https://` 图片引用    |
| `JEV_MULTIMODAL_ALLOWED_MIME_PREFIXES`   | `image/`           | 逗号分隔                            |
| `JEV_LOGGING_LEVEL`                      | `INFO`             |                                      |

`JEV_BACKEND_BASE_URL` 末尾的斜杠会被去掉，所以 `http://127.0.0.1:8080/` 和
`http://127.0.0.1:8080` 等价。

布尔值接受 `true` / `false`、`1` / `0`、`yes` / `no`、`on` / `off`，不区分大小写。
`JEV_BACKEND_SUPPORTS_IMAGES` 额外接受 `auto`。

## 各段说明 {#sections}

### 服务器

`JEV_SERVER_HOST` / `JEV_SERVER_PORT` 是监听地址，默认 `0.0.0.0:8000`，容器镜像和 Docker
Compose 模板用的就是这个。

### 后端 `JEV_BACKEND_*` {#backend}

`TYPE` 恒为 `openai`——网关只说一种协议。不同服务之间只有 `BASE_URL` 会变，见
[后端](backends.md#base_url-by-server)。

`MODEL` 留空表示自动探测：网关会问服务的 `/v1/models` 并取它报告的第一个模型。会报出
多个模型的服务，或要求客户端指明用哪个的服务（vLLM、开了多个 served model 的 SGLang），
应显式固定。

`TOP_LOGPROBS` 是 next-token 窗口的大小。两到四个候选时默认的 `128` 已经相当宽裕；看到
诊断里的 `truncated: true` 就上调，取值范围 16 – 4096。

!!! warning "服务端都有自己的上限，默认 128 可能直接让所有请求失败"

    OpenAI 规范把 `top_logprobs` 上限定在 20，而不少服务端的上限**比这更低**。超限时后端
    **不报错**，只是把 `logprobs` 变成 `null`，表现为
    `BACKEND_PROTOCOL_ERROR: backend response contained no logprobs object`。这是换后端时
    最难查的故障，因为它返回 `HTTP 200`、日志里也没有任何错误信息。

    换新后端时先用 `20 / 24 / 32 / 64 / 128` 试出上限（见
    [后端](backends.md#silent-logprobs-null)），再把能用的最大值填进这个变量。

`SUPPORTS_IMAGES=auto` 即「自动」：假定后端能接收图片，图片缺失时就不投递。模型没有
视觉塔时显式设为 `false`，图片请求会以 `BACKEND_CAPABILITY_UNSUPPORTED` 快速失败，而
不会打到后端。

`EXTRA_HEADERS` 会作为 HTTP 头合并进每个请求——云 API key、租户 id、项目名。值是一个 JSON
对象，shell 里记得给引号：

```bash
JEV_BACKEND_EXTRA_HEADERS='{"X-Tenant": "acme"}'
```

`EXTRA_BODY` 同理合并进 JSON body；服务端「关思考」的开关就写在这里：

```bash
JEV_BACKEND_EXTRA_BODY='{"chat_template_kwargs": {"enable_thinking": false}}'
```

推理模型漏出的 `reasoning` 字段会占用 `max_tokens: 1` 里唯一那个 token，于是 `logprobs`
变成 `null`。不同后端认的写法不同（llama.cpp / vLLM 认 `chat_template_kwargs`，
OpenRouter 的 `openrouter/qwen/*` 只认 `reasoning: {effort: "none"}`），**不确定时可以两种
都写**，不认识的 key 会被静默忽略：

```bash
JEV_BACKEND_EXTRA_BODY='{"chat_template_kwargs": {"enable_thinking": false}, "reasoning": {"effort": "none"}}'
```

两者都无法覆盖 `messages`、`max_tokens`、`logprobs` 和 `top_logprobs`。

### 请求 `JEV_REQUEST_*` {#request}

`MAX_QUESTIONS` 限制单次请求能带多少问题（1 – 64）。超出的请求以 `INVALID_REQUEST` 拒绝。

`TOTAL_TIMEOUT_SECONDS` 是整次请求的总预算，所有问题加起来。由于问题并发执行，一个 64
问题、60 秒预算的请求，耗时大致仍等于最慢的那一个问题——除非后端已经饱和。

`PROMPT_LAYOUT` 可选 `fused`（默认，与参考网关逐字节兼容）或 `split`（对缓存友好）。
两者都在[工作方式](how-it-works.md#prompt-layouts)里有说明。

### 多模态 `JEV_MULTIMODAL_*` {#multimodal}

| 变量                            | 默认值               | 含义                              |
| ------------------------------- | -------------------- | --------------------------------- |
| `ENABLED`                       | `true`               | 为 `false` 时任何 `images` 都报 400 |
| `MAX_IMAGES`                    | `4`                  | 单次请求的图片数                  |
| `MAX_IMAGE_BYTES`               | `5242880`（5 MiB）   | 单图，base64 解码后计算           |
| `ALLOW_REMOTE_URLS`             | `false`              | 开启后才接受 `https://` 图片引用 |
| `ALLOWED_MIME_PREFIXES`         | `image/`             | 逗号分隔的 MIME 前缀              |

`ALLOW_REMOTE_URLS` 默认为 `false`，因为远程 URL 会让网关去取调用方指定的任意地址——这是
一个服务端请求伪造面。只有两端都归你所有时才应该打开。

### 日志 `JEV_LOGGING_LEVEL` {#logging}

接受标准 Python 日志级别：`DEBUG`、`INFO`、`WARNING`、`ERROR`。默认 `INFO`。
每个响应本来就带 `x-request-id`，日志用的也是同一个 id，排查时直接 grep 它即可。

## Prompt 布局一句话版 {#prompt-layouts-in-one-line}

* `fused`——单条 user message 含 `{evidence, criterion, options}`，与参考网关逐字节兼容，
  在托管 API 上测出的校准结论可以直接迁移。
* `split`——证据（及图片）放第一条 user message，指令与选项放第二条。第一条在同一次请求
  的所有问题之间完全相同，llama.cpp 可复用视觉编码和状态 prefill。延迟优先于字节兼容时用。
