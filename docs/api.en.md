# API reference

## `POST /v1/systemone`

The single decision endpoint.

### Request

```json
{
  "state": {"error_rate": 0.42, "p99_latency_ms": 3100, "recent_deploy": true},
  "model": "Qwen3-4B-Instruct",
  "images": ["data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=="],
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

| field       | type                | required | notes                                                        |
| ----------- | ------------------- | -------- | ------------------------------------------------------------ |
| `state`     | string, object, list | yes    | must be non-empty; no `NaN` / `Infinity`                      |
| `questions` | object              | yes      | 1 – `request.max_questions` (max 64) entries                  |
| `model`     | string              | no       | advisory only; the backend's own model is reported back       |
| `images`    | array of strings    | no       | data URLs, opt-in remote URLs, or raw base64                   |

Every question has a `type` (`noul`, `choice` or `score`) and an `instructions` string.
`choice` and `score` also require `criteria`; `noul` accepts an optional two-key
`criteria` that overrides the `Yes` / `No` labels.

**Unknown keys anywhere are rejected.** A question carries 2 – **16** candidates; more is
rejected with `INVALID_REQUEST`.

### Response

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

`answers` always has the same keys as the request's `questions`, in the request's order.

| answer type | shape                                                                          |
| ----------- | ------------------------------------------------------------------------------ |
| `noul`      | `{"type": "noul", "noul": p}` — the probability of `criteria.true` (or `Yes`)   |
| `choice`    | `{"type": "choice", "choice": key, "probabilities": {...}}` — argmax plus the full distribution over candidate keys |
| `score`     | `{"type": "score", "score": expected, "legend": {...}, "probabilities": {...}}` — the probability-weighted mean of the level indices, so it interpolates between levels instead of collapsing to one |

`usage.output_tokens` is the number of questions in practice: one decoded token each.

`diagnostics.questions.<id>.truncated` is `true` when a candidate letter was outside the
returned `top_logprobs` window and had to be floored. See
[Backends](backends#reading-the-top-n-window).

### Semantics

Matching the reference implementation:

* questions run **concurrently**, bounded by `backend.max_concurrency`;
* **any** failing question fails the whole request;
* `request.total_timeout_seconds` bounds the whole request, not each question.

## Other endpoints

| endpoint         | purpose                                       |
| ---------------- | --------------------------------------------- |
| `GET /healthz`   | liveness; never touches the backend            |
| `GET /readyz`    | readiness; probes the backend, 503 when down   |
| `GET /v1/models` | OpenAI-shaped model list                      |

`/healthz` answers `{"status":"ok","version":"0.1.0"}` without a backend call, so it is
safe as a container healthcheck. `/readyz` probes the backend and returns
`{"status":"not_ready"}` with 503 when the inference server is unreachable or not loaded.
`/v1/models` returns the backend's model list in the OpenAI shape.

Every response carries an `x-request-id` header; send your own to correlate logs.

## Errors

```json
{"error": {"code": "BACKEND_TIMEOUT", "message": "...", "detail": null, "request_id": "..."}}
```

| code                             | status | when                                                    |
| -------------------------------- | ------ | ------------------------------------------------------- |
| `INVALID_REQUEST`                | 400    | schema/limit violation, bad image, unsupported feature  |
| `BACKEND_CAPABILITY_UNSUPPORTED` | 400    | the model or backend cannot serve images                 |
| `BACKEND_PROTOCOL_ERROR`         | 502    | backend answered with something unusable                 |
| `BACKEND_UNAVAILABLE`            | 502    | connection refused / backend errored                     |
| `BACKEND_NOT_READY`              | 503    | backend not loaded or not reachable                      |
| `BACKEND_TIMEOUT`                | 504    | one question exceeded `backend.timeout_seconds`          |
| `REQUEST_TIMEOUT`                | 504    | the request exceeded `request.total_timeout_seconds`     |

The `error` object always has `code`, `message`, `detail` and `request_id`. Validation
errors from the request schema land in the same shape, so a client only needs one error
parser.
