"""Prompt construction.

Two layouts are supported:

``fused`` (default, byte compatible with the reference gateway)
    one user message holding ``{evidence, criterion, options}``.

``split``
    a user message with the images and the evidence, followed by a second user
    message with the criterion and the options.  The first message is identical
    for every question of a request, so backends with a prompt cache
    (llama.cpp) reuse the vision encoder work and the state prefill.

In both layouts images come first so that the shared prefix stays byte identical
across the questions of one request.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from app.decision.ir import DecisionTask
from app.utils.imaging import to_openai_parts

SYSTEM_PROMPT = (
    "Evaluate the supplied evidence according to the instruction.\n"
    "Choose exactly one of the listed options that best matches the evidence.\n"
    "Respond with only its uppercase letter, with no explanation or reasoning."
)

Message = dict[str, Any]


def _dump(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _options(task: DecisionTask) -> list[dict[str, str]]:
    return [
        {"letter": candidate.letter, "description": candidate.description}
        for candidate in task.candidates
    ]


def _evidence_message(text: str, task: DecisionTask) -> Message:
    if not task.images:
        return {"role": "user", "content": text}
    return {
        "role": "user",
        "content": [*to_openai_parts(task.images), {"type": "text", "text": text}],
    }


def build_messages(
    task: DecisionTask, layout: Literal["fused", "split"] = "fused"
) -> list[Message]:
    if layout == "split":
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            _evidence_message(_dump({"evidence": task.state}), task),
            {
                "role": "user",
                "content": _dump({"criterion": task.instruction, "options": _options(task)}),
            },
        ]
    payload = {
        "evidence": task.state,
        "criterion": task.instruction,
        "options": _options(task),
    }
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        _evidence_message(_dump(payload), task),
    ]


def has_images(messages: list[Message]) -> bool:
    for message in messages:
        content = message.get("content")
        if isinstance(content, list) and any(
            isinstance(part, dict) and part.get("type") == "image_url" for part in content
        ):
            return True
    return False
