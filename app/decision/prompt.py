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
import re
from typing import Any, Literal

from app.decision.ir import DecisionTask
from app.utils.imaging import to_openai_parts

SYSTEM_PROMPT = (
    "Evaluate the supplied evidence according to the instruction.\n"
    "Choose exactly one of the listed options that best matches the evidence.\n"
    "Respond with only its uppercase letter, with no explanation or reasoning."
)
BOX_SYSTEM_PROMPT = (
    "Find the target. Return compact JSON only: "
    '{"b":[top,bottom,left,right],"c":confidence}. All values are 0 to 1; '
    "top < bottom and left < right."
)

Message = dict[str, Any]


def _dump(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _options(task: DecisionTask) -> list[dict[str, Any]]:
    return [
        {"letter": candidate.letter, "description": candidate.description}
        for candidate in task.candidates
    ]


def _evidence_message(payload: dict[str, Any], task: DecisionTask) -> Message:
    if not task.images:
        return {"role": "user", "content": _dump(payload)}
    image_by_reference = {image.reference: image for image in task.images}
    references = sorted(image_by_reference, key=len, reverse=True)
    matcher = re.compile("|".join(re.escape(reference) for reference in references))
    if not matcher.search(_dump(payload)):
        return {"role": "user", "content": _dump(payload)}

    parts: list[dict[str, Any]] = []

    def text(value: str) -> None:
        if value:
            if parts and parts[-1].get("type") == "text":
                parts[-1]["text"] += value
            else:
                parts.append({"type": "text", "text": value})

    def render_string(value: str) -> None:
        matches = list(matcher.finditer(value))
        if not matches:
            text(json.dumps(value, ensure_ascii=False))
            return
        if value in image_by_reference:
            parts.extend(to_openai_parts([image_by_reference[value]]))
            return
        cursor = 0
        text('"')
        for match in matches:
            segment = value[cursor : match.start()]
            text(json.dumps(segment, ensure_ascii=False)[1:-1])
            image = image_by_reference[match.group()]
            parts.extend(to_openai_parts([image]))
            cursor = match.end()
        text(json.dumps(value[cursor:], ensure_ascii=False)[1:-1] + '"')

    def render(value: Any) -> None:
        if isinstance(value, str):
            render_string(value)
        elif isinstance(value, dict):
            text("{")
            for index, (key, child) in enumerate(value.items()):
                if index:
                    text(",")
                text(json.dumps(key, ensure_ascii=False) + ":")
                render(child)
            text("}")
        elif isinstance(value, list):
            text("[")
            for index, child in enumerate(value):
                if index:
                    text(",")
                render(child)
            text("]")
        else:
            text(json.dumps(value, ensure_ascii=False, allow_nan=False))

    render(payload)
    return {"role": "user", "content": parts}


def build_messages(
    task: DecisionTask, layout: Literal["fused", "split"] = "fused"
) -> list[Message]:
    if task.primitive == "box":
        payload = {"evidence": task.state, "criterion": task.instruction}
        return [
            {"role": "system", "content": BOX_SYSTEM_PROMPT},
            _evidence_message(payload, task),
        ]
    if layout == "split":
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            _evidence_message({"evidence": task.state}, task),
            _evidence_message(
                {"criterion": task.instruction, "options": _options(task)}, task
            ),
        ]
    payload = {
        "evidence": task.state,
        "criterion": task.instruction,
        "options": _options(task),
    }
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        _evidence_message(payload, task),
    ]


def has_images(messages: list[Message]) -> bool:
    for message in messages:
        content = message.get("content")
        if isinstance(content, list) and any(
            isinstance(part, dict) and part.get("type") == "image_url" for part in content
        ):
            return True
    return False
