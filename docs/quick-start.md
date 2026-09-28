# 快速开始 {#quick-start}

## 安装 {#install}

```bash
pip install -e ".[dev]"           # 或 pip install -e .
```

需要 Python 3.11 或更新版本。运行时依赖为 `fastapi`、`httpx`、`pydantic` 和
`uvicorn[standard]`。

## 配置 {#configure}

配置全部是 `JEV_` 前缀的环境变量，**没有配置文件**。不设置就用默认值，空环境下也能直接启
动。想集中管理就用模板：

```bash
cp .env.example .env      # .env 不入库，改它不会污染仓库
$EDITOR .env
```

真正要改的是 `JEV_BACKEND_BASE_URL`——你的 OpenAI 兼容服务的地址：

```bash
JEV_BACKEND_BASE_URL=http://127.0.0.1:8080
JEV_BACKEND_MODEL=                      # 留空 = 从服务的 /v1/models 自动探测
```

全部变量、默认值和取值范围见[配置参考](configuration.md)。监听地址取
`JEV_SERVER_HOST:JEV_SERVER_PORT`（默认 `0.0.0.0:8000`）。

## 运行 {#run}

```bash
set -a; . ./.env; set +a     # 把 .env 导进当前 shell
python -m app
```

已经 export 好的话，等价的控制台脚本是 `jev-gateway`。配置也可以直接写在命令行上，不建文件：

```bash
JEV_BACKEND_BASE_URL=http://192.168.1.10:8080 python -m app
```

确认它活着：

```bash
curl http://127.0.0.1:8000/healthz
# {"status":"ok","version":"0.1.0"}
```

## 发一次决策 {#send-a-decision}

```bash
curl -X POST http://127.0.0.1:8000/v1/systemone \
  -H 'content-type: application/json' \
  -d '{
    "state": {"error_rate": 0.42, "p99_latency_ms": 3100, "recent_deploy": true},
    "questions": {
      "is_healthy": {"type": "noul", "instructions": "Is the service healthy?"},
      "severity": {
        "type": "choice",
        "instructions": "Pick the incident severity.",
        "criteria": {"sev1": "a whole region is down", "sev2": "error_rate is at least 0.05", "sev3": "below 0.05 and users are unaffected"}
      },
      "urgency": {
        "type": "score",
        "instructions": "How urgent is the response?",
        "criteria": ["can wait", "today", "right now"]
      }
    }
  }'
```

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
  }
}
```

一次请求，三个决策，三个输出 token。

## Docker {#docker}

```bash
docker compose up -d                                   # 仅网关
docker compose -f docker-compose.llamacpp.yml up -d    # 网关 + llama-server
```

每个模板都通过 `environment:` 传配置，**不挂载任何文件**。改后端地址就改那个变量；改模型、
上下文长度或端口，直接编辑 `command` 段。启动后端栈前先把 `.gguf` 放进 `./models`。视觉模型
还需要投影权重：在 `llama-server` 命令里加 `--mmproj /models/mmproj.gguf`，并用
`GET /props` 确认 `modalities.vision` 为 `true`。

## 端到端验证 {#verify-end-to-end}

```bash
curl -s http://127.0.0.1:8000/healthz
curl -s http://127.0.0.1:8000/readyz

curl -s http://127.0.0.1:8000/v1/systemone \
  -H 'content-type: application/json' \
  -d '{
        "state": {"error_rate": 0.31, "p99_latency_ms": 4100},
        "questions": {
          "severity": {
            "type": "choice",
            "instructions": "Pick the severity.",
            "criteria": {
              "sev1": "error_rate >= 0.20",
              "sev2": "error_rate >= 0.05 but < 0.20",
              "sev3": "error_rate < 0.05"
            }
          }
        }
      }'
```

`/healthz` 证明进程活着，`/readyz` 证明后端已就绪并返回它加载的模型名，最后一个请求
会返回答案、`usage` 与 `diagnostics`。带图验证把 `state` 换成
`{"images": ["data:image/png;base64,..."]}`。

!!! note "vLLM 与 SGLang"

    仓库里只随附 llama.cpp 那套 compose。要对接 vLLM 或 SGLang，把 `backend.base_url`
    指过去，并把 `backend.model` 设成服务启动时用的名字。见[后端](backends.md)。

## 接下来 {#where-to-go-next}

* [工作方式](how-it-works.md)——分类技巧与 prompt 布局。
* [接口参考](api.md)——所有字段、端点与错误码。
* [后端](backends.md)——llama.cpp / vLLM / SGLang 的 `base_url` 对照表。
