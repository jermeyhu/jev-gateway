from __future__ import annotations

import base64
import json

from app.decision.prompt import SYSTEM_PROMPT, build_messages, has_images
from app.primitives.base import to_decision_task
from app.schemas.request import ChoiceQuestion, NoulQuestion, ScoreQuestion
from app.utils.imaging import ImageRef

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 16).decode()
IMAGE = ImageRef("data_url", "image/png", PNG, f"data:image/png;base64,{PNG}")


def noul(state: str = "the sky is blue"):
    return to_decision_task("q", state, NoulQuestion(type="noul", instructions="is it blue?"))


def choice(state: str = "red green blue"):
    question = ChoiceQuestion(
        type="choice",
        instructions="pick a colour",
        criteria={"red": "the colour red", "green": "the colour green"},
    )
    return to_decision_task("q", state, question)


def score():
    question = ScoreQuestion(type="score", instructions="rate", criteria=["low", "mid", "high"])
    return to_decision_task("q", "a report", question)


def test_fused_layout_is_system_plus_one_user_message() -> None:
    messages = build_messages(choice(), "fused")
    assert [message["role"] for message in messages] == ["system", "user"]
    assert messages[0]["content"] == SYSTEM_PROMPT
    payload = json.loads(messages[1]["content"])
    assert payload["evidence"] == "red green blue"
    assert payload["criterion"] == "pick a colour"
    assert payload["options"] == [
        {"letter": "A", "description": "the colour red"},
        {"letter": "B", "description": "the colour green"},
    ]


def test_fused_layout_is_compact_json() -> None:
    content = build_messages(choice(), "fused")[1]["content"]
    assert ", " not in content
    assert '": ' not in content


def test_split_layout_separates_evidence_from_criterion() -> None:
    messages = build_messages(choice(), "split")
    assert [message["role"] for message in messages] == ["system", "user", "user"]
    assert json.loads(messages[1]["content"]) == {"evidence": "red green blue"}
    second = json.loads(messages[2]["content"])
    assert second["criterion"] == "pick a colour"
    assert [option["letter"] for option in second["options"]] == ["A", "B"]


def test_split_prefix_is_identical_across_questions() -> None:
    other = to_decision_task(
        "q2",
        "red green blue",
        ChoiceQuestion(
            type="choice", instructions="another", criteria={"a": "x", "b": "y"}
        ),
    )
    first_messages = build_messages(choice(), "split")
    second_messages = build_messages(other, "split")
    assert first_messages[:2] == second_messages[:2]


def test_images_come_first_inside_the_content_parts() -> None:
    task = to_decision_task("q", "a photo", NoulQuestion(type="noul", instructions="ok?"), (IMAGE,))
    for layout in ("fused", "split"):
        messages = build_messages(task, layout)
        parts = messages[1]["content"]
        assert isinstance(parts, list)
        assert parts[0]["type"] == "image_url"
        assert parts[-1]["type"] == "text"
        assert has_images(messages)


def test_no_images_keeps_plain_string_content() -> None:
    assert isinstance(build_messages(score(), "fused")[1]["content"], str)
    assert not has_images(build_messages(score(), "fused"))


def test_structured_state_is_embedded_as_json() -> None:
    task = to_decision_task(
        "q", {"rows": [1, 2], "ok": True}, NoulQuestion(type="noul", instructions="ok?")
    )
    payload = json.loads(build_messages(task, "fused")[1]["content"])
    assert payload["evidence"] == {"rows": [1, 2], "ok": True}


def test_score_options_are_ordered_levels() -> None:
    payload = json.loads(build_messages(score(), "fused")[1]["content"])
    assert [option["description"] for option in payload["options"]] == ["low", "mid", "high"]
    assert [option["letter"] for option in payload["options"]] == ["A", "B", "C"]


def test_noul_criteria_become_the_option_descriptions() -> None:
    task = to_decision_task(
        "q",
        "text",
        NoulQuestion(
            type="noul", instructions="is it ok?", criteria={"true": "allow", "false": "deny"}
        ),
    )
    payload = json.loads(build_messages(task, "fused")[1]["content"])
    assert [option["description"] for option in payload["options"]] == ["allow", "deny"]
