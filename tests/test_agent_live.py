"""LLM-in-the-loop behaviour tests (text mode, no audio). Marked `live`: they call OpenAI.

    pytest -m live -q

These pin the behaviours the exercise cares about most, against the *real* prompt + tools + model.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from livekit.agents import AgentSession
from livekit.agents.voice.run_result import ChatMessageEvent, FunctionCallEvent
from livekit.plugins import openai

from supplier_caller import config
from supplier_caller.agent import DEFAULT_REQUEST, DMECallerAgent
from supplier_caller.models import CallState, Outcome, Question

pytestmark = pytest.mark.live


def _calls(result) -> list[tuple[str, dict]]:
    return [
        (ev.item.name, json.loads(ev.item.arguments or "{}"))
        for ev in result.events
        if isinstance(ev, FunctionCallEvent)
    ]


def _said(result) -> str:
    return " ".join(ev.item.text_content or "" for ev in result.events if isinstance(ev, ChatMessageEvent) and ev.item.role == "assistant")


@pytest.fixture()
async def convo():
    state = CallState(request=DEFAULT_REQUEST)
    done = asyncio.Event()
    session = AgentSession(llm=openai.LLM(model=config.MODELS.caller_llm, temperature=0.2, parallel_tool_calls=True))
    await session.start(DMECallerAgent(state, done))
    await session.run(user_input="Lakeview Medical Supply, this is Dana.")
    try:
        yield session, state
    finally:
        await session.aclose()


async def test_opener_asks_about_zip_first(convo) -> None:
    session, _state = convo
    opener = " ".join(
        m.text_content or "" for m in session.history.items if m.type == "message" and m.role == "assistant"
    )
    assert "60640" in opener or "six zero six four zero" in opener.lower()


async def test_two_answers_in_one_sentence_are_both_recorded_and_not_reasked(convo) -> None:
    session, state = convo
    await session.run(user_input="Yep, we go up there.")
    result = await session.run(user_input="Yeah we take Medicare, and we've got manual chairs in stock.")
    names = [n for n, _ in _calls(result)]
    assert names.count("record_answer") == 2
    assert state.findings[Question.ACCEPTS_MEDICARE].value is True
    assert state.findings[Question.ITEM_IN_STOCK].value is True
    said = _said(result).lower()
    assert "medicare" not in said or "?" not in said, said  # must not re-ask Medicare
    assert state.remaining() == [Question.DELIVERY_ETA]


async def test_hold_gets_a_tiny_ack_and_nothing_else(convo) -> None:
    session, state = convo
    result = await session.run(user_input="Hold on, let me check.")
    assert "callee_on_hold" in [n for n, _ in _calls(result)]
    assert state.on_hold is True
    assert len(_said(result).split()) <= 8, _said(result)


async def test_callee_question_is_answered_and_thread_resumes(convo) -> None:
    session, state = convo
    await session.run(user_input="Yeah, we deliver to 60640.")
    result = await session.run(user_input="Wait, who's this for?")
    said = _said(result).lower()
    assert "eleanor" in said or "martinez" in said
    assert "medicare" in said, said  # picks its own thread back up: next unanswered is Medicare
    assert Question.DELIVERS_TO_ZIP in state.findings  # and didn't lose what it had


async def test_wrong_code_readback_is_corrected(convo) -> None:
    session, state = convo
    result = await session.run(user_input="So that's a K zero zero zero three?")
    assert "check_code_readback" in [n for n, _ in _calls(result)]
    assert state.code_readbacks and state.code_readbacks[-1]["matches"] is False
    assert "zero zero zero one" in _said(result).lower() or "0001" in _said(result)


async def test_zip_no_ends_the_call_without_asking_the_rest(convo) -> None:
    session, state = convo
    result = await session.run(user_input="No, sorry, we don't deliver up there, only the western suburbs.")
    names = [n for n, _ in _calls(result)]
    assert "record_answer" in names and "end_call" in names
    assert state.outcome() is Outcome.DISQUALIFIED
    assert state.remaining() == []
    assert "medicare" not in _said(result).lower()
