# 开发 {#development}

## 环境搭建 {#setup}

```bash
pip install -e ".[dev]"
pytest -q
ruff check .
mypy app
```

需要 Python 3.11 或更新版本。测试套件不需要网络，也不需要 GPU。

## 测试覆盖了什么 {#what-the-tests-cover}

| 范围                                                   | 文件                              |
| ------------------------------------------------------ | --------------------------------- |
| prompt 布局、图片顺序、紧凑 JSON                      | `tests/test_prompt.py`            |
| 图片解析、类型嗅探、限额、拒绝                        | `tests/test_imaging.py`           |
| logprob → 答案的换算、并列、重新归一化                 | `tests/test_engine.py`            |
| 线上报文结构、logprob 解析、`extra_body` 合并          | `tests/test_backends.py`          |
| 配置加载、未知键严格拒绝                               | `tests/test_config.py`            |
| HTTP 层、错误结构、超时                                | `tests/test_api.py`               |
| 并发上限、首个错误胜出、时间预算                       | `tests/test_coordinator.py`       |
| 一次完整的进程内请求：真实 app + service + backend 对 mock transport | `tests/test_end_to_end.py` |
| 候选构造与 16 候选上限                                 | `tests/test_primitives.py`        |

端到端那个测试最有意思：真实的 app、真实的 service、真实的 backend 类都接在一起，只把
HTTP 传输层换掉。替身服务说的是标准 OpenAI chat-completions 契约——还会打乱
`top_logprobs` 的顺序，因为 API 并不保证顺序——因此从请求校验一直到 logprob 解析的每一层
都是真的被跑过的。

## 目录结构 {#layout}

```
app/
  main.py            FastAPI app、request-id 中间件、错误处理器、端点
  config.py          冻结的 settings dataclass、严格的 YAML 加载
  service.py         把 backend + engine + coordinator 接线，请求 → 响应
  schemas/           请求与响应模型
  decision/          prompt 构建、引擎、协调器、任务 IR
  primitives/        问题类型 → 带字母的候选
  backends/          唯一的 OpenAI 兼容后端
  utils/             错误、图片解析、softmax
scripts/             纯 HTTP 工具；没有任何一个 import app
tests/               pytest 套件
```

分层是刻意的：`scripts/` 从不 import `app/`，所以每个工具都能指向远端网关；`backends/`
不知道问题的存在，`primitives/` 不知道 HTTP 的存在。

## 开发网关 {#working-on-the-gateway}

```bash
JEV_GATEWAY_CONFIG=config.local.yaml python -m app
# 或者等价的控制台脚本
jev-gateway
```

VS Code 里已有 `Test`、`Lint`、`Type check`、`Check all`、`Run gateway`、`Smoke test`
和 `Validate compose YAML` 等任务。

## 开发本站文档 {#working-on-this-site}

文档站是 [MkDocs](https://www.mkdocs.org/) 搭配
[Material](https://squidfunk.github.io/mkdocs-material/) 主题，配置在 `mkdocs.yml`，
多语言用 `i18n` 插件。英文页面在 `docs/`，简体中文翻译紧邻其后，文件名为
`<页面名>.zh.md`——插件配置为 `docs_structure: suffix`，所以 `docs/api.md` 和
`docs/api.zh.md` 是同一个页面的两种语言。

```bash
pip install mkdocs-material mkdocs-material-extensions
mkdocs serve          # 热重载，http://127.0.0.1:8001 —— 这是站点，不是网关
mkdocs build --strict # 输出 site/，与 CI 跑的是同一条命令
```

`mkdocs.yml` 里声明了语言切换、导航翻译和搜索语言。新增页面意味着要在**两种语言**的
`nav:` 里都加一条，并在英文页面旁边建一个 `.zh.md`。

## 参与贡献 {#contributing}

1. 先开 issue 描述你想改的行为。
2. 保持线上契约稳定——按托管服务写的客户端必须继续可用。
3. 为你要改的行为补上或更新对应的测试。
4. 提 PR 前跑一遍 `Check all`（lint、类型、测试）。
5. 如果改动对用户可见，同时更新 `README.md`、`README.zh-CN.md` 以及 `docs/` 下对应的页面。

未知配置键仍然是硬错误，`extra_body` 仍然无法覆盖契约字段。两者都是承重的：正是它们让
拼写错误大声失败，而不是悄悄生效。
