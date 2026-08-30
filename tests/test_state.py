"""Unit tests for the deterministic call state (FR-1..5, FR-12..14)."""
from __future__ import annotations

import pytest

from supplier_caller.models import (
    CallRequest,
    CallState,
    Outcome,
    Patient,
    Question,
    Supplier,
)


@pytest.fixture()
def request_() -> CallRequest:
    return CallRequest(
        patient=Patient(
            name="Eleanor Martinez", age=72, coverage="Original Medicare Part B",
            item="standard manual wheelchair", hcpcs="K0001", city="Chicago", zip_code="60640",
        ),
        supplier=Supplier(name="Lakeview Medical Supply", phone="+13125550100"),
    )


@pytest.fixture()
def state(request_: CallRequest) -> CallState:
    return CallState(request=request_)


def test_initial_state_has_all_four_questions_remaining(state: CallState) -> None:
    assert state.remaining() == [
        Question.DELIVERS_TO_ZIP,
        Question.ACCEPTS_MEDICARE,
        Question.ITEM_IN_STOCK,
        Question.DELIVERY_ETA,
    ]
    assert state.outcome() is Outcome.IN_PROGRESS
    assert state.succeeded() is False


def test_record_removes_question_and_tells_llm_next_question(state: CallState) -> None:
    receipt = state.record(Question.ACCEPTS_MEDICARE, True, quote="yeah we take Medicare", confidence="high")
    assert Question.ACCEPTS_MEDICARE not in state.remaining()
    assert "delivers_to_zip" in receipt  # next unanswered, in priority order
    assert "accepts_medicare" not in receipt.split("Still need")[-1]


def test_zip_no_disqualifies_and_empties_remaining(state: CallState) -> None:
    receipt = state.record(Question.DELIVERS_TO_ZIP, False, quote="we don't go that far north", confidence="high")
    assert state.remaining() == []
    assert state.disqualified_by() is Question.DELIVERS_TO_ZIP
    assert state.outcome() is Outcome.DISQUALIFIED
    assert state.succeeded() is True  # a definitive 'no' is a successful call
    assert "end_call" in receipt


def test_medicare_no_disqualifies(state: CallState) -> None:
    state.record(Question.DELIVERS_TO_ZIP, True, quote="sure", confidence="high")
    state.record(Question.ACCEPTS_MEDICARE, False, quote="not taking new Medicare", confidence="high")
    assert state.remaining() == []
    assert state.outcome() is Outcome.DISQUALIFIED


def test_two_answers_at_once_then_next_is_eta(state: CallState) -> None:
    state.record(Question.DELIVERS_TO_ZIP, True, quote="yep", confidence="high")
    state.record(Question.ACCEPTS_MEDICARE, True, quote="we take Medicare", confidence="high")
    receipt = state.record(Question.ITEM_IN_STOCK, True, quote="got manual chairs in stock", confidence="high")
    assert state.remaining() == [Question.DELIVERY_ETA]
    assert "delivery_eta" in receipt


def test_all_answered_is_complete_and_receipt_says_wrap_up(state: CallState) -> None:
    state.record(Question.DELIVERS_TO_ZIP, True, quote="yep", confidence="high")
    state.record(Question.ACCEPTS_MEDICARE, True, quote="yes", confidence="high")
    state.record(Question.ITEM_IN_STOCK, True, quote="in stock", confidence="high")
    receipt = state.record(Question.DELIVERY_ETA, "Thursday, 2 business days", quote="probably Thursday", confidence="medium")
    assert state.is_complete()
    assert state.outcome() is Outcome.COMPLETE
    assert "end_call" in receipt


def test_bool_question_rejects_string_value(state: CallState) -> None:
    with pytest.raises(ValueError):
        state.record(Question.ACCEPTS_MEDICARE, "yes", quote="yes", confidence="high")  # type: ignore[arg-type]


def test_eta_rejects_bool_and_empty(state: CallState) -> None:
    with pytest.raises(ValueError):
        state.record(Question.DELIVERY_ETA, True, quote="x", confidence="high")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        state.record(Question.DELIVERY_ETA, "  ", quote="x", confidence="high")


def test_ended_with_some_answers_is_partial_and_lists_unanswered(state: CallState) -> None:
    state.record(Question.ACCEPTS_MEDICARE, True, quote="yes", confidence="high")
    state.end("callee hung up")
    assert state.outcome() is Outcome.PARTIAL
    assert state.succeeded() is False
    result = state.to_result()
    assert result["outcome"] == "partial"
    assert set(result["unanswered"]) == {"delivers_to_zip", "item_in_stock", "delivery_eta"}
    assert result["answers"]["accepts_medicare"]["value"] is True
    assert result["answers"]["accepts_medicare"]["quote"] == "yes"


def test_ended_with_no_answers_is_failed(state: CallState) -> None:
    state.end("no one spoke")
    assert state.outcome() is Outcome.FAILED


def test_voicemail_forces_outcome_even_if_ended_later(state: CallState) -> None:
    state.mark_voicemail()
    state.end("left message")
    assert state.outcome() is Outcome.VOICEMAIL
    assert state.succeeded() is False
    assert len(state.to_result()["unanswered"]) == 4


def test_hold_tracking(state: CallState) -> None:
    msg = state.begin_hold("checking stock")
    assert state.on_hold is True
    assert "silent" in msg.lower()
    state.callee_spoke()
    assert state.on_hold is False
    assert state.hold_count == 1


def test_revision_keeps_history_and_latest_wins(state: CallState) -> None:
    state.record(Question.ITEM_IN_STOCK, True, quote="yeah in stock", confidence="medium")
    state.record(Question.ITEM_IN_STOCK, False, quote="oh wait, actually we're out", confidence="high")
    assert state.findings[Question.ITEM_IN_STOCK].value is False
    assert len(state.revisions) == 1


def test_downgrade_confidence_from_audit(state: CallState) -> None:
    state.record(Question.ITEM_IN_STOCK, True, quote="in stock", confidence="high")
    state.apply_audit({"item_in_stock": {"agrees": False, "note": "transcript says out of stock"}})
    assert state.findings[Question.ITEM_IN_STOCK].confidence == "low"
    assert any("item_in_stock" in n for n in state.notes)


def test_garbled_word_is_not_a_gating_no(state: CallState) -> None:
    # Real case: the supplier spoke another language, STT produced "Nava.", the model guessed "no".
    receipt = state.record(Question.DELIVERS_TO_ZIP, False, quote="Nava.", confidence="high")
    assert Question.DELIVERS_TO_ZIP not in state.findings
    assert state.disqualified_by() is None
    assert "NOT recorded" in receipt and "didn't catch that" in receipt
    assert state.remaining()[0] is Question.DELIVERS_TO_ZIP  # still the next thing to ask


def test_low_confidence_gating_no_is_not_recorded(state: CallState) -> None:
    receipt = state.record(Question.ACCEPTS_MEDICARE, False, quote="we don't, uh, maybe", confidence="low")
    assert Question.ACCEPTS_MEDICARE not in state.findings and "NOT recorded" in receipt


def test_second_unclear_gating_no_escalates(state: CallState) -> None:
    state.record(Question.DELIVERS_TO_ZIP, False, quote="Nava.", confidence="high")
    receipt = state.record(Question.DELIVERS_TO_ZIP, False, quote="hmm", confidence="high")
    assert "trouble hearing you" in receipt and "end_call" in receipt
    assert state.disqualified_by() is None


def test_clear_no_still_disqualifies(state: CallState) -> None:
    state.record(Question.DELIVERS_TO_ZIP, False, quote="No, we don't go up there.", confidence="high")
    assert state.disqualified_by() is Question.DELIVERS_TO_ZIP


def test_unclear_yes_is_still_recorded_but_gating_only_guards_no(state: CallState) -> None:
    # Only a disqualifying "no" is guarded; a "yes" can't end the call, so the model's judgement stands.
    state.record(Question.DELIVERS_TO_ZIP, True, quote="yeah", confidence="medium")
    assert state.findings[Question.DELIVERS_TO_ZIP].value is True


def test_off_topic_is_never_answered_and_fourth_ends_the_call(state: CallState) -> None:
    from supplier_caller.models import OFF_TOPIC_LIMIT

    r1 = state.note_off_topic("What is two plus two?")
    assert "Do NOT answer" in r1 and "1/4" in r1 and "deliver" in r1  # steers back to the next question
    state.note_off_topic("Write me Django code")
    r3 = state.note_off_topic("Sing for me")
    assert "3/4" in r3 and "end_call" not in r3.split("Say")[0]
    r4 = state.note_off_topic("Binary tree in Python first")
    assert "end_call" in r4 and "I'll let you go" in r4
    assert len(state.off_topic) == OFF_TOPIC_LIMIT
    state.end("callee off-topic")
    assert state.outcome() is Outcome.FAILED  # nothing learned, and the result says why
    assert any("off-topic" in n for n in state.notes)
    assert state.to_result()["off_topic"][0] == "What is two plus two?"


def test_background_talk_during_hold_keeps_the_hold(state: CallState) -> None:
    import time as _t

    state.begin_hold("let me check")
    started = state.hold_started_at
    _t.sleep(0.01)
    state.callee_spoke()  # provisional: something was heard
    assert state.on_hold is False
    msg = state.resume_hold("put that box over there, Dana")
    assert state.on_hold is True and state.hold_started_at == started  # same hold, not a new one
    assert "Say NOTHING" in msg
    assert state.background_heard == ["put that box over there, Dana"]
    assert state.total_hold_seconds < 0.001  # the false wake-up didn't bank hold time twice
    state.callee_spoke()  # now they really are back
    assert state.on_hold is False and state.total_hold_seconds >= 0.01
    assert state.to_result()["background_heard"] == ["put that box over there, Dana"]


def test_is_back_cue() -> None:
    from supplier_caller.models import is_back_cue

    for background in ["um", "Dana", "[door]", "hmm", "later"]:
        assert not is_back_cue(background), background
    for back in ["okay", "Sorry.", "hello?", "yeah so that ZIP is fine", "you still there", "no"]:
        assert is_back_cue(back), back


def test_addressed_to_us_beats_hold() -> None:
    from supplier_caller.models import is_addressed_to_us

    for said in ["I think you called the wrong number", "Hello?", "Why are you being so silent?", "bye bye",
                 "Sorry about that, yeah we deliver there", "okay so the ZIP is fine"]:
        assert is_addressed_to_us(said), said
    for background in ["put that box over there Dana", "yeah the Toyota one", "two of them by Thursday"]:
        assert not is_addressed_to_us(background), background


def test_wrong_number_forces_outcome(state: CallState) -> None:
    state.begin_hold("wait")
    msg = state.mark_wrong_number("you called the wrong number")
    assert state.on_hold is False and "end_call" in msg
    state.end("wrong number")
    assert state.outcome() is Outcome.WRONG_NUMBER and state.succeeded() is False
    assert any("wrong number" in n for n in state.notes)
