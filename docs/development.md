# Development

## Setup

```bash
pip install -e ".[dev]"
pytest -q
ruff check .
mypy app
```

Python 3.11 or newer. No network or GPU is required for the test suite.

## What the tests cover

| area                     | file                                              |
| ------------------------ | ------------------------------------------------- |
| prompt layouts, image ordering, compact JSON | `tests/test_prompt.py`  |
| image parsing, sniffing, limits, rejection      | `tests/test_imaging.py` |
| the logprob → answer maths, ties, renormalisation | `tests/test_engine.py` |
| the wire shape, logprob parsing, `extra_body` merging | `tests/test_backends.py` |
| config loading, strict unknown-key rejection     | `tests/test_config.py`  |
| the HTTP surface, error shapes, timeouts         | `tests/test_api.py`     |
| concurrency limits, first-error-wins, budgets   | `tests/test_coordinator.py` |
| a whole in-process request, real app + service + backend against a mock transport | `tests/test_end_to_end.py` |
| candidate building and the 16-candidate ceiling | `tests/test_primitives.py` |

The end-to-end test is the interesting one: the real application, the real service and the
real backend class are wired together, and only the HTTP transport is replaced. A stand-in
server speaks the plain OpenAI chat-completions contract — including shuffling the
`top_logprobs` order, because the API does not guarantee one — so everything from request
validation down to logprob parsing is exercised for real.

## Layout

```
app/
  main.py            FastAPI app, request-id middleware, error handlers, endpoints
  config.py          frozen settings dataclasses, strict YAML loading
  service.py         wiring backend + engine + coordinator, request → response
  schemas/           request and response models
  decision/          prompt building, the engine, the coordinator, the task IR
  primitives/        question types → lettered candidates
  backends/          the one OpenAI-compatible backend
  utils/             errors, image parsing, softmax
scripts/             HTTP-only tools; none of them import the app
tests/               pytest suite
```

The layering is deliberate: `scripts/` never imports `app/`, so every tool can be pointed
at a remote gateway. `backends/` knows nothing about questions, and `primitives/` knows
nothing about HTTP.

## Working on the gateway

```bash
JEV_GATEWAY_CONFIG=config.local.yaml python -m app
# or the console script, equivalent
jev-gateway
```

VS Code tasks are available for `Test`, `Lint`, `Type check`, `Check all`, `Run gateway`,
`Smoke test` and `Validate compose YAML`.

## Working on this site

The documentation site is [MkDocs](https://www.mkdocs.org/) with the
[Material](https://squidfunk.github.io/mkdocs-material/) theme, configured in `mkdocs.yml`
with the `i18n` plugin. English pages live in `docs/`, the Simplified Chinese translations
right next to them as `<page>.zh.md` — the plugin is configured with
`docs_structure: suffix`, so `docs/api.md` and `docs/api.zh.md` are the two languages of
one page.

```bash
pip install mkdocs-material mkdocs-material-extensions
mkdocs serve          # live reload on http://127.0.0.1:8001 — the site, not the gateway
mkdocs build --strict # writes site/, same command CI runs
```

`mkdocs.yml` declares the language switch, the navigation translations and the search
languages. Adding a page means adding it to `nav:` in **both** languages, and creating the
`.zh.md` file next to the English one.

## Contributing

1. Open an issue describing the behaviour you want to change.
2. Keep the wire contract stable — clients written against the hosted service must keep
   working.
3. Add or update the test that pins the behaviour you are changing.
4. Run `Check all` (lint, types, tests) before opening a pull request.
5. If the change is user-visible, update both `README.md` and `README.en.md`, and the
   matching pages under `docs/`.

Unknown config keys stay a hard error, and `extra_body` stays unable to override the
contract fields. Both are load-bearing: they are what makes a typo fail loudly instead of
silently.
