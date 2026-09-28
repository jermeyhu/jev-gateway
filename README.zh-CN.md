# jev-gateway

[![Documentation](https://img.shields.io/badge/docs-jermeyhu.github.io%2Fjev--gateway-3f51b5?logo=material%2Ffor-linux)](https://jermeyhu.github.io/jev-gateway/zh/)
[![PyPI](https://img.shields.io/pypi/v/jev-gateway?color=3f51b5)](https://pypi.org/project/jev-gateway/)
[![License](https://img.shields.io/badge/license-Apache--2.0-3f51b5)](LICENSE)
[![Python](https://img.shields.io/badge/python-%3E%3D3.11-3f51b5)](https://www.python.org/)

> 完整文档：**[jermeyhu.github.io/jev-gateway](https://jermeyhu.github.io/jev-gateway/zh/)**
> （English version: [/](https://jermeyhu.github.io/jev-gateway/)）

自托管的 Jev / System One 兼容决策网关。对外只暴露你已经在调用的 `POST /v1/systemone`，
决策层跑在自己的推理服务上，不依赖闭源的 TypeSafe API，并在同一契约上扩展了本地图片输入。

网关只说一种协议：**OpenAI chat-completions API**（`POST /v1/chat/completions` 带
`logprobs`）。任何实现了它的服务都能用——llama.cpp、vLLM、SGLang、Ollama、LM Studio、
TGI、托管 API——且不会发送任何引擎专属参数。

官方 Jev 接口只接受文本（文档明确 `No image, audio, or video input`），Pydantic AI 客户端
遇到非文本 part 直接抛 `UserError: Files are not supported by this model`。本项目替换文本
路径，同时支持带截图 / 相机帧的证据打分。

## 工作方式

Jev 的一次调用是分类问题，不是生成问题。每个问题：渲染证据加带字母的选项（`A`、`B`、
`C`…），向后端请求**恰好一个** next token 的 logprobs（`max_tokens: 1`），读出每个候选
字母的 logprob，对候选做 softmax 得到概率分布。不生成任何文本，一次决策的成本就是
prompt 上的一次前向传播。这正是 0.6B–4B 小模型能充当路由、门禁和分诊分类器的原因。

## 网关 vs 直连工具调用

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

同一基准还得出两条评分标准写法的经验：

* **把判据写成可判定的。** 用模糊标签（"Total outage / Degraded / Minor"）时两条路径都
  接近随机——直连甚至把一个全绿的平凡案例判成 `sev1`。把每个候选改写成能从 state 直接
  验证的阈值（"error_rate ≥ 0.20，或整区宕机…"）后，两条路径同时改善，说明瓶颈在措辞，
  不在网关。
* **softmax 是诚实的。** 打乱候选顺序只会改变哪个*字母*胜出，不会改变哪个*标签*胜出，
  概率质量跟着证据走（全局宕机时 `sev1 ≈ 0.85`）。可以信任分布本身；阈值在自己的标注集
  上校准。

## 快速开始

```bash
pip install -e ".[dev]"           # 或 pip install -e .
cp config.yaml config.local.yaml  # 可选
JEV_GATEWAY_CONFIG=config.local.yaml python -m app
```

`JEV_GATEWAY_CONFIG` 是网关唯一读取的环境变量，默认 `./config.yaml`。监听地址取
`server.host:server.port`（默认 `0.0.0.0:8000`）。

Docker：

```bash
docker compose up -d                                   # 仅网关
docker compose -f docker-compose.llamacpp.yml up -d    # 网关 + llama-server
python scripts/smoke_test.py --url http://127.0.0.1:8000
```

下面还会提到另外两个模板，但它们**没有随仓库提供**：`docker-compose.vllm.yml` 与
`docker-compose.sglang.yml`。复制 `docker-compose.llamacpp.yml` 并改掉 `base_url` 和
`backend.model` 即可对接这两个服务，详见[后端页面](https://jermeyhu.github.io/jev-gateway/zh/backends/)。

每个模板都挂载同一个 `./config.yaml`；改模型、上下文长度或端口，直接编辑 `command` 段。
启动后端栈前先把 `.gguf` 放进 `./models`。视觉模型还需要投影权重：在 `llama-server` 命令
里加 `--mmproj /models/mmproj.gguf`，并用 `GET /props` 确认 `modalities.vision` 为 `true`。

### 指向后端

`backend.type` 恒为 `openai`，只有 `backend.base_url` 会变：

| 服务      | 宿主机 `backend.base_url` | compose 内部               | `backend.model`             |
| --------- | ------------------------ | -------------------------- | --------------------------- |
| llama.cpp | `http://127.0.0.1:8080`  | `http://llama-server:8080` | `null`（自动探测）          |
| vLLM      | `http://127.0.0.1:8001`  | `http://vllm:8000`         | 对应 `--served-model-name`  |
| SGLang    | `http://127.0.0.1:8002`  | `http://sglang:30000`      | 对应 `--served-model-name`  |

容器里的 `base_url` 绝不会是 `127.0.0.1`（那指向网关容器自己）；要访问宿主机上的服务用
`http://host.docker.internal:8080`。vLLM 和 SGLang 的 `backend.model` 必须与
`--served-model-name` 一致；留 `null` 则自动探测。

对真实后端做端到端检查：

```bash
python scripts/smoke_test.py --url http://127.0.0.1:8000
python scripts/smoke_test.py --url http://127.0.0.1:8000 --image screenshot.png
```

## 接口

### `POST /v1/systemone`

```json
{
  "state": {"error_rate": 0.42, "p99_latency_ms": 3100, "recent_deploy": true},
  "questions": {
    "is_healthy": {"type": "noul", "instructions": "Is the service healthy?"},
    "severity": {
      "type": "choice",
      "instructions": "Pick the incident severity.",
      "criteria": {
        "sev1": "error_rate is 0.20 or higher, OR a whole region is down",
        "sev2": "error_rate is 0.05 or higher but below 0.20",
        "sev3": "error_rate is below 0.05 AND users are not visibly impacted"
      }
    },
    "urgency": {
      "type": "score",
      "instructions": "How urgent is the response?",
      "criteria": ["can wait", "today", "right now"]
    }
  }
}
```

顶层可选字段：`model`（仅作提示）和 `images`（data URL、远程 URL 需开启、或裸 base64）。
`state` 接受非空字符串、对象或数组，不允许 `NaN` / `Infinity`。任何位置出现未知字段都会
被拒绝。单个问题候选数须在 2 到 **16** 之间，超过以 `INVALID_REQUEST` 拒绝。

响应：

```json
{
  "model": "Qwen3-4B-Instruct",
  "answers": {
    "is_healthy": {"type": "noul", "noul": 0.0314},
    "severity": {
      "type": "choice",
      "choice": "sev2",
      "probabilities": {"sev1": 0.024, "sev2": 0.921, "sev3": 0.055}
    },
    "urgency": {
      "type": "score",
      "score": 1.86,
      "legend": {"0": "can wait", "1": "today", "2": "right now"},
      "probabilities": {"0": 0.041, "1": 0.058, "2": 0.901}
    }
  },
  "usage": {"input_tokens": 1421, "output_tokens": 3},
  "diagnostics": {
    "backend": "openai",
    "model": "Qwen3-4B-Instruct",
    "total_latency_ms": 214.7,
    "questions": {
      "is_healthy": {"latency_ms": 61.2, "truncated": false, "backend": "openai"}
    }
  }
}
```

答案结构：

* `noul` → `{"type": "noul", "noul": p}`，`p` 是 `criteria.true` 的概率（未给 `criteria` 时按 `Yes` 计）。
* `choice` → argmax 候选加完整分布。
* `score` → 各档位下标的概率加权均值，可在档位之间插值，不会退化成一个点。

与参考实现一致的行为：所有问题**并发**执行（上限 `backend.max_concurrency`）；**任一**
问题失败则整次请求失败；`request.total_timeout_seconds` 约束整次请求，不是单个问题。

### 其他端点

| 端点             | 作用                                       |
| ---------------- | ------------------------------------------ |
| `GET /healthz`   | 存活探针，不访问后端                       |
| `GET /readyz`    | 就绪探针，探测后端，不可用返回 503         |
| `GET /v1/models` | OpenAI 形状的模型列表                      |

每个响应都带 `x-request-id` 头，可自带以关联日志。

### 错误

```json
{"error": {"code": "BACKEND_TIMEOUT", "message": "...", "detail": null, "request_id": "..."}}
```

| code                            | 状态码 | 触发条件                                     |
| ------------------------------- | ------ | -------------------------------------------- |
| `INVALID_REQUEST`               | 400    | schema / 限额违规、非法图片、不支持的能力     |
| `BACKEND_CAPABILITY_UNSUPPORTED` | 400   | 模型或后端无法接收图片                        |
| `BACKEND_PROTOCOL_ERROR`        | 502    | 后端返回了不可用的内容                        |
| `BACKEND_UNAVAILABLE`           | 502    | 连接被拒 / 后端报错                           |
| `BACKEND_NOT_READY`             | 503    | 后端未加载或不可达                            |
| `BACKEND_TIMEOUT`               | 504    | 单个问题超过 `backend.timeout_seconds`        |
| `REQUEST_TIMEOUT`               | 504    | 整次请求超过 `request.total_timeout_seconds`  |

## 图片（扩展）

图片固定挂在第一条 user message 的最前面，使视觉编码结果和证据 prefill 在同一次请求的
所有问题之间复用。接受形式：`data:image/jpeg;base64,...`、`https://...`（需
`multimodal.allow_remote_urls: true`）、或裸 base64（按 magic bytes 嗅探类型：jpeg、png、
gif、bmp、tiff、webp）。限额来自 `multimodal` 配置块。后端无视觉能力时直接以
`BACKEND_CAPABILITY_UNSUPPORTED` 失败；`backend.supports_images: false` 可彻底关闭该路径。

## 配置参考

未知键直接报错，拼写错误在启动时失败而不是被忽略。

| 段           | 键                                          | 默认值                 | 说明                                    |
| ------------ | ------------------------------------------- | ---------------------- | --------------------------------------- |
| `server`     | `host` / `port`                             | `0.0.0.0` / `8000`     |                                         |
| `backend`    | `type`                                      | `openai`               | 唯一支持的协议                          |
| `backend`    | `base_url` / `model` / `api_key`            | — / `null` / `null`    | `model: null` 时用后端自报模型名        |
| `backend`    | `timeout_seconds`                           | `30.0`                 | 单问题超时                              |
| `backend`    | `max_concurrency`                           | `32`                   | 单网关实例并发上限                      |
| `backend`    | `top_logprobs`                              | `128`                  | 范围 16–4096；窗口外候选按下界截断      |
| `backend`    | `supports_images`                           | `null`（自动）         | `true` / `false` 强制指定              |
| `backend`    | `extra_headers` / `extra_body`              | `{}`                   | 云 API key、租户标识；见「推理模型」    |
| `request`    | `max_questions`                             | `64`                   | 单次请求 1–64 个问题                    |
| `request`    | `total_timeout_seconds`                     | `60.0`                 | 整次请求预算                            |
| `request`    | `prompt_layout`                             | `fused`                | `fused` \| `split`                     |
| `multimodal` | `enabled` / `max_images` / `max_image_bytes` | `true` / `4` / `5242880` | `allow_remote_urls: false`           |
| `logging`    | `level`                                     | `INFO`                 |                                         |

### Prompt 布局

* `fused`：单条 user message 含 `{evidence, criterion, options}`，与参考网关逐字节兼容，
  在托管 API 上测出的校准结论可以直接迁移。
* `split`：证据（及图片）放第一条 user message，指令与选项放第二条。第一条在同一次请求
  的所有问题之间完全相同，llama.cpp 可复用视觉编码和状态 prefill。延迟优先于字节兼容时用。

## 后端

网关发送 `POST /v1/chat/completions`，带 `max_tokens: 1`、`logprobs: true` 和
`top_logprobs: N`，从 `choices[0].logprobs.content[0].top_logprobs` 读取 next-token 分布。
每个返回的 token 都映射到候选字母（`"A"` 和 `" A"` 都算），同一字母取概率最高的变体。
落在 top-N 窗口外的候选按下界截断到实际返回的最低 logprob，并把该问题标记 `truncated`；
看到这个标记就上调 `backend.top_logprobs`（最高 4096）。

`backend.supports_images: null` 即「自动」：假定后端能接收图片，图片缺失时不投递。模型
没有视觉塔时显式设为 `false`，带图请求会直接快速失败，而不会打到后端。

### 推理模型

*推理*模型（Qwen3 / Qwen3.5、DeepSeek-R1、OpenAI o 系列……）会把思考内容作为第一个 token
输出，于是没有任何字母落进 top-N 窗口，每个问题都以 `BACKEND_PROTOCOL_ERROR` 失败。网关
不会替你猜怎么关；把 `backend.extra_body` 设成你的服务认识的写法即可：

| 后端                             | `backend.extra_body`                             |
| -------------------------------- | ------------------------------------------------ |
| llama.cpp / vLLM / SGLang（Qwen3） | `chat_template_kwargs: {enable_thinking: false}` |
| OpenAI o 系列 / GPT-5            | `reasoning_effort: none`                         |
| OpenRouter（任意推理模型）       | `reasoning: {enabled: false}`                    |
| DeepSeek `deepseek-reasoner`     | *（无关闭开关——改用 `deepseek-chat`）*           |

`extra_body` 会合并进每个请求，但永远不会覆盖 `messages`、`max_tokens`、`logprobs`、
`top_logprobs`。非推理模型保持 `{}` 即可。

## 脚本

决策模型对**校准**敏感：阈值门禁（`p < 0.7 转人工`）成立的前提是概率诚实，而激进量化可能
保住 argmax 却毁掉置信度。用于门禁的模型优先 Q8_0 或 F16，Q4_K_M 按实验对待。

| 脚本                     | 用途                                                                 |
| ------------------------ | -------------------------------------------------------------------- |
| `smoke_test.py`          | 对运行中的网关做端到端健康检查（`--image` 测视觉）                   |
| `compare_backends.py`    | 同一组用例 A/B 多个服务；argmax 一致性、概率偏移、Brier 距离         |
| `bench_triage.py`        | 正确率：网关 logprobs vs 直连工具调用（SRE 分诊案例）                |
| `bench_speed_tokens.py`  | 速度 + 输出 token：网关 vs 直连工具调用（`--repeat N`）              |
| `diagnose_severity.py`   | 模糊 vs 精确评分标准在两条路径上的对照（区分措辞问题与设计问题）     |
| `probe_position_bias.py` | 打乱候选顺序，区分位置偏见与语义判断                                 |
| `probe_live.py`          | 对活动网关做边界检查（图片限额、候选数限额）                         |
| `make_test_png.py`       | 不依赖 Pillow 生成一张极小的合法 PNG                                 |

`compare_backends.py` 用同一组用例跑多个服务，只报告真正要紧的指标：

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

* **argmax agreement**：排序是否保住；
* **mean|dp|**：概率向量整体移动了多远；
* **Brier distance**：两个概率向量的均方差距。要盯的是这个数——argmax 一致但 Brier 距离大，
  意味着换量化或换后端已经悄悄改坏了阈值。

`--backend` 格式为 `NAME=URL[+key:value]`，可选值有 `model`、`api_key`、
`top_logprobs`、`supports_images`。

## 开发

```bash
pip install -e ".[dev]"
pytest -q
ruff check .
mypy app
```

测试覆盖 prompt 布局、图片解析、logprob → 答案的换算、后端对 mock transport 的行为、
配置校验、HTTP 层，以及一次进程内端到端请求。不需要网络或 GPU。

## 文档

完整文档在 **[jermeyhu.github.io/jev-gateway](https://jermeyhu.github.io/jev-gateway/zh/)**
（English: [/](https://jermeyhu.github.io/jev-gateway/)），由 `docs/` 用 MkDocs Material
构建。页面包括：[快速开始](https://jermeyhu.github.io/jev-gateway/zh/quick-start/)、
[工作方式](https://jermeyhu.github.io/jev-gateway/zh/how-it-works/)、
[接口参考](https://jermeyhu.github.io/jev-gateway/zh/api/)、
[配置参考](https://jermeyhu.github.io/jev-gateway/zh/configuration/)、
[后端](https://jermeyhu.github.io/jev-gateway/zh/backends/)、
[图片](https://jermeyhu.github.io/jev-gateway/zh/images/)、
[脚本](https://jermeyhu.github.io/jev-gateway/zh/scripts/)、
[限制与 FAQ](https://jermeyhu.github.io/jev-gateway/zh/limits/) 和
[开发](https://jermeyhu.github.io/jev-gateway/zh/development/)。

本地构建：

```bash
pip install -r requirements-docs.txt
mkdocs serve        # http://127.0.0.1:8001
mkdocs build --strict
```

## 已知限制

* `noul` 概率是只对两个候选字母做 softmax；阈值要在自己的标注集上校准。
* 单个问题最多 **16 个候选**，超出以 `INVALID_REQUEST` 拒绝。
* 诊断里出现 `truncated: true` 说明有候选被截断；上调 `top_logprobs`。
* 图片是对参考契约的扩展，按托管服务写的客户端仍可运行，但会忽略该字段。
* 超出后端真实能力的部分应靠水平扩展解决，而不是继续调高 `max_concurrency`。

## 致谢

请求/响应契约与 logprob 分类的思路参考了
[David-Lolly/Jev-Compatible](https://github.com/David-Lolly/Jev-Compatible)——感谢这个
本网关保持兼容的参考实现。

## 许可

Apache-2.0。
