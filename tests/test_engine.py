from __future__ import annotations

import math

import pytest

from app.backends.base import CandidateScores
from app.decision.engine import DecisionEngine, build_answer
from app.decision.ir import Candidate, DecisionTask
from app.primitives.base import to_decision_task
from app.schemas.request import ChoiceQuestion, NoulQuestion, ScoreQuestion

from .conftest import IMAGE, FakeBackend


def scores(values: dict[str, float]) -> CandidateScores:
    return CandidateScores(
        logprobs=values, truncated=False, input_tokens=7, output_tokens=1
    )


def noul_task() -> DecisionTask:
    return to_decision_task("q", "state", NoulQuestion(type="noul", instructions="ok?"))


def choice_task() -> DecisionTask:
    return to_decision_task(
        "q",
        "state",
        ChoiceQuestion(
            type="choice",
            instructions="pick",
            criteria={"a": "first", "b": "second", "c": "third"},
        ),
    )


def score_task() -> DecisionTask:
    return to_decision_task(
        "q",
        "state",
        ScoreQuestion(type="score", instructions="rate", criteria=["low", "mid", "high"]),
    )


def test_noul_answer_is_the_true_probability() -> None:
    answer = build_answer(noul_task(), scores({"A": math.log(0.7), "B": math.log(0.3)}))
    assert answer == {"type": "noul", "noul": pytest.approx(0.7)}
    assert set(answer) == {"type", "noul"}


def test_probabilities_are_renormalised_over_candidates_only() -> None:
    # A, B, C are candidates; the rest of the vocabulary mass is discarded.
    answer = build_answer(
        choice_task(),
        CandidateScores(
            logprobs={"A": -0.1, "B": -0.2, "C": -5.0},
            truncated=False,
            input_tokens=1,
            output_tokens=1,
        ),
    )
    probabilities = answer["probabilities"]
    assert sum(probabilities.values()) == pytest.approx(1.0)
    assert probabilities["a"] > probabilities["b"] > probabilities["c"]
    assert probabilities["c"] < 0.01


def test_choice_answer_picks_the_argmax_key() -> None:
    answer = build_answer(choice_task(), scores({"A": -3.0, "B": -0.1, "C": -2.0}))
    assert answer["type"] == "choice"
    assert answer["choice"] == "b"
    assert list(answer["probabilities"]) == ["a", "b", "c"]


def test_score_answer_is_an_expected_value_with_legend() -> None:
    answer = build_answer(
        score_task(), scores({"A": math.log(0.5), "B": math.log(0.25), "C": math.log(0.25)})
    )
    assert answer["type"] == "score"
    # 0*0.5 + 1*0.25 + 2*0.25
    assert answer["score"] == pytest.approx(0.75)
    assert answer["legend"] == {"0": "low", "1": "mid", "2": "high"}
    assert set(answer["probabilities"]) == {"0", "1", "2"}


def test_ties_resolve_to_the_first_candidate() -> None:
    answer = build_answer(choice_task(), scores({"A": -1.0, "B": -1.0, "C": -9.0}))
    assert answer["choice"] == "a"
    probabilities = answer["probabilities"]
    assert probabilities["a"] == pytest.approx(probabilities["b"])
    assert probabilities["a"] > 0.49
    assert probabilities["c"] < 0.001


def test_unknown_primitive_is_rejected() -> None:
    task = noul_task()
    object.__setattr__(task, "primitive", "mystery")
    with pytest.raises(ValueError, match="unsupported primitive"):
        build_answer(task, scores({"A": 0.0, "B": -1.0}))


async def test_engine_records_latency_and_usage() -> None:
    backend = FakeBackend(default={"A": -0.1, "B": -2.0})
    engine = DecisionEngine(backend, prompt_layout="split")
    result = await engine.decide(noul_task())
    assert result.question_id == "q"
    assert result.input_tokens == 11
    assert result.latency_ms >= 0.0
    messages, letters, _ = backend.calls[0]
    assert [m["role"] for m in messages] == ["system", "user", "user"]
    assert letters == ("A", "B")


async def test_engine_passes_images_through() -> None:
    backend = FakeBackend(default={"A": -0.1, "B": -2.0})
    engine = DecisionEngine(backend)
    task = to_decision_task(
        "q", "photo", NoulQuestion(type="noul", instructions="ok?"), (IMAGE,)
    )
    await engine.decide(task)
    _, _, images = backend.calls[0]
    assert images == (IMAGE,)


def test_candidate_dataclass_is_frozen() -> None:
    candidate = Candidate(key="a", description="first", letter="A")
    with pytest.raises(AttributeError):
        candidate.letter = "B"  # type: ignore[misc]
