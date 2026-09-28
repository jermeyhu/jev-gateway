from __future__ import annotations

import pytest

from app.primitives.base import build_candidates, to_decision_task
from app.schemas.request import ChoiceQuestion, NoulQuestion, ScoreQuestion
from app.utils.errors import InvalidRequest


def test_noul_without_criteria_uses_yes_no() -> None:
    candidates = build_candidates(NoulQuestion(type="noul", instructions="ok?"))
    assert [(c.letter, c.key, c.description) for c in candidates] == [
        ("A", "true", "Yes"),
        ("B", "false", "No"),
    ]


def test_noul_with_criteria_keeps_client_labels() -> None:
    question = NoulQuestion(
        type="noul",
        instructions="is it safe?",
        criteria={"true": "It is safe to run", "false": "It is not safe"},
    )
    candidates = build_candidates(question)
    assert [c.description for c in candidates] == [
        "It is safe to run",
        "It is not safe",
    ]
    assert [c.letter for c in candidates] == ["A", "B"]


def test_choice_preserves_insertion_order() -> None:
    question = ChoiceQuestion(
        type="choice",
        instructions="pick",
        criteria={"zeta": "last", "alpha": "first", "mid": "middle"},
    )
    assert [c.key for c in build_candidates(question)] == ["zeta", "alpha", "mid"]
    assert [c.letter for c in build_candidates(question)] == ["A", "B", "C"]


def test_score_keys_are_indices() -> None:
    question = ScoreQuestion(type="score", instructions="rate", criteria=["bad", "ok", "good"])
    candidates = build_candidates(question)
    assert [c.key for c in candidates] == ["0", "1", "2"]
    assert [c.letter for c in candidates] == ["A", "B", "C"]


def test_letters_go_past_z_in_order() -> None:
    question = ChoiceQuestion(
        type="choice",
        instructions="many",
        criteria={chr(ord("a") + i): f"option {i}" for i in range(16)},
    )
    assert [c.letter for c in build_candidates(question)] == list("ABCDEFGHIJKLMNOP")


def test_to_decision_task_carries_state_and_images() -> None:
    question = NoulQuestion(type="noul", instructions="ok?")
    task = to_decision_task("q1", {"k": "v"}, question, ())
    assert task.question_id == "q1"
    assert task.primitive == "noul"
    assert task.state == {"k": "v"}
    assert task.instruction == "ok?"
    assert task.images == []


@pytest.mark.parametrize(
    "criteria",
    [
        {},
        {"only": "one"},
        {f"k{i}": f"v{i}" for i in range(17)},
    ],
)
def test_choice_candidate_count_is_bounded(criteria: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        ChoiceQuestion(type="choice", instructions="x", criteria=criteria)


def test_invalid_request_error_carries_400() -> None:
    error = InvalidRequest("nope")
    assert error.status_code == 400
    assert error.to_payload("rid")["error"]["request_id"] == "rid"
