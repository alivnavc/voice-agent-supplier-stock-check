"""Deterministic call state.

The LLM never decides whether the call succeeded. It reports facts through tools;
this module owns the truth: what is known, what is still missing, whether the
supplier is already disqualified, and what the outcome is.
"""
from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Literal

Confidence = Literal["high", "medium", "low"]


class Question(str, Enum):
    DELIVERS_TO_ZIP = "delivers_to_zip"
    ACCEPTS_MEDICARE = "accepts_medicare"
    ITEM_IN_STOCK = "item_in_stock"
    DELIVERY_ETA = "delivery_eta"


# Priority order. ZIP first: a "no" there makes the rest of the call pointless.
QUESTION_ORDER: tuple[Question, ...] = (
    Question.DELIVERS_TO_ZIP,
    Question.ACCEPTS_MEDICARE,
    Question.ITEM_IN_STOCK,
    Question.DELIVERY_ETA,
)
# Words that mean "I'm back / talking to you" after a hold. Anything shorter than two words without one of
# these is dropped as background while on hold, without waking the model.
BACK_CUES = frozenset(
    ["ok", "okay", "alright", "hello", "hi", "hey", "sorry", "yes", "yeah", "yep", "no", "nope", "back", "there", "so", "right", "thanks", "sir", "ma'am"]
)


def is_back_cue(text: str) -> bool:
    words = [w.strip(".,!?").lower() for w in text.split()]
    return len(words) >= 2 or any(w in BACK_CUES for w in words)


# Things that are ALWAYS said to us, hold or not: they end the hold outright and the model must respond.
ADDRESSED_PHRASES = (
    "wrong number", "wrong person", "not a supplier", "who is this", "who's this", "hello", "you there",
    "still there", "are you there", "so silent", "so quiet", "say something", "hang up", "bye", "goodbye",
    "sorry about that", "thanks for holding", "thanks for waiting", "okay so", "alright so",
)


def is_addressed_to_us(text: str) -> bool:
    t = " " + " ".join(text.lower().replace(",", " ").replace(".", " ").replace("?", " ").split()) + " "
    return any(f" {p} " in t or t.strip().startswith(p) for p in ADDRESSED_PHRASES)


# Unrelated remarks/questions from the callee before the agent politely hangs up.
OFF_TOPIC_LIMIT = 4
# A False on any of these ends the enquiry.
GATING_QUESTIONS: frozenset[Question] = frozenset({Question.DELIVERS_TO_ZIP, Question.ACCEPTS_MEDICARE})
BOOL_QUESTIONS: frozenset[Question] = frozenset(
    {Question.DELIVERS_TO_ZIP, Question.ACCEPTS_MEDICARE, Question.ITEM_IN_STOCK}
)


class Outcome(str, Enum):
    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"  # all four answered
    DISQUALIFIED = "disqualified"  # definitive no on a gating question
    PARTIAL = "partial"  # ended with some answers
    VOICEMAIL = "voicemail"
    NO_ANSWER = "no_answer"
    WRONG_NUMBER = "wrong_number"
    FAILED = "failed"  # ended with nothing usable


@dataclass(frozen=True)
class Patient:
    name: str
    age: int
    coverage: str
    item: str
    hcpcs: str
    city: str
    zip_code: str


@dataclass(frozen=True)
class Supplier:
    name: str
    phone: str | None


@dataclass(frozen=True)
class CallRequest:
    patient: Patient
    supplier: Supplier
    caller_org: str = "Northside Care Partners"
    caller_name: str = "Sam"
    callback_number: str = "312-555-0142"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CallRequest:
        pt = {k: v for k, v in d["patient"].items() if k in Patient.__dataclass_fields__}
        sp = {k: v for k, v in d["supplier"].items() if k in Supplier.__dataclass_fields__}
        extra = {k: d[k] for k in ("caller_org", "caller_name", "callback_number") if k in d}
        return cls(patient=Patient(**pt), supplier=Supplier(**sp), **extra)


@dataclass
class Finding:
    question: Question
    value: bool | str
    quote: str
    confidence: Confidence
    recorded_at: float = field(default_factory=time.time)


def human_question(q: Question, req: CallRequest) -> str:
    p = req.patient
    return {
        Question.DELIVERS_TO_ZIP: f"do you deliver to ZIP {p.zip_code} ({p.city})",
        Question.ACCEPTS_MEDICARE: "are you taking new Medicare patients",
        Question.ITEM_IN_STOCK: f"is a {p.hcpcs} ({p.item}) in stock",
        Question.DELIVERY_ETA: "how soon could you deliver it",
    }[q]


@dataclass
class CallState:
    request: CallRequest
    findings: dict[Question, Finding] = field(default_factory=dict)
    revisions: list[Finding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    callee_questions: list[str] = field(default_factory=list)
    code_readbacks: list[dict[str, Any]] = field(default_factory=list)
    unclear: dict[Question, int] = field(default_factory=dict)  # gating "no"s rejected as not clear enough
    off_topic: list[str] = field(default_factory=list)  # unrelated things the callee said/asked
    background_heard: list[str] = field(default_factory=list)  # speech during a hold that wasn't for us
    _prev_hold_started_at: float | None = None
    on_hold: bool = False
    hold_started_at: float | None = None
    hold_count: int = 0
    total_hold_seconds: float = 0.0
    ended: bool = False
    end_reason: str | None = None
    forced_outcome: Outcome | None = None
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None

    # ----- facts -----------------------------------------------------------
    def record(self, question: Question, value: bool | str, *, quote: str, confidence: Confidence) -> str:
        """Record an answer. Returns a short instruction for the LLM's next move."""
        if question in BOOL_QUESTIONS:
            if not isinstance(value, bool):
                raise ValueError(f"{question.value} needs true/false, got {value!r}")
            if question in GATING_QUESTIONS and value is False and not self._clear_no(quote, confidence):
                # A "no" here ends the whole call, so it must be unmistakable. A garbled word, another
                # language, or a low-confidence guess is not a no — ask again instead of hanging up.
                self.unclear[question] = self.unclear.get(question, 0) + 1
                q = human_question(question, self.request)
                if self.unclear[question] >= 2:
                    return (
                        f"NOT recorded: still couldn't get a clear answer to {question.value} ({quote!r}). "
                        f"Say 'Sorry, I'm having trouble hearing you' and ask once more, slowly: '{q}?' "
                        "If it is still unclear, thank them and call end_call."
                    )
                return (
                    f"NOT recorded: {quote!r} is not a clear 'no' to {question.value}. "
                    f"Say 'Sorry, I didn't catch that' and ask again: '{q}?'"
                )
        else:
            if isinstance(value, bool) or not str(value).strip():
                raise ValueError(f"{question.value} needs a short text answer, got {value!r}")
            value = str(value).strip()
        if question in self.findings:
            self.revisions.append(self.findings[question])
        self.findings[question] = Finding(question, value, quote.strip(), confidence)
        return self._receipt(question)

    _NEGATIVE = re.compile(
        r"\b(no|nope|nah|not|don'?t|doesn'?t|can'?t|cannot|won'?t|never|outside|only|unable|isn'?t|aren'?t)\b",
        re.IGNORECASE,
    )

    @classmethod
    def _clear_no(cls, quote: str, confidence: Confidence) -> bool:
        """True when the supplier's own words plainly contain a negative and the model was sure."""
        return confidence == "high" and bool(cls._NEGATIVE.search(quote))

    def _receipt(self, just_recorded: Question) -> str:
        head = f"Recorded {just_recorded.value}={self.findings[just_recorded].value!r}."
        dq = self.disqualified_by()
        if dq is not None:
            why = (
                f"they don't deliver to {self.request.patient.zip_code}"
                if dq is Question.DELIVERS_TO_ZIP
                else "they aren't taking new Medicare patients"
            )
            return (
                f"{head} This supplier is disqualified ({why}). Do NOT ask the remaining questions. "
                "Thank them in one short sentence and call end_call."
            )
        rem = self.remaining()
        if not rem:
            return f"{head} All four answers are in. Confirm briefly in one sentence, thank them, then call end_call."
        return (
            f"{head} Still need: {', '.join(q.value for q in rem)}. "
            f"Next, ask: {human_question(rem[0], self.request)}?"
        )

    def remaining(self) -> list[Question]:
        if self.disqualified_by() is not None:
            return []
        return [q for q in QUESTION_ORDER if q not in self.findings]

    def disqualified_by(self) -> Question | None:
        for q in QUESTION_ORDER:
            if q in GATING_QUESTIONS and q in self.findings and self.findings[q].value is False:
                return q
        return None

    def is_complete(self) -> bool:
        return all(q in self.findings for q in QUESTION_ORDER)

    # ----- events ----------------------------------------------------------
    def begin_hold(self, reason: str) -> str:
        if not self.on_hold:
            self.on_hold = True
            self.hold_started_at = time.time()
            self.hold_count += 1
        self.notes.append(f"on hold: {reason}")
        return (
            "Acknowledge in at most six words (e.g. 'Sure, take your time.') and then stay completely silent. "
            "Do not speak again until they come back, no matter how long it takes."
        )

    def callee_spoke(self) -> None:
        """They're back (or we think so). Closes the hold interval; resume_hold() can undo it."""
        if self.on_hold and self.hold_started_at is not None:
            self.total_hold_seconds += time.time() - self.hold_started_at
            self._prev_hold_started_at = self.hold_started_at
        self.on_hold = False
        self.hold_started_at = None

    def resume_hold(self, heard: str) -> str:
        """What we heard during the hold wasn't for us: keep waiting as if nothing happened."""
        self.background_heard.append(heard.strip())
        if not self.on_hold and self._prev_hold_started_at is not None:
            self.total_hold_seconds -= time.time() - self._prev_hold_started_at  # undo callee_spoke()
            self.hold_started_at = self._prev_hold_started_at
            self.on_hold = True
        return "Background talk, not for you. Say NOTHING. Keep waiting until they speak to you."

    def note_callee_question(self, question: str) -> None:
        self.callee_questions.append(question)

    def note_off_topic(self, what: str) -> str:
        """The callee said something unrelated to the call. Never entertain it; after OFF_TOPIC_LIMIT, hang up."""
        self.off_topic.append(what.strip())
        n = len(self.off_topic)
        if n >= OFF_TOPIC_LIMIT:
            self.notes.append(f"ended: callee off-topic {n} times ({'; '.join(self.off_topic)})")
            return (
                f"That is unrelated thing #{n}. Do NOT answer it. This call is going nowhere: say "
                "\"I'll let you go — thanks for your time.\" and call end_call in the same turn."
            )
        rem = self.remaining()
        nxt = f" Then ask again: '{human_question(rem[0], self.request)}?'" if rem else " Then wrap up."
        return (
            f"Do NOT answer that ({n}/{OFF_TOPIC_LIMIT} unrelated; at {OFF_TOPIC_LIMIT} you end the call). "
            f"Say in a few words that you can only help with the {self.request.patient.item} order today.{nxt}"
        )

    def note_code_readback(self, heard: str, matches: bool | None) -> None:
        self.code_readbacks.append({"heard": heard, "matches": matches})

    def mark_voicemail(self) -> None:
        self.forced_outcome = Outcome.VOICEMAIL

    def mark_no_answer(self) -> None:
        self.forced_outcome = Outcome.NO_ANSWER

    def mark_wrong_number(self, what_they_said: str = "") -> str:
        self.forced_outcome = Outcome.WRONG_NUMBER
        self.callee_spoke()
        self.notes.append(f"wrong number: {what_they_said.strip()}" if what_they_said else "wrong number")
        return "Wrong number. Apologise in one short sentence ('Sorry about that, have a good one.') and call end_call."

    def mark_failed(self, error: str) -> None:
        self.forced_outcome = Outcome.FAILED
        self.notes.append(f"error: {error}")

    def end(self, reason: str) -> None:
        if self.ended:
            return
        self.callee_spoke()  # closes any open hold interval
        self.ended = True
        self.end_reason = reason
        self.ended_at = time.time()

    # ----- verdict ---------------------------------------------------------
    def outcome(self) -> Outcome:
        if self.forced_outcome is not None:
            return self.forced_outcome
        if self.disqualified_by() is not None:
            return Outcome.DISQUALIFIED
        if self.is_complete():
            return Outcome.COMPLETE
        if self.ended:
            return Outcome.PARTIAL if self.findings else Outcome.FAILED
        return Outcome.IN_PROGRESS

    def succeeded(self) -> bool:
        """True when the advocate can act on the result without calling back."""
        return self.outcome() in (Outcome.COMPLETE, Outcome.DISQUALIFIED)

    def apply_audit(self, audit: dict[str, dict[str, Any]]) -> None:
        """Merge a second-model transcript audit: disagreements downgrade confidence."""
        for key, verdict in audit.items():
            try:
                q = Question(key)
            except ValueError:
                continue
            if q in self.findings and verdict.get("agrees") is False:
                self.findings[q].confidence = "low"
                self.notes.append(f"audit disagrees on {q.value}: {verdict.get('note', '')}".strip())

    def to_result(self, transcript: list[dict[str, Any]] | None = None, **extra: Any) -> dict[str, Any]:
        unanswered = [q.value for q in QUESTION_ORDER if q not in self.findings]
        return {
            "outcome": self.outcome().value,
            "succeeded": self.succeeded(),
            "supplier": asdict(self.request.supplier),
            "patient": asdict(self.request.patient),
            "answers": {
                q.value: {"value": f.value, "quote": f.quote, "confidence": f.confidence}
                for q, f in self.findings.items()
            },
            "unanswered": unanswered,
            "disqualified_by": self.disqualified_by().value if self.disqualified_by() else None,
            "callee_questions": list(self.callee_questions),
            "off_topic": list(self.off_topic),
            "background_heard": list(self.background_heard),
            "code_readbacks": list(self.code_readbacks),
            "holds": {"count": self.hold_count, "total_seconds": round(self.total_hold_seconds, 1)},
            "notes": list(self.notes),
            "end_reason": self.end_reason,
            "duration_seconds": round((self.ended_at or time.time()) - self.started_at, 1),
            "transcript": transcript or [],
            **extra,
        }
