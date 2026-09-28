"""Run the hand-written cases through the gateway and through real tool-calling.

Every case runs on its own and strictly one after another: the gateway endpoint
is called ``--repeats`` times, then the equivalent tool-calling request is sent
``--repeats`` times, and only then does the next case start.  Nothing is
concurrent on purpose -- a small model on one consumer GPU cannot absorb two
requests at a time, and letting it try would measure queueing delay rather than
the two approaches.

Both paths are reduced to the same comparable string, so a single expected
value can be checked against either:

* ``noul``   -> ``"true"`` / ``"false"``
* ``choice`` -> the criteria key
* ``score``  -> the tier index, taken as the most likely tier for the gateway
  (its ``score`` is an expectation, not a decision) and read off the enum label
  for the tool call

Usage::

    python scripts/run_test_cases.py
    python scripts/run_test_cases.py --repeats 5 --warmup 1
    python scripts/run_test_cases.py --cases 1,4,7 --mode gateway
    python scripts/run_test_cases.py --out bench_cases.json
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import statistics
import sys
import time
from collections.abc import Callable
from typing import Any

import httpx

CASES_PATH = pathlib.Path(__file__).with_name("jev_test_cases.json")
DEFAULT_GATEWAY_URL = "http://127.0.0.1:8000"
DEFAULT_BACKEND_URL = "http://127.0.0.1:8080"
RULE = "=" * 78
SHORT_NAMES = {"gateway": "网关", "tool": "直连"}


# --------------------------------------------------------------------------- #
# Reading the cases
# --------------------------------------------------------------------------- #
def load_cases(path: pathlib.Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = payload.get("cases") or []
    if not cases:
        raise SystemExit(f"{path} contains no cases")
    return cases


def only_question(case: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    questions = case["网关请求参数"]["questions"]
    if len(questions) != 1:
        raise SystemExit(f"expected exactly one question per case, got {len(questions)}")
    return next(iter(questions.items()))


def expected_of(case: dict[str, Any]) -> str:
    """The answer column reads ``<value> —— <explanation>``; only the head compares."""
    head = case["问题的答案"].split("——")[0].strip()
    if not head:
        raise SystemExit(f"cannot read the expected answer from {case['问题的答案']!r}")
    return head


def title_of(case: dict[str, Any]) -> str:
    """A short label for the case, taken from the first clause of the task."""
    text = case["真实任务描述"].split("。")[0]
    return text if len(text) <= 30 else text[:30] + "…"


def score_labels(question: dict[str, Any]) -> list[str]:
    """The bare tier names of a score question, e.g. ``["低风险", "中风险", …]``."""
    return [str(item).split("：")[0].strip() for item in question["criteria"]]


def score_index(value: Any, question: dict[str, Any]) -> str | None:
    """Fold both ways of naming a tier onto its position.

    The gateway speaks in ``probabilities`` keys (``"0"``, ``"1"``, …) while a
    real tool call names the tier outright (``"中风险"``).  The answer column
    uses the index, so both are reduced to that.
    """
    text = str(value).strip()
    labels = score_labels(question)
    if text in labels:
        return str(labels.index(text))
    return text if text in {str(index) for index in range(len(labels))} else None


# --------------------------------------------------------------------------- #
# Reducing each path to one comparable value
# --------------------------------------------------------------------------- #
def from_gateway(answer: dict[str, Any], question: dict[str, Any]) -> str | None:
    kind = question["type"]
    if kind == "noul":
        value = answer.get("noul")
        return None if value is None else ("true" if float(value) >= 0.5 else "false")
    if kind == "choice":
        value = answer.get("choice")
        return None if value is None else str(value)
    probabilities = answer.get("probabilities")
    if not isinstance(probabilities, dict) or not probabilities:
        return None
    return max(probabilities, key=lambda key: float(probabilities[key]))


def parse_arguments(raw: Any) -> dict[str, Any] | None:
    """Tool arguments are a JSON string; small models sometimes wrap it in prose."""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return parsed if isinstance(parsed, dict) else None


def from_tool_call(raw: Any, qid: str, question: dict[str, Any]) -> str | None:
    arguments = parse_arguments(raw)
    if arguments is None or qid not in arguments:
        return None
    value = arguments[qid]
    kind = question["type"]
    if kind == "score":
        return score_index(value, question)
    if kind == "noul":
        if isinstance(value, bool):
            return "true" if value else "false"
        text = str(value).strip().lower()
        return text if text in {"true", "false"} else None
    return str(value).strip() or None


# --------------------------------------------------------------------------- #
# One call, either path
# --------------------------------------------------------------------------- #
class Attempt:
    __slots__ = ("value", "latency_ms", "error", "output_tokens")

    def __init__(self, value: str | None, latency_ms: float, error: str, tokens: int) -> None:
        self.value = value
        self.latency_ms = latency_ms
        self.error = error
        self.output_tokens = tokens


def _timed(call: Callable[[], httpx.Response]) -> tuple[httpx.Response | None, float, str]:
    started = time.perf_counter()
    try:
        response = call()
    except httpx.HTTPError as exc:
        return None, (time.perf_counter() - started) * 1000.0, f"transport error: {exc}"
    return response, (time.perf_counter() - started) * 1000.0, ""


def ask_gateway(
    client: httpx.Client, url: str, case: dict[str, Any], qid: str, question: dict[str, Any]
) -> Attempt:
    response, ms, error = _timed(
        lambda: client.post(f"{url}/v1/systemone", json=case["网关请求参数"])
    )
    if response is None:
        return Attempt(None, ms, error, 0)
    if response.status_code != 200:
        return Attempt(None, ms, f"HTTP {response.status_code}: {response.text[:200]}", 0)
    body = response.json()
    answers = body.get("answers") or {}
    if qid not in answers:
        return Attempt(None, ms, f"no answer for {qid!r}", 0)
    value = from_gateway(answers[qid], question)
    if value is None:
        return Attempt(None, ms, "unreadable answer", 0)
    usage = body.get("usage") or {}
    return Attempt(value, ms, "", int(usage.get("output_tokens") or 0))


def ask_tool(
    client: httpx.Client,
    url: str,
    headers: dict[str, str],
    extra_body: dict[str, Any],
    model: str,
    case: dict[str, Any],
    qid: str,
    question: dict[str, Any],
) -> Attempt:
    payload = dict(case["大模型toolcall参数"])
    payload.update(extra_body)
    if model:
        payload["model"] = model
    response, ms, error = _timed(
        lambda: client.post(
            f"{url}/v1/chat/completions", json=payload, headers=headers
        )
    )
    if response is None:
        return Attempt(None, ms, error, 0)
    if response.status_code != 200:
        return Attempt(None, ms, f"HTTP {response.status_code}: {response.text[:200]}", 0)
    body = response.json()
    choices = body.get("choices") or []
    if not choices:
        return Attempt(None, ms, "response carried no choices", 0)
    message = choices[0].get("message") or {}
    calls = message.get("tool_calls") or []
    if calls:
        raw = (calls[0].get("function") or {}).get("arguments")
        if choices[0].get("finish_reason") not in (None, "tool_calls", "stop"):
            return Attempt(None, ms, f"finish_reason={choices[0].get('finish_reason')}", 0)
    else:
        raw = message.get("content")  # answered in prose instead of calling the tool
    value = from_tool_call(raw, qid, question)
    if value is None:
        return Attempt(None, ms, "no usable tool call in the reply", 0)
    usage = body.get("usage") or {}
    return Attempt(value, ms, "", int(usage.get("completion_tokens") or 0))


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def describe(attempts: list[Attempt], expected: str) -> str:
    hits = sum(1 for item in attempts if item.value == expected)
    latencies = [item.latency_ms for item in attempts]
    mark = "OK " if hits == len(attempts) else ("-- " if hits == 0 else "~  ")
    values = ", ".join(item.value if item.value else f"error({item.error[:28]})"
                       for item in attempts)
    return (
        f"    {mark}{hits}/{len(attempts)}  {values}"
        f"  |  mean {statistics.mean(latencies):7.0f} ms"
        f"  runs {', '.join(f'{value:.0f}' for value in latencies)}"
    )


def summarise(name: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    latencies = [item["latency_ms"] for item in records if item["value"] is not None]
    hits = sum(1 for item in records if item["correct"])
    tokens = [item["output_tokens"] for item in records]
    return {
        "path": name,
        "total": len(records),
        "correct": hits,
        "accuracy": hits / len(records) if records else 0.0,
        "latency_mean_ms": statistics.mean(latencies) if latencies else 0.0,
        "latency_median_ms": statistics.median(latencies) if latencies else 0.0,
        "latency_min_ms": min(latencies) if latencies else 0.0,
        "latency_max_ms": max(latencies) if latencies else 0.0,
        "output_tokens_mean": statistics.mean(tokens) if tokens else 0.0,
    }


def report(stats: dict[str, Any], by_primitive: dict[str, dict[str, Any]]) -> None:
    print(RULE)
    print("汇总")
    print(RULE)
    print(f"  {'路径':<6}{'准确率':>22}{'延迟 mean':>14}{'延迟 median':>14}{'输出 tok':>12}")
    for key in ("gateway", "tool"):
        item = stats.get(key)
        if item is None:
            continue
        accuracy = f"{item['correct']}/{item['total']} ({item['accuracy'] * 100:.1f}%)"
        print(
            f"  {SHORT_NAMES[key]:<6}{accuracy:>22}"
            f"{item['latency_mean_ms']:11.0f} ms"
            f"{item['latency_median_ms']:11.0f} ms"
            f"{item['output_tokens_mean']:12.1f}"
        )
    gateway, tool = stats.get("gateway"), stats.get("tool")
    if gateway and tool and gateway["latency_mean_ms"] > 0:
        speed = tool["latency_mean_ms"] / gateway["latency_mean_ms"]
        print(f"\n  延迟比 直连 / 网关 = {speed:.2f}x")
    print("\n  按 primitive 拆分（正确数 / 总数）")
    for kind in ("noul", "choice", "score"):
        row = by_primitive.get(kind)
        if not row:
            continue
        cells = []
        for key in ("gateway", "tool"):
            item = row.get(key)
            cells.append(
                f"{SHORT_NAMES[key]} {item['correct']}/{item['total']}".ljust(14)
                if item
                else f"{SHORT_NAMES[key]} -".ljust(14)
            )
        print(f"    {kind:<8}" + "".join(cells))
    print(RULE)


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cases-file", type=pathlib.Path, default=CASES_PATH)
    parser.add_argument("--gateway-url", default=DEFAULT_GATEWAY_URL)
    parser.add_argument(
        "--backend-url",
        default=os.environ.get("JEV_BACKEND_BASE_URL", DEFAULT_BACKEND_URL),
        help="the OpenAI-compatible server the tool-calling requests go to",
    )
    parser.add_argument("--api-key", default=os.environ.get("JEV_BACKEND_API_KEY", ""))
    parser.add_argument(
        "--model",
        default="",
        help="override the model id baked into each case's tool-calling payload, "
        "which otherwise keeps naming the local llama.cpp build",
    )
    parser.add_argument(
        "--extra-body",
        default=os.environ.get("JEV_BACKEND_EXTRA_BODY", ""),
        help="JSON object merged into every tool-calling payload, so both paths "
        "run against the same backend settings (e.g. thinking off)",
    )
    parser.add_argument(
        "--thinking",
        choices=("off", "on"),
        default="off",
        help="'off' sends chat_template_kwargs.enable_thinking=false, which a "
        "reasoning model such as Qwen3.5 needs: with thinking left on it burns "
        "the whole budget on reasoning tokens and never reaches the tool call",
    )
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--warmup", type=int, default=0, help="discarded calls per path")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--cases", default="", help="1-based indices, e.g. 1,4,7")
    parser.add_argument("--mode", choices=("both", "gateway", "tool"), default="both")
    parser.add_argument("--out", type=pathlib.Path, help="write the raw results as JSON")
    args = parser.parse_args(argv)

    try:
        cases = load_cases(args.cases_file)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"cannot read {args.cases_file}: {exc}", file=sys.stderr)
        return 2
    if args.cases:
        wanted = {int(part) for part in args.cases.split(",") if part.strip()}
        cases = [case for index, case in enumerate(cases, 1) if index in wanted]
        if not cases:
            print("no case matched --cases", file=sys.stderr)
            return 2
    if args.repeats < 1:
        print("--repeats must be at least 1", file=sys.stderr)
        return 2
    try:
        extra_body = json.loads(args.extra_body) if args.extra_body.strip() else {}
    except json.JSONDecodeError as exc:
        print(f"--extra-body is not valid JSON: {exc}", file=sys.stderr)
        return 2
    # A reasoning model spends the whole budget thinking out loud and never emits
    # the tool call, so the two paths are only comparable with thinking off.
    # There is no portable switch, so send every spelling we know of: each server
    # ignores the keys it does not understand.  "effort: none" matters because
    # without it the single permitted token is consumed by reasoning and the
    # response comes back with null logprobs.
    if args.thinking == "off":
        extra_body = dict(extra_body)
        extra_body.setdefault("chat_template_kwargs", {}).setdefault("enable_thinking", False)
        extra_body.setdefault("reasoning", {}).setdefault("effort", "none")
    elif "chat_template_kwargs" not in extra_body:
        extra_body = dict(extra_body)
        extra_body["chat_template_kwargs"] = {"enable_thinking": True}
    headers = {"Authorization": f"Bearer {args.api_key}"} if args.api_key else {}

    run_gateway = args.mode in ("both", "gateway")
    run_tool = args.mode in ("both", "tool")
    print(
        f"网关 {args.gateway_url}   直连 {args.backend_url}"
        f"   每题 {args.repeats} 次   单线程   用例 {len(cases)} 条"
        f"   思考 {'关闭' if args.thinking == 'off' else '开启'}"
    )
    print(f"直连模型 {args.model or '(取自用例文件)'}", flush=True)
    print(RULE, flush=True)

    records: list[dict[str, Any]] = []
    with httpx.Client(timeout=args.timeout) as client:
        if args.warmup:
            for case in cases:
                qid, question = only_question(case)
                for _ in range(args.warmup):
                    if run_gateway:
                        ask_gateway(client, args.gateway_url, case, qid, question)
                    if run_tool:
                        ask_tool(
                            client,
                            args.backend_url,
                            headers,
                            extra_body,
                            args.model,
                            case,
                            qid,
                            question,
                        )
            print(f"（已完成 {args.warmup} 轮预热，结果不计入）", flush=True)

        for index, case in enumerate(cases, 1):
            qid, question = only_question(case)
            expected = expected_of(case)
            kind = question["type"]
            print(
                f"[{index}/{len(cases)}] {title_of(case)}  ·  {qid} ({kind})"
                f"  期望 {expected}",
                flush=True,
            )
            for path, label, enabled in (
                ("gateway", "网关", run_gateway),
                ("tool", "直连", run_tool),
            ):
                if not enabled:
                    continue
                if path == "gateway":
                    attempts = [
                        ask_gateway(client, args.gateway_url, case, qid, question)
                        for _ in range(args.repeats)
                    ]
                else:
                    attempts = [
                        ask_tool(
                            client,
                            args.backend_url,
                            headers,
                            extra_body,
                            args.model,
                            case,
                            qid,
                            question,
                        )
                        for _ in range(args.repeats)
                    ]
                print(f"  {label}", flush=True)
                print(describe(attempts, expected), flush=True)
                for attempt in attempts:
                    records.append(
                        {
                            "case": index,
                            "question_id": qid,
                            "primitive": kind,
                            "path": path,
                            "expected": expected,
                            "value": attempt.value,
                            "correct": attempt.value == expected,
                            "latency_ms": attempt.latency_ms,
                            "output_tokens": attempt.output_tokens,
                            "error": attempt.error,
                        }
                    )
            print(flush=True)

    stats: dict[str, Any] = {}
    by_primitive: dict[str, dict[str, Any]] = {}
    for path, label in (("gateway", "网关"), ("tool", "直连 tool-call")):
        subset = [item for item in records if item["path"] == path]
        if not subset:
            continue
        stats[path] = summarise(label, subset)
        for kind in {item["primitive"] for item in subset}:
            rows = [item for item in subset if item["primitive"] == kind]
            by_primitive.setdefault(kind, {})[path] = summarise(label, rows)
    if stats:
        report(stats, by_primitive)
    if args.out:
        args.out.write_text(
            json.dumps(
                {
                    "settings": {
                        "gateway_url": args.gateway_url,
                        "backend_url": args.backend_url,
                        "repeats": args.repeats,
                        "warmup": args.warmup,
                    },
                    "summary": stats,
                    "by_primitive": by_primitive,
                    "records": records,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"原始结果已写入 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
