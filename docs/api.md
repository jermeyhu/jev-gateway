# 接口参考 {#api-reference}

## `POST /v1/systemone`

唯一的决策端点。

### 请求 {#request}

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

| 字段        | 类型                   | 必填 | 说明                                                |
| ----------- | ---------------------- | ---- | --------------------------------------------------- |
| `state`     | 字符串、对象或数组     | 是   | 不能为空；不允许 `NaN` / `Infinity`                 |
| `questions` | 对象                   | 是   | 1 – `request.max_questions`（最多 64）个条目        |
| `model`     | 字符串                 | 否   | 仅作提示；响应中回报后端自己的模型名                |
| `images`    | 字符串数组             | 否   | data URL、需开启的远程 URL，或裸 base64             |

每个问题都有一个 `type`（`noul`、`choice` 或 `score`）和一条 `instructions`。
`choice` 和 `score` 还要求 `criteria`；`noul` 可以带一个两键的 `criteria`，用来覆盖
`Yes` / `No` 这两个默认标签。

**任何位置出现未知字段都会被拒绝。** 单个问题须有 2 – **16** 个候选；更多则以
`INVALID_REQUEST` 拒绝。

### 响应 {#response}

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

`answers` 的键与请求中 `questions` 的键一致，顺序也一致。

| 答案类型  | 结构                                                                          |
| --------- | ----------------------------------------------------------------------------- |
| `noul`    | `{"type": "noul", "noul": p}`——`criteria.true`（或 `Yes`）的概率             |
| `choice`  | `{"type": "choice", "choice": key, "probabilities": {...}}`——argmax 候选加全部候选键上的完整分布 |
| `score`   | `{"type": "score", "score": expected, "legend": {...}, "probabilities": {...}}`——档位下标的概率加权均值，可在档位之间插值而不是退化成一个点 |

`usage.output_tokens` 实际上就是问题数：每题一个解码 token。

`diagnostics.questions.<id>.truncated` 为 `true` 表示某个候选字母落在返回的 `top_logprobs`
窗口之外、只能被截断到下界。见[后端](backends.md#reading-the-top-n-window)。

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
| `BACKEND_CAPABILITY_UNSUPPORTED` | 400   | 模型或后端无法接收图片                        |
| `BACKEND_PROTOCOL_ERROR`        | 502    | 后端返回了不可用的内容                        |
| `BACKEND_UNAVAILABLE`           | 502    | 连接被拒 / 后端报错                           |
| `BACKEND_NOT_READY`             | 503    | 后端未加载或不可达                            |
| `BACKEND_TIMEOUT`               | 504    | 单个问题超过 `backend.timeout_seconds`        |
| `REQUEST_TIMEOUT`               | 504    | 整次请求超过 `request.total_timeout_seconds`  |

`error` 对象始终包含 `code`、`message`、`detail` 和 `request_id`。请求 schema 的校验错误
也落进同样的结构，客户端因此只需要一个错误解析器。
