# 脚本 {#scripts}

所有脚本都在 `scripts/` 下，都支持 `--help`，都通过 HTTP 与网关通信——没有一个会 import
应用本身。这让它们既可以指向容器，也可以指向远端机器或同事的环境。

```bash
python scripts/<name>.py --help
```

## 列表 {#the-list}

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

## 冒烟测试 {#smoke-test}

启动网关后第一个该跑的脚本。

```bash
python scripts/smoke_test.py --url http://127.0.0.1:8000
python scripts/smoke_test.py --url http://127.0.0.1:8000 --image screenshot.png
python scripts/smoke_test.py --url http://127.0.0.1:8000 --json
```

它会访问 `/healthz`、`/readyz` 和 `/v1/systemone`，校验每个答案的结构，并打印 usage 与
diagnostics。`--json` 输出机器可读结果，便于放进 CI。

## 对比两个后端 {#comparing-two-backends}

仓库里最有用的脚本。它用同一组用例跑多个服务，只报告真正要紧的指标：

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

* **argmax 一致性**：排序是否保住；
* **mean|dp|**：概率向量整体移动了多远；
* **Brier 距离**：两个概率向量的均方差距。要盯的是这个数——argmax 一致但 Brier 距离大，
  意味着换量化或换后端已经悄悄改坏了阈值。

`--backend` 格式为 `NAME=URL[+key:value]`，可选值有 `model`、`api_key`、
`top_logprobs`、`supports_images`。任何一次观测里出现 `truncated: true` 都会作为警告报出，
因为被截断的概率是下界而不是估计值。

## 正确率：网关 vs 直连工具调用 {#accuracy-gateway-vs-direct-tool-calling}

```bash
python scripts/bench_triage.py --url http://127.0.0.1:8000 --repeat 3
```

六个带标准答案的 SRE 分诊案例，两条路径都跑一遍，并排报告正确率、延迟与 token 数。在
参考基准上（Qwen3.5-0.8B + llama.cpp，单线程，两条路径都关思考）两条路径都是 3/4；网关
输出 3 个 token，直连约 55 个。

## 速度与 token {#speed-and-tokens}

```bash
python scripts/bench_speed_tokens.py --url http://127.0.0.1:8000 --repeat 3
```

先预热两条路径的缓存，然后报告网关与直连路径的 p50 / p95 / mean / min / max 延迟，以及
速度比和输出 token 比。

## 诊断评分标准 {#diagnosing-the-rubric}

正确率不理想时，问题通常在*措辞*，不在网关。

```bash
python scripts/diagnose_severity.py --url http://127.0.0.1:8000
```

它在网关和直连两条路径上，对比模糊标准（`Total outage / Degraded / Minor`）与精确标准
（可从 state 验证的阈值），并包含两个全绿的平凡案例。在参考基准上，模糊标准把两条路径
都推到接近随机——直连甚至把一个全绿案例判成 `sev1`——而精确标准同时修好了两条。如果这个
脚本显示出明显差距，先改写判据，别动任何网关配置。

## 位置偏见 {#position-bias}

```bash
python scripts/probe_position_bias.py --url http://127.0.0.1:8000
```

用三种不同的候选顺序问同一个问题。如果打乱顺序只改变胜出的*字母*而不改变胜出的*标签*，
说明模型在判断语义，分布可以信任；如果*标签*翻了，说明模型读的是位置而不是含义。

## 边界探针 {#boundary-probes}

```bash
python scripts/probe_live.py --url http://127.0.0.1:8000
```

对着活动网关检查文档中承诺的限额：候选数取 2、16、17，图片限额，未知字段的拒绝，以及
各个错误码。改过 `max_questions` 或 `multimodal` 之后很有用。

## 生成测试图片 {#making-a-test-image}

```bash
python scripts/make_test_png.py
```

只用标准库写出一张极小的合法 PNG，这样测视觉路径就不需要 Pillow，也不需要现成的样例图。
