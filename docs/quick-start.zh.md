# 快速开始 {#quick-start}

## 安装 {#install}

```bash
pip install -e ".[dev]"           # 或 pip install -e .
```

需要 Python 3.11 或更新版本。运行时依赖为 `fastapi`、`httpx`、`pydantic`、`pyyaml` 和
`uvicorn[standard]`。

## 配置 {#configure}

```bash
cp config.yaml config.local.yaml  # 可选，保留仓库里的原文件不动
$EDITOR config.local.yaml
```

`JEV_GATEWAY_CONFIG` 是网关唯一读取的环境变量，默认 `./config.yaml`。监听地址取
`server.host:server.port`（默认 `0.0.0.0:8000`）。

真正要改的是 `backend.base_url`——你的 OpenAI 兼容服务的地址：

```yaml
backend:
  type: openai
  base_url: http://127.0.0.1:8080
  model: null          # null = 从服务的 /v1/models 自动探测
```

## 运行 {#run}

```bash
JEV_GATEWAY_CONFIG=config.local.yaml python -m app
```

如果已经设置了环境变量，等价的控制台脚本是 `jev-gateway`，不需要在命令行再传配置路径。

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

每个模板都挂载同一个 `./config.yaml`；改模型、上下文长度或端口，直接编辑 `command` 段。
启动后端栈前先把 `.gguf` 放进 `./models`。视觉模型还需要投影权重：在 `llama-server` 命令
里加 `--mmproj /models/mmproj.gguf`，并用 `GET /props` 确认 `modalities.vision` 为 `true`。

## 端到端验证 {#verify-end-to-end}

```bash
python scripts/smoke_test.py --url http://127.0.0.1:8000
python scripts/smoke_test.py --url http://127.0.0.1:8000 --image screenshot.png
```

冒烟脚本会访问 `/healthz`、`/readyz` 和 `/v1/systemone`，校验每个答案的结构是否正确，并
打印 usage 与 diagnostics。

!!! note "vLLM 与 SGLang 模板"

    README 里还出现了 `docker-compose.vllm.yml` 和 `docker-compose.sglang.yml` 的命令，
    但仓库中并没有附带这两个文件。请自行启动 vLLM / SGLang 服务，再把 `base_url` 指过去。
    见[后端](backends.zh.md)。

## 接下来 {#where-to-go-next}

* [工作方式](how-it-works.zh.md)——分类技巧与 prompt 布局。
* [接口参考](api.zh.md)——所有字段、端点与错误码。
* [后端](backends.zh.md)——llama.cpp / vLLM / SGLang 的 `base_url` 对照表。
