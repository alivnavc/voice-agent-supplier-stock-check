"""The caller: a LiveKit Agents worker that phones one DME supplier about one patient.

Pipeline:  callee audio -> Deepgram Nova-3 STT -> Silero VAD + LiveKit turn detector
           -> OpenAI (tool-calling) -> Deepgram Aura-2 TTS -> room / SIP participant.
Truth lives in CallState (models.py); the LLM only reports what it heard through tools.
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
from collections.abc import AsyncIterable
from typing import Literal

from livekit import api, rtc
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    ModelSettings,
    RunContext,
    StopResponse,
    WorkerOptions,
    cli,
    function_tool,
    llm,
)
from livekit.agents.voice.room_io import AudioInputOptions, RoomOptions
from livekit.plugins import deepgram, noise_cancellation, openai, silero
from livekit.plugins.turn_detector.english import EnglishModel
from livekit.plugins.turn_detector.multilingual import MultilingualModel

from . import config
from .audit import audit_call
from .catalog import Catalog
from .codes import readback_matches, spell_code
from .models import (
    CallRequest,
    CallState,
    Outcome,
    Question,
    Supplier,
    human_question,
    is_addressed_to_us,
    is_back_cue,
)
from .prompts import build_caller_instructions, build_opener
from .speech import is_stage_direction, speakable

logger = logging.getLogger("supplier-caller")

ALL_KINDS = [
    rtc.ParticipantKind.PARTICIPANT_KIND_SIP,
    rtc.ParticipantKind.PARTICIPANT_KIND_STANDARD,
    rtc.ParticipantKind.PARTICIPANT_KIND_AGENT,
]

DEFAULT_REQUEST = Catalog.load().default_request()  # first patient + first supplier in data/*.json

YesNoQuestion = Literal["delivers_to_zip", "accepts_medicare", "item_in_stock"]
Conf = Literal["high", "medium", "low"]


class DMECallerAgent(Agent):
    def __init__(self, state: CallState, done: asyncio.Event) -> None:
        super().__init__(instructions=build_caller_instructions(state.request))
        self.state = state
        self.done = done

    # --- speech shaping -------------------------------------------------------
    async def tts_node(self, text: AsyncIterable[str], model_settings: ModelSettings):
        """Spell HCPCS codes / ZIPs digit-by-digit. Buffers to whole words so a code isn't split."""

        async def shaped() -> AsyncIterable[str]:
            buf = ""
            holding = None  # None = undecided, True = looks like "(Silence)": buffer it all, False = normal
            async for chunk in text:
                buf += chunk
                if holding is None and buf.strip():
                    holding = buf.lstrip()[0] in "([*"
                if holding:
                    continue  # decide at the end whether this is a stage direction
                cut = max(buf.rfind(" "), buf.rfind("\n"))
                if cut >= 0:
                    yield speakable(buf[: cut + 1])
                    buf = buf[cut + 1 :]
            if buf and not is_stage_direction(buf):
                yield speakable(buf)
            elif buf:
                logger.info("swallowed stage direction instead of speaking it: %r", buf)

        return Agent.default.tts_node(self, shaped(), model_settings)

    # --- on hold: don't let background talk wake us up ---------------------------
    async def on_user_turn_completed(self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage) -> None:
        if not self.state.on_hold:
            return
        text = (new_message.text_content or "").strip()
        if is_addressed_to_us(text):
            # "wrong number", "hello?", "why are you so quiet", "bye" — always for us. Hold is over, respond.
            logger.info("on hold, but clearly addressed to us: %r", text)
            self.state.callee_spoke()
            return
        if not is_back_cue(text):
            # a grunt, a door, one word of someone else's conversation: drop it, the model never sees it
            logger.info("on hold, ignoring background: %r", text)
            self.state.background_heard.append(text)
            raise StopResponse()
        # Might be them coming back — let the model judge, with a reminder of where we are.
        self.state.callee_spoke()  # provisional; background_talk() undoes it
        turn_ctx.add_message(
            role="system",
            content=(
                "[ON HOLD] You were put on hold. What you just heard MAY be background talk not meant for you "
                "(the supplier talking to a colleague, a TV, a customer at the desk) — if so, call "
                "background_talk and write NO words at all (never write '(Silence)' or any stage direction). "
                "But anything about THIS call is for you and you must respond: wrong number, goodbye, hello, "
                "asking why you're quiet, an answer to your question, 'sorry about that', 'okay so…'."
            ),
        )

    # --- tools: the only way facts enter the result -----------------------------
    @function_tool()
    async def record_answer(
        self, ctx: RunContext, question: YesNoQuestion, answer: bool, quote: str, confidence: Conf
    ) -> str:
        """Record a yes/no fact the supplier just gave (delivers to ZIP, taking new Medicare, item in stock).
        Call once per fact, immediately, even if you didn't ask for it. `quote` = their words, verbatim."""
        msg = self.state.record(Question(question), answer, quote=quote, confidence=confidence)
        logger.info("record_answer %s=%s (%s) %r", question, answer, confidence, quote)
        return msg

    @function_tool()
    async def record_delivery_eta(self, ctx: RunContext, eta: str, quote: str, confidence: Conf) -> str:
        """Record how soon they can deliver, e.g. "Thursday, ~2 business days". Use their words for `quote`."""
        msg = self.state.record(Question.DELIVERY_ETA, eta, quote=quote, confidence=confidence)
        logger.info("record_delivery_eta %r (%s)", eta, confidence)
        return msg

    @function_tool()
    async def callee_on_hold(self, ctx: RunContext, reason: str) -> str:
        """Call when they say hold on / let me check / let me transfer you / that's not me. Then go quiet."""
        logger.info("callee_on_hold: %s", reason)
        return self.state.begin_hold(reason)

    @function_tool()
    async def wrong_number(self, ctx: RunContext, what_they_said: str) -> str:
        """Call the moment they say this is the wrong number / not a medical supplier / they don't know what
        you're talking about as a business. Then apologise in one sentence and call end_call."""
        logger.info("wrong_number: %r", what_they_said)
        return self.state.mark_wrong_number(what_they_said)

    @function_tool()
    async def background_talk(self, ctx: RunContext, heard: str) -> str:
        """While on hold: what you heard was not said to you (someone else in the room, the supplier talking to
        a colleague, TV). Call this and write NO text — you keep waiting silently."""
        logger.info("background_talk: %r", heard)
        return self.state.resume_hold(heard)

    @function_tool()
    async def callee_asked_question(self, ctx: RunContext, question: str) -> str:
        """Log a question the supplier asked ABOUT THIS CALL (who's this for? are you the patient? which code?
        where are you calling from?). Anything unrelated to the call goes to callee_off_topic instead."""
        self.state.note_callee_question(question)
        rem = self.state.remaining()
        nxt = f" Then continue with: {human_question(rem[0], self.state.request)}?" if rem else " Then wrap up."
        return "Answer it in one sentence from the patient facts." + nxt

    @function_tool()
    async def callee_off_topic(self, ctx: RunContext, what: str) -> str:
        """Call when the supplier says or asks something unrelated to this call — maths, coding, trivia,
        personal questions, "sing for me", bargaining, jokes. Do NOT answer it; the tool tells you what to say.
        The fourth unrelated thing ends the call."""
        logger.info("callee_off_topic: %s", what)
        return self.state.note_off_topic(what)

    @function_tool()
    async def check_code_readback(self, ctx: RunContext, heard: str) -> str:
        """Call when they repeat the HCPCS code back. Pass exactly what they said (e.g. 'K zero zero zero three')."""
        expected = self.state.request.patient.hcpcs
        ok = readback_matches(expected, heard)
        self.state.note_code_readback(heard, ok)
        if ok is True:
            return "They have the right code. Continue."
        if ok is None:
            return f"Couldn't make out a code. Repeat it once: '{spell_code(expected)}'."
        return f"Wrong code. Correct them plainly: 'Sorry, it's {spell_code(expected)} — one, not three.' Then continue."

    @function_tool()
    async def voicemail_detected(self, ctx: RunContext) -> str:
        """Call the moment you realise this is voicemail / an answering machine. It leaves the message for you."""
        self.state.mark_voicemail()
        r = self.state.request
        p = r.patient
        message = (
            f"Hi, this is {r.caller_name} with {r.caller_org}, calling about a patient, {p.name}, who needs a "
            f"{p.hcpcs} standard manual wheelchair delivered to ZIP {p.zip_code}. Could you call us back at "
            f"{r.callback_number}? Thanks."
        )
        logger.info("voicemail: leaving scripted message")
        await ctx.wait_for_playout()
        await ctx.session.say(message, allow_interruptions=False).wait_for_playout()
        return "Message left. Call end_call now, and say nothing else."

    @function_tool()
    async def end_call(self, ctx: RunContext, reason: str) -> str:
        """Hang up. Call this in the same turn as your goodbye sentence, never together with another tool."""
        logger.info("end_call: %s", reason)
        self.state.end(reason)
        await ctx.wait_for_playout()  # the goodbye that came with this tool call, if any
        spoke = any(
            i.type == "message" and i.role == "assistant" and (i.text_content or "").strip()
            for i in ctx.speech_handle.chat_items
        )
        if spoke or self.state.forced_outcome is Outcome.VOICEMAIL:
            self.done.set()  # goodbye / voicemail already played: hang up now
        else:
            # Goodbye is coming in the next step; the agent_state listener hangs up after it plays.
            asyncio.get_running_loop().call_later(8.0, self.done.set)
        return "Call ended. Say nothing more."


def _parse_job(ctx: JobContext) -> tuple[CallRequest, dict]:
    """The full CallRequest travels in the dispatch metadata (`request`); fall back to the catalog default."""
    meta = json.loads(ctx.job.metadata) if ctx.job.metadata else {}
    if meta.get("request"):
        req = CallRequest.from_dict(meta["request"])
    else:
        req = DEFAULT_REQUEST
    if meta.get("supplier_name") or meta.get("phone"):
        req = CallRequest(
            patient=req.patient,
            supplier=Supplier(name=meta.get("supplier_name", req.supplier.name), phone=meta.get("phone") or req.supplier.phone),
            caller_org=req.caller_org, caller_name=req.caller_name, callback_number=req.callback_number,
        )
    return req, meta


async def _dial(ctx: JobContext, phone: str, trunk_id: str, identity: str) -> bool:
    try:
        await ctx.api.sip.create_sip_participant(
            api.CreateSIPParticipantRequest(
                room_name=ctx.room.name,
                sip_trunk_id=trunk_id,
                sip_call_to=phone,
                participant_identity=identity,
                participant_name="Supplier",
                wait_until_answered=True,
                play_dialtone=True,
                krisp_enabled=True,
            )
        )
        return True
    except api.SipCallError as e:  # not answered / busy / rejected
        logger.warning("SIP call failed: %s %s", e.sip_status_code, e.sip_status)
    except api.TwirpError as e:
        logger.warning("SIP call failed: %s %s", e.code, e.message)
    return False


async def entrypoint(ctx: JobContext) -> None:
    req, meta = _parse_job(ctx)
    state = CallState(request=req)
    done = asyncio.Event()
    callee_identity = meta.get("callee_identity", "supplier-phone")
    config.RESULTS_DIR.mkdir(exist_ok=True)
    config.RECORDINGS_DIR.mkdir(exist_ok=True)
    result_path = config.RESULTS_DIR / f"{ctx.room.name}.json"

    await ctx.connect()

    # 1. Dial the supplier over SIP and wait for them to pick up.
    phone = meta.get("phone") or req.supplier.phone
    trunk_id = meta.get("trunk_id") or config.os.getenv("SIP_OUTBOUND_TRUNK_ID", "")
    if meta.get("dial", True) is False:
        logger.info("dial=false: the callee is dispatched to the room by the caller (test harness)")
    elif not (trunk_id and phone):
        state.mark_failed("no phone number / SIP_OUTBOUND_TRUNK_ID")
    elif not await _dial(ctx, phone, trunk_id, callee_identity):
        state.mark_no_answer()
    if state.forced_outcome:
        state.end("could not connect")
        _write_result(result_path, state, [], None)
        return
    try:
        callee = await asyncio.wait_for(
            ctx.wait_for_participant(kind=ALL_KINDS), timeout=config.CALLEE_JOIN_TIMEOUT
        )
    except TimeoutError:
        state.mark_no_answer()
        state.end("callee never joined")
        _write_result(result_path, state, [], None)
        return
    logger.info("callee joined: %s (%s)", callee.identity, callee.kind)

    # 2. Voice pipeline.
    p = req.patient
    multilingual = config.MODELS.stt_language == "multi"
    stt_kw: dict = {"model": config.MODELS.stt_model, "language": config.MODELS.stt_language}
    if multilingual:
        logger.info("STT language=multi: keyterm boosting disabled (English-only feature)")
    else:
        stt_kw["keyterm"] = [p.hcpcs, "Medicare", "wheelchair", p.zip_code, "Uptown", "hold on"]
    session: AgentSession[CallState] = AgentSession(
        stt=deepgram.STT(**stt_kw),
        llm=openai.LLM(model=config.MODELS.caller_llm, temperature=0.4, parallel_tool_calls=True),
        tts=deepgram.TTS(model=config.MODELS.caller_voice),
        vad=ctx.proc.userdata["vad"],
        turn_detection=MultilingualModel() if multilingual else EnglishModel(),
        user_away_timeout=None,  # silence is never "the user left" — they're checking stock
        preemptive_generation=True,
        min_endpointing_delay=0.6,
        max_endpointing_delay=5.0,
        # Background talk near the handset must not count as the supplier speaking: an interruption needs
        # N seconds of speech AND at least N transcribed words; if nothing intelligible arrives, the agent
        # resumes what it was saying. Knobs live in config.py (env-overridable).
        min_interruption_duration=config.MIN_INTERRUPTION_SECONDS,
        min_interruption_words=config.MIN_INTERRUPTION_WORDS,
        false_interruption_timeout=config.FALSE_INTERRUPTION_TIMEOUT,
        resume_false_interruption=True,
        userdata=state,
    )

    @session.on("agent_state_changed")
    def _on_agent_state(ev) -> None:
        # Hang up only once the goodbye has actually been spoken.
        if state.ended and ev.new_state in ("listening", "idle"):
            done.set()

    @ctx.room.on("participant_disconnected")
    def _on_left(part: rtc.RemoteParticipant) -> None:
        if part.identity == callee.identity and not state.ended:
            logger.info("callee hung up")
            state.end("callee hung up")
            done.set()

    agent = DMECallerAgent(state, done)
    await session.start(
        agent,
        room=ctx.room,
        record=True,  # 2-channel OGG: ch0 = callee, ch1 = agent
        room_options=RoomOptions(
            participant_identity=callee.identity,
            participant_kinds=ALL_KINDS,
            # Krisp background-voice cancellation tuned for phone audio: keeps the person on the line,
            # drops other voices / office noise around them before STT ever hears it.
            audio_input=AudioInputOptions(noise_cancellation=noise_cancellation.BVCTelephony()),
        ),
    )

    # 3. We speak first: greeting, who we are, why we're calling, first question. Interruptible, so if
    #    they talk over it ("Lakeview, this is Dana") the model picks up from whatever they said.
    await asyncio.sleep(0.8)  # let the audio path settle so the first word isn't clipped
    await session.say(build_opener(req)).wait_for_playout()
    await asyncio.sleep(5.0)
    if not any(i.type == "message" and i.role == "user" for i in session.history.items) and not state.ended:
        session.generate_reply(instructions="They haven't said anything since your opener. Ask 'Hello, can you hear me?'")

    # 4. Guards: hold check-in / give-up, hard max duration.
    async def guards() -> None:
        checked_in = False
        while not done.is_set():
            await asyncio.sleep(2.0)
            now = time.time()
            if state.on_hold and state.hold_started_at:
                held = now - state.hold_started_at
                if held > config.HOLD_GIVE_UP_AFTER:
                    state.notes.append(f"gave up after {held:.0f}s on hold")
                    state.end("hold timeout")
                    done.set()
                elif held > config.HOLD_CHECKIN_AFTER and not checked_in:
                    checked_in = True
                    session.say("Still here whenever you're ready.")
            if now - state.started_at > config.MAX_CALL_SECONDS and not state.ended:
                state.notes.append("hit max call duration")
                session.generate_reply(
                    instructions="You're out of time. Thank them in one sentence and call end_call."
                )
                await asyncio.sleep(15)
                if not done.is_set():
                    state.end("max duration")
                    done.set()

    guard_task = asyncio.create_task(guards())
    await done.wait()
    guard_task.cancel()
    if not state.ended:
        state.end("session closed")

    # 5. Finalise: close (flushes the recorder and commits the last turn), audit, write, hang up.
    rec_started = getattr(getattr(session, "_recorder_io", None), "recording_started_at", None) or state.started_at
    await session.aclose()
    transcript = session.history.to_dict(exclude_timestamp=False).get("items", [])
    for it in transcript:  # seconds into the recording, for the UI's click-to-seek transcript
        if isinstance(it.get("created_at"), (int, float)):
            it["t"] = round(max(0.0, it["created_at"] - rec_started), 1)
    recording = _save_recording(ctx, ctx.room.name)
    _write_transcript(ctx.room.name, transcript)
    try:
        audit = await asyncio.wait_for(audit_call(state, transcript), timeout=30)
        state.apply_audit(audit)
    except Exception as e:  # noqa: BLE001 — audit is a bonus, never a blocker
        logger.warning("audit failed: %s", e)
        audit = {"error": str(e)}
    _write_result(result_path, state, transcript, recording, audit=audit, job_id=meta.get("job_id"),
                  recording_started_at=rec_started)
    try:
        await ctx.api.room.delete_room(api.DeleteRoomRequest(room=ctx.room.name))
    except api.TwirpError as e:  # room may already be gone
        logger.info("delete_room: %s", e.message)


def _save_recording(ctx: JobContext, room_name: str) -> str | None:
    src = ctx.session_directory / "audio.ogg"
    if not src.exists():
        logger.warning("no recording found at %s", src)
        return None
    dst = config.RECORDINGS_DIR / f"{room_name}.ogg"
    shutil.copy(src, dst)
    return str(dst)


def _write_transcript(room_name: str, transcript: list[dict]) -> None:
    lines = []
    for it in transcript:
        if it.get("type") == "message":
            c = it.get("content")
            c = " ".join(x for x in c if isinstance(x, str)) if isinstance(c, list) else c
            who = "SUPPLIER" if it.get("role") == "user" else "AGENT"
            lines.append(f"{who}: {c}")
        elif it.get("type") == "function_call":
            lines.append(f"  [tool] {it.get('name')}({it.get('arguments')})")
    (config.RECORDINGS_DIR / f"{room_name}.transcript.txt").write_text("\n".join(lines) + "\n")


def _write_result(path, state: CallState, transcript, recording, **extra) -> None:
    result = state.to_result(transcript=transcript, recording=recording, models=config.MODELS.__dict__, **extra)
    path.write_text(json.dumps(result, indent=2, default=str))
    logger.info(
        "RESULT %s outcome=%s answered=%s unanswered=%s",
        path.name, result["outcome"], list(result["answers"]), result["unanswered"],
    )


def prewarm(proc) -> None:
    # Higher activation threshold than the 0.5 default: quieter background voices don't start a "turn".
    proc.userdata["vad"] = silero.VAD.load(activation_threshold=config.VAD_ACTIVATION, min_speech_duration=0.1)


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, prewarm_fnc=prewarm, agent_name=config.CALLER_AGENT_NAME))
