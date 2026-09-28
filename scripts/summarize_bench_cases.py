"""Recompute the summary tables straight from the raw benchmark JSON.

Long runs lose their stdout to buffering, so the JSON is the only trustworthy
source.  Prints one table per backend so the docs can be checked against real
numbers rather than a transcript that scrolled away.
"""

from __future__ import annotations

import json
import pathlib
import statistics
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
FILES = {
    "0.8B (本地 llama.cpp)": ROOT / "bench_test_cases.json",
    "27B (openrouter)": ROOT / "bench_test_cases_27b.json",
}
KINDS = ("noul", "choice", "score")


def summarise(records: list[dict]) -> dict[str, tuple[int, int, float, float, float]]:
    out: dict[str, tuple[int, int, float, float, float]] = {}
    for path in ("gateway", "tool"):
        rows = [item for item in records if item["path"] == path]
        if not rows:
            continue
        latencies = [item["latency_ms"] for item in rows if item["value"] is not None]
        tokens = [item["output_tokens"] for item in rows]
        out[path] = (
            sum(1 for item in rows if item["correct"]),
            len(rows),
            statistics.mean(latencies) if latencies else 0.0,
            statistics.median(latencies) if latencies else 0.0,
            statistics.mean(tokens) if tokens else 0.0,
        )
    return out


def main() -> None:
    for label, path in FILES.items():
        if not path.exists():
            print(f"缺少 {path.name}，跳过")
            continue
        records = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(records, dict):
            records = records.get("records") or records.get("results") or []
        print(f"\n===== {label}  ({path.name}, {len(records)} 次调用) =====")
        summary = summarise(records)
        for path_name, name in (("gateway", "网关"), ("tool", "直连")):
            if path_name not in summary:
                continue
            hits, total, mean, median, tokens = summary[path_name]
            print(
                f"{name}  {hits}/{total} ({hits / total:.1%})  "
                f"mean {mean:.0f} ms  median {median:.0f} ms  tok {tokens:.1f}"
            )
        if "gateway" in summary and "tool" in summary:
            gw, tool = summary["gateway"], summary["tool"]
            print(f"延迟比 直连/网关 = {tool[2] / gw[2]:.2f}x")
            print(f"输出 token 比     = {tool[4] / gw[4]:.1f}x" if gw[4] else "")
        for kind in KINDS:
            cells = []
            for path_name, name in (("gateway", "网关"), ("tool", "直连")):
                rows = [
                    item
                    for item in records
                    if item["path"] == path_name and item["primitive"] == kind
                ]
                if rows:
                    cells.append(f"{name} {sum(1 for i in rows if i['correct'])}/{len(rows)}")
            if cells:
                print(f"  {kind:<7} " + "   ".join(cells))
        bad = [item for item in records if item["value"] is None]
        print(f"失败 {len(bad)} 次")
        for item in bad[:5]:
            print(f"  case {item['case']} {item['path']}: {item['error'][:90]}")


if __name__ == "__main__":
    sys.exit(main())
