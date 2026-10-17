# 接口参考 {#api-reference}

## `POST /v1/systemone`

唯一的决策端点。

### 请求 {#request}

```json
{
  "state": {"error_rate": 0.42, "p99_latency_ms": 3100, "recent_deploy": true},
  "model": "jev-latest",
  "assets": {
    "screen-uuid": {"type": "image", "format": "png", "data_base64": "iVBORw0KGgo..."}
  },
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
    },
    "button": {
      "type": "box",
      "instructions": "the primary button in `screen-uuid`"
    }
  }
}
```

包含图片时，请求还需带 `TypeSafe-Danger-Zone-Images: true`。图片可用 `assets` 中的
`data_base64` 发送，也可用 multipart 文件 part 上传；图片引用应出现在 `state`、问题的
`instructions` 或 `criteria` 中。

请求体上限为 32 MiB（压缩前后分别计）；支持 `Content-Encoding: gzip`、`x-gzip` 和
`zstd`。`state`、`instructions`、`criteria` 最多嵌套 64 层，只接受有限数值。

| 字段        | 类型                   | 必填 | 说明                                                |
| ----------- | ---------------------- | ---- | --------------------------------------------------- |
| `state`     | 字符串、对象或数组     | 是   | 不能为空；不允许 `NaN` / `Infinity`                 |
| `model`     | 字符串                 | 是   | 模型名或别名                                         |
| `questions` | 对象                   | 是   | 1 – `request.max_questions`（最多 64）个条目        |
| `assets`    | 对象                   | 否   | 图片引用到 asset 的映射，详见[图片](images.md)     |

`instructions`、noul 的 `criteria.true/false`、Choice 的候选描述和 Score 的档位都可为
字符串、对象或数组。Noul 的 true/false 描述可分别省略；Choice 候选描述还可为 `null`。
`choice` 和 `score` 要求 `criteria`；Score 接受 2 – 10 个档位。`box` 只接受
`instructions`，并要求关联图片。

**任何位置出现未知字段都会被拒绝。** 当前网关允许 2 – 16 个 Choice 候选；Score 上限为 10。

### 响应 {#response}

```json
{
  "model": "Qwen3-4B-Instruct",
  "answers": {
    "is_healthy": {"type": "noul", "noul": 0.0314},
    "severity": {
      "type": "choice",
      "choice": "sev2",
      "probabilities": {"sev1": 0.024, "sev2": 0.921, "sev3": 0.055},
      "confidence": 0.882
    },
    "urgency": {
      "type": "score",
      "score": 1.86,
      "legend": {"0": "can wait", "1": "today", "2": "right now"},
      "probabilities": {"0": 0.041, "1": 0.058, "2": 0.901},
      "confidence": 0.81
    },
    "button": {
      "type": "box",
      "box": {"top": 0.25, "bottom": 0.82, "left": 0.44, "right": 0.84},
      "confidence": 0.92
    }
  },
  "usage": {"input_tokens": 1421, "output_tokens": 3}
}
```

`answers` 的键与请求中 `questions` 的键一致，顺序也一致。

| 答案类型  | 结构                                                                          |
| --------- | ----------------------------------------------------------------------------- |
| `noul`    | `{"type": "noul", "noul": p}`——`criteria.true`（或 `Yes`）的概率             |
| `choice`  | `{"type": "choice", "choice": key, "probabilities": {...}, "confidence": p}`——argmax 候选、完整概率分布和置信度 |
| `score`   | `{"type": "score", "score": expected, "legend": {...}, "probabilities": {...}, "confidence": p}`——档位下标的概率加权均值，可在档位间插值 |
| `box`     | `{"type": "box", "box": {"top": y1, "bottom": y2, "left": x1, "right": x2}, "confidence": p}`——0 到 1 的归一化边界坐标 |

Noul 答案不包含 `confidence`；Choice 和 Score 的 confidence 按概率分布计算。Box 的四条边
均位于 0 到 1，且 `left < right`、`top < bottom`。

### 语义 {#semantics}

与参考实现保持一致：

* 所有问题**并发**执行，上限为 `backend.max_concurrency`；
* **任一**问题失败则整次请求失败；
* `request.total_timeout_seconds` 约束整次请求，不是单个问题。

## 其他端点 {#other-endpoints}

| 端点             | 作用                                       |
| ---------------- | ------------------------------------------ |
| `GET /healthz`   | 存活探针，不访问后端                       |
| `GET /readyz`    | 就绪探针，探测后端，不可用返回 503         |
| `GET /v1/models` | OpenAI 形状的模型列表                      |

`/healthz` 返回 `{"status":"ok","version":"0.1.0"}` 且不打后端，因此可以安全地用作容器
健康检查。`/readyz` 会探测后端，推理服务不可达或未加载完成时返回 `{"status":"not_ready"}`
与 503。`/v1/models` 以 OpenAI 的形状返回后端的模型列表。

每个响应都带 `x-request-id` 头，可自带以关联日志。

## 错误 {#errors}

```json
{"error": {"code": "BACKEND_TIMEOUT", "message": "...", "detail": null, "request_id": "..."}}
```

| code                            | 状态码 | 触发条件                                     |
| ------------------------------- | ------ | -------------------------------------------- |
| `INVALID_REQUEST`               | 400    | schema / 限额违规、非法图片、不支持的能力     |
| `INVALID_REQUEST`               | 422    | JSON 请求 schema 校验失败                     |
| `BACKEND_CAPABILITY_UNSUPPORTED` | 400   | 模型或后端无法接收图片                        |
| `BACKEND_PROTOCOL_ERROR`        | 502    | 后端返回了不可用的内容                        |
| `BACKEND_UNAVAILABLE`           | 502    | 连接被拒 / 后端报错                           |
| `BACKEND_NOT_READY`             | 503    | 后端未加载或不可达                            |
| `BACKEND_TIMEOUT`               | 504    | 单个问题超过 `backend.timeout_seconds`        |
| `REQUEST_TIMEOUT`               | 504    | 整次请求超过 `request.total_timeout_seconds`  |
| `PAYLOAD_TOO_LARGE`             | 413    | 压缩或解压后的请求体超过 32 MiB               |
| `UNSUPPORTED_MEDIA_TYPE`        | 415    | `Content-Encoding` 不受支持                   |

`error` 对象始终包含 `code`、`message`、`detail` 和 `request_id`。请求 schema 的校验错误
也落进同样的结构，客户端因此只需要一个错误解析器。
