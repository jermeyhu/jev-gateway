# API reference

## `POST /v1/systemone`

The single decision endpoint.

### Request

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

Requests containing images must also send `TypeSafe-Danger-Zone-Images: true`. Send image
bytes in `assets.data_base64` or upload them as multipart file parts. Each asset reference
must appear in `state`, an instruction, or criteria.

The request body limit is 32 MiB both before and after decompression. `Content-Encoding:
gzip`, `x-gzip`, and `zstd` are supported. `state`, `instructions`, and `criteria` may be
nested up to 64 levels and accept finite numbers only.

| field       | type                | required | notes                                                        |
| ----------- | ------------------- | -------- | ------------------------------------------------------------ |
| `state`     | string, object, list | yes      | must be non-empty; no `NaN` / `Infinity`                      |
| `model`     | string              | yes      | model name or alias                                           |
| `questions` | object              | yes      | 1 – `request.max_questions` (max 64) entries                  |
| `assets`    | object              | no       | image references mapped to assets; see [Images](images.en.md) |

`instructions`, Noul `criteria.true/false`, Choice descriptions, and Score levels accept
strings, objects, or arrays. Either Noul criterion may be omitted; Choice descriptions may
also be `null`. Choice and Score require `criteria`; Score accepts 2 – 10 levels. `box`
accepts only `instructions` and requires a referenced image.

**Unknown keys anywhere are rejected.** This gateway currently allows 2 – 16 Choice options
and up to 10 Score levels.

### Response

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

`answers` always has the same keys as the request's `questions`, in the request's order.

| answer type | shape                                                                          |
| ----------- | ------------------------------------------------------------------------------ |
| `noul`      | `{"type": "noul", "noul": p}` — the probability of `criteria.true` (or `Yes`)   |
| `choice`    | `{"type": "choice", "choice": key, "probabilities": {...}, "confidence": p}` — argmax, full distribution, and confidence |
| `score`     | `{"type": "score", "score": expected, "legend": {...}, "probabilities": {...}, "confidence": p}` — probability-weighted mean over levels |
| `box`       | `{"type": "box", "box": {"top": y1, "bottom": y2, "left": x1, "right": x2}, "confidence": p}` — normalized edges from 0 to 1 |

Noul answers do not include `confidence`; Choice and Score confidence is computed from the
probability distribution. Box edges are between 0 and 1, with `left < right` and
`top < bottom`.

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
| `INVALID_REQUEST`                | 422    | JSON request schema validation failed                  |
| `BACKEND_CAPABILITY_UNSUPPORTED` | 400    | the model or backend cannot serve images                 |
| `BACKEND_PROTOCOL_ERROR`         | 502    | backend answered with something unusable                 |
| `BACKEND_UNAVAILABLE`            | 502    | connection refused / backend errored                     |
| `BACKEND_NOT_READY`              | 503    | backend not loaded or not reachable                      |
| `BACKEND_TIMEOUT`                | 504    | one question exceeded `backend.timeout_seconds`          |
| `REQUEST_TIMEOUT`                | 504    | the request exceeded `request.total_timeout_seconds`     |
| `PAYLOAD_TOO_LARGE`              | 413    | compressed or decompressed body exceeds 32 MiB           |
| `UNSUPPORTED_MEDIA_TYPE`         | 415    | unsupported `Content-Encoding`                           |

The `error` object always has `code`, `message`, `detail` and `request_id`. Validation
errors from the request schema land in the same shape, so a client only needs one error
parser.
