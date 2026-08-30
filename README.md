# Supplier Caller

A voice agent that phones a medical-equipment supplier for a Medicare patient and asks four things:

1. Do you deliver to ZIP 60640?
2. Are you taking new Medicare patients?
3. Is a K0001 manual wheelchair in stock?
4. How soon can you deliver?

It comes back with a JSON result that says what it learned, what it did not, and a quote for each answer.

The patient in the demo is Eleanor Martinez, 72, Medicare Part B, Chicago. Everything about her lives in
`supplier_caller/data/patients.json` — change it and the whole call follows. The supplier and the number to
dial live in `supplier_caller/data/suppliers.json`.

Design, cut list and roadmap are in **[WRITEUP.md](WRITEUP.md)**.

---

## 🎧 Recordings — start here

Three calls. The first two are **real phone calls to a human**; the third is **one call where a simulated
supplier throws every edge case at the agent**. Every recording is real audio out of the same agent.

| | call | what happens | outcome | 🔊 audio | 📄 transcript | 🧾 result |
|---|---|---|---|---|---|---|
| **1** | **Human, real phone — awkward but successful** | "who is this for?", hold while the person talks to a colleague, code read back wrong ("K one zero one") and corrected, all four answers | `complete`, 124 s | [▶ mp3](recordings/scenario-1-complete-hold-and-wrong-code-readback.mp3) | [open](recordings/scenario-1-complete-hold-and-wrong-code-readback.transcript.txt) | [json](results/scenario-1-complete-hold-and-wrong-code-readback.json) |
| **2** | **Human, real phone — guardrails** | asks the agent to write Python (refused, steered back), Medicare-vs-Medicaid question, then "you have the wrong person" → apology, hang-up | `wrong_number`, 95 s | [▶ mp3](recordings/scenario-2-off-topic-then-wrong-number.mp3) | [open](recordings/scenario-2-off-topic-then-wrong-number.transcript.txt) | [json](results/scenario-2-off-topic-then-wrong-number.json) |
| **3** | **Simulated supplier — every edge case in one call** (automated test) | "who's this for?" → 20 s hold while talking to a colleague in the background → two answers in one breath → "K zero zero zero three" caught and corrected → "are you the patient?" → "what's two plus two?" refused → delivery date | `complete`, 150 s | [▶ mp3](automated_testing/00-ALL-EDGE-CASES-one-real-audio-call-FR1-FR4-FR5-FR6-FR7-FR8-FR12-FR16-FR17-FR18.mp3) | [open](automated_testing/00-ALL-EDGE-CASES-one-real-audio-call-FR1-FR4-FR5-FR6-FR7-FR8-FR12-FR16-FR17-FR18.transcript.txt) | [json](automated_testing/00-ALL-EDGE-CASES-one-real-audio-call-FR1-FR4-FR5-FR6-FR7-FR8-FR12-FR16-FR17-FR18.json) |

Calls 1–2: the supplier is a person on a mobile phone (Plivo → PSTN), unscripted. Call 3: the supplier is a second
voice agent playing a scripted persona in a LiveKit room; the caller agent is identical and does not know which
it is talking to. 13 more scripted scenarios, one requirement group each, are indexed in
**[`automated_testing/README.md`](automated_testing/README.md)**.

---

## What's real, what's simulated

**Everything on the audio path is real.** The agent's voice is Deepgram text-to-speech published into a
LiveKit room, sent over a Plivo SIP trunk to the public phone network, and the supplier's voice comes back the
same way through voice detection → end-of-turn detection → Deepgram speech-to-text → gpt-4.1 → text-to-speech.
Pauses, interruptions and mishearings in the recordings all really happened on a phone line.

**On the two human calls (1 and 2), nothing is simulated.** The supplier is a person on a mobile phone playing
the role unscripted — holds, questions back, off-topic demands, "wrong person". The agent never knows in advance
what it will get.

**On call 3 (the automated test), the supplier is simulated.** It is a second voice agent in the same LiveKit
room — same Deepgram speech stack, a scripted persona behind gpt-4.1 — that runs through every awkward
behaviour in one call. The audio is still real (its speech is synthesised, sent as audio, and heard by the
caller through the same voice detection → speech-to-text path); only the *person* is scripted. The caller
agent is identical in all three calls and cannot tell which kind of supplier it has.

**What's mocked:** the queue is local files standing in for SQS, and the job store is a JSON file standing in
for a database. Neither touches the call itself.

---

## Run it

```bash
cp .env.example .env                  # LIVEKIT_*, DEEPGRAM_VOICE_API_KEY, OPENAI_API_KEY, then the Plivo values (below)
make install                          # venv + deps + downloads the voice-detection models
python -m supplier_caller.setup_sip   # once: creates the LiveKit ↔ Plivo trunk, writes SIP_OUTBOUND_TRUNK_ID to .env
make worker                           # terminal 1: the caller agent
make server                           # terminal 2: web UI at http://localhost:8000 — pick a patient, click Call
```

Or one call from the command line, no UI:

```bash
make call                        # dials the supplier's number in suppliers.json
make call PHONE=+1XXXXXXXXXX     # dial a different number
```

Every call leaves behind:

| file | what |
|---|---|
| `results/<room>.json` | outcome, each answer with quote and confidence, unanswered questions, full transcript |
| `recordings/<room>.mp3` | the call audio (`.ogg` is two-channel: left = supplier, right = agent) |
| `recordings/<room>.transcript.txt` | readable transcript with tool calls |

The UI shows the same per job: answers, the recording with a click-to-seek transcript, tool calls, and the
after-call audit. Calls go through a small queue with retries and a dead-letter list; concurrency and retry
counts are in `supplier_caller/data/workflows.json`.

---

## How it works, in short

```
  caller agent (agent.py)                              LiveKit room + SIP trunk        supplier
  ─────────────────────────                            ────────────────────────        ────────
  Deepgram Nova-3   speech → text     ◀──── audio ────────┐
  OpenAI gpt-4.1    decides + calls tools                  ├──── Plivo ──── ☎ real phone
  Deepgram Aura-2   text → speech     ───── audio ────────┘
  CallState (models.py) = the truth: what is known, what is missing, the outcome

  after the call: gpt-4.1 re-reads the transcript and lowers confidence on any answer it disagrees with
```

- **Speaks first, like a person.** On pickup: "Hi, this is Sam from Northside Care Partners — how are you
  today?" When they reply, it says why it's calling in one sentence and asks the first question. Then one
  question at a time, with a word of acknowledgement between.
- **The model reports facts through tools; `CallState` decides the outcome.** Each tool replies with what's
  still missing and what to ask next, so the agent picks its thread back up after any interruption.
- **Handles on its own:** hold (six words, then silence), questions back at it, two answers in one sentence,
  talk-over, wrong code read back, "that's not me", voicemail.
- **On hold, it stays on hold.** Twenty seconds of nothing, a door, a keyboard, someone talking in the
  background — none of it wakes the agent. One-word noises are dropped before the model ever sees them;
  longer talk gets judged with a reminder that we're on hold, and if it wasn't for us (`background_talk`)
  the agent keeps waiting silently. Only when the supplier clearly speaks *to it* does the call continue —
  and "wrong number", "hello?", "why are you so quiet?" or "bye" always count as speaking to it.
- **Wrong number → apologise and hang up.** "You've got the wrong number" ends the call at once with outcome
  `wrong_number`; no arguing, no re-asking. It never speaks stage directions like "(Silence)" — if the model
  writes one, it is swallowed before text-to-speech.
- **Stays on topic, like a person on a work call.** Maths puzzles, coding questions, "sing for me",
  bargaining — it doesn't answer, says it can only help with the wheelchair order today, and asks its question
  again. After the **fourth** unrelated thing it says "I'll let you go — thanks for your time" and hangs up.
  Questions *about the call* (who's this for? what code? where are you calling from?) are still answered.
- **Never hangs up on a word it didn't understand.** A "no" on delivery or Medicare ends the call, so the
  state machine only accepts one when the supplier's own words contain a clear negative and the model was
  sure. A garbled word or another language gets "Sorry, I didn't catch that" and the question again.
- **Ignores the room around the phone.** Background-voice cancellation (Krisp BVC, telephony model) runs on
  the incoming audio, and an interruption needs 0.6 s of speech *and* two real words — so someone talking
  near the supplier's desk doesn't get transcribed as the supplier, and doesn't cut the agent off.
  Tune without code changes via `.env`: `VAD_ACTIVATION` (0.6; raise to ignore more noise),
  `MIN_INTERRUPTION_SECONDS` (0.6), `MIN_INTERRUPTION_WORDS` (2) — lower them if the agent is too hard to
  interrupt.
- **Languages.** English by default, with keyterm boosting for the code and ZIP. Set `STT_LANGUAGE=multi`
  and Deepgram Nova-3 detects the language per utterance and code-switches automatically (the turn detector
  switches to its multilingual model too). Cost: keyterm boosting is English-only, so codes over a phone line
  get a little less help; latency is about the same.
- **Outcomes:** `complete`, `disqualified`, `partial`, `voicemail`, `no_answer`, `failed`. `succeeded` is true
  only for the first two.

Full reasoning in [WRITEUP.md](WRITEUP.md).

---

## Requirements it was built against

Numbered so tests can point at them (`tests/` mention FR-8, FR-16, …).

### Questions and rules

| # | Requirement |
|---|---|
| FR-1 | Get four answers: delivers to the ZIP · taking new Medicare patients · item in stock · soonest delivery. |
| FR-2 | If they do **not** deliver to the ZIP: stop asking, thank them, hang up. Outcome `disqualified`. |
| FR-3 | If they are **not** taking new Medicare patients: same as FR-2. |
| FR-4 | If they volunteer an answer before it was asked, record it and never ask that question. |
| FR-5 | When all four are in: confirm briefly, thank them, hang up. Outcome `complete`. |

### Handling what the supplier does

| # | Requirement |
|---|---|
| FR-6 | "Hold on / let me check": reply in six words or fewer, then stay silent. No re-prompt for 150 s. Silence is not a hang-up. |
| FR-7 | If they ask the agent a question, answer it from the patient facts, then continue with the next *unanswered* question. Never re-ask. |
| FR-8 | If they read back a wrong code (K0003), correct it by spelling the right one: "K zero zero zero one". |
| FR-9 | If they talk over the agent, the agent stops and listens (barge-in). |
| FR-10 | "That's not me, hang on": wait as in FR-6, then re-introduce to the new person, keeping what was already recorded. |
| FR-10b | "Wrong number" / not a supplier: apologise in one sentence and hang up. Outcome `wrong_number`. |
| FR-11 | Voicemail: leave a message under 15 s with a callback number. Outcome `voicemail`. |
| FR-18 | Unrelated questions or demands are never answered; the agent steers back to its question. After four unrelated turns it ends the call politely. |
| FR-19 | A disqualifying "no" is recorded only when the supplier's words clearly contain a negative; anything unclear is asked again, never guessed. |

### The result must be honest

| # | Requirement |
|---|---|
| FR-12 | The result comes from tool calls (`record_answer`), never from the model saying "I got it". |
| FR-13 | Every unanswered question is listed explicitly. |
| FR-14 | Each answer has a value, a verbatim quote, and a confidence level. |
| FR-15 | After the call, a second model pass re-reads the transcript; any disagreement lowers that answer's confidence and adds a note. |

### How it speaks

| # | Requirement |
|---|---|
| FR-16 | Codes and ZIPs are spoken digit by digit. |
| FR-17 | Sounds like a person: no lists, one question at a time, at most two sentences per turn. |

### Limits

| # | Requirement |
|---|---|
| NFR-1 | A cooperative call finishes in under 60 s of talk. |
| NFR-2 | The audio path is real: phone via LiveKit SIP + Plivo, Deepgram speech in and out. |
| NFR-3 | Hard caps: 6 minutes per call, 5 minutes total on hold. |

### When things go wrong

| what happens | what the agent does |
|---|---|
| nobody picks up | outcome `no_answer` |
| on hold more than 5 min | one check-in, then hang up with `partial` and a note |
| call passes 6 min | wrap up, `partial` |
| speech or model error | outcome `failed`, error in the notes |

---

## Phone setup (Plivo, about 10 minutes)

Plivo only lets you call out from a number you rent from them.

1. **Account** at console.plivo.com. A trial account can only call numbers you verify in
   *Phone Numbers → Sandbox Numbers* — fine for calling yourself. Add credit to call anyone.
2. **Buy a US number** (Phone Numbers → Buy Numbers → US, Voice, Local; ~$0.50/month).
3. **Create an outbound trunk**: SIP Trunking (Zentrunk) → Outbound Trunks → Create. Add a credentials list
   (username 5–20 letters/digits, password 5–20 chars with a special character). Copy the
   *Termination SIP Domain*, which looks like `<trunk_id>.zt.plivo.com`.
4. **Put it in `.env`** and run the setup script:
   ```
   PLIVO_TRUNK_DOMAIN=<trunk_id>.zt.plivo.com
   PLIVO_SIP_USERNAME=...
   PLIVO_SIP_PASSWORD=...
   PLIVO_NUMBER=+1XXXXXXXXXX
   ```
   ```bash
   python -m supplier_caller.setup_sip     # creates the LiveKit trunk, writes SIP_OUTBOUND_TRUNK_ID to .env
   ```
5. **Put the supplier's number in `supplier_caller/data/suppliers.json`** and run `make call`.

If it fails: LiveKit Cloud → Telephony → Calls shows the SIP status. `503` = wrong trunk domain,
`403` = bad credentials, `4190` in the Plivo log = the caller ID is not a Plivo number.
US calls cost about $0.005 per minute.

---

## Testing — what was tested, how, and where the evidence is

Three layers. Each requirement (FR-x below) is covered by at least one of them.

| layer | what it exercises | how it runs | where to look |
|---|---|---|---|
| **Manual — real phone calls** | The whole thing on real audio: Deepgram → LiveKit → Plivo → a human on a mobile playing the supplier, unscripted. Barge-in, real silence, background talk, phone-line mishearings. | `make call`, a person answers and misbehaves on purpose | [`recordings/`](recordings/) — `.mp3` audio + `.transcript.txt`; [`results/`](results/) — result JSON |
| **Automated — scenario tests** | (A) **one real-audio call** through the full speech pipeline against a simulated supplier that throws every edge case at the agent in a single call — question back, hold with background talk, two answers at once, wrong code, off-topic; (B) **13 scripted scenarios** in text mode against the real prompt, tools and model, 4–6 checks each. File names carry the FR numbers they cover. | Simulated-supplier persona / scripted lines fed to the agent; the harness itself is not in this repo | **[`automated_testing/README.md`](automated_testing/README.md)** — index with links to each scenario's audio / transcript / result |
| **Unit tests** | The deterministic core: `CallState` rules, outcomes, the "clear no" and off-topic guards, hold accounting, code spelling, queue/DLQ, catalog, consumer. No network. | `make test` (59 tests) · `make lint` | `tests/` |

```bash
make test        # 59 unit tests, offline
make test-live   # 6 text-mode behaviour tests that call the real model
make lint
```

What only the manual layer can prove: talking over the agent, 20 s of literal silence, background *noise*, the
STT itself mishearing a code. What only the automated layer gives you: the same 13 situations, repeatable, in
about two minutes, without a phone.

---

## Files

```
supplier_caller/
  agent.py         the caller: LiveKit worker, SIP dial, tools, hold/timeout guards, recording, hang-up
  prompts.py       caller instructions and the opening line
  models.py        CallState — the truth: answers, rules, outcome, result JSON
  codes.py         HCPCS code normalising ("K zero zero zero three" → K0003) and readback checks
  speech.py        speak codes and ZIPs digit by digit
  audit.py         after-call second opinion on the transcript
  server.py        FastAPI: UI, API, queue consumer
  static/index.html  the UI
  queue.py         SQS-shaped queue; local file version (+ dead-letter, redrive); SQS stub
  consumer.py      queue → per-workflow concurrency → dispatch; retry, then dead-letter
  jobs.py          job records
  catalog.py       loads the JSON data, builds call requests
  dispatch.py      creates the LiveKit room, dispatches the agent, waits for the result
  run_call.py      command-line: one call, no queue
  setup_sip.py     one-time LiveKit ↔ Plivo trunk setup
  data/*.json      patients, suppliers (numbers to dial), workflows, caller identity
tests/             unit tests + live behaviour tests
automated_testing/ 13 scripted scenarios run against the real prompt/tools/model in text mode —
                     index in automated_testing/README.md; per scenario a .transcript.txt and .json,
                     named by the requirements they cover (e.g. 08-FR2-FR12-FR13-does-not-deliver-to-zip…)
recordings/        call audio (.mp3) + transcripts, two real phone calls (manual testing):
                     scenario-1-complete-hold-and-wrong-code-readback  — "who is this for?", hold with a colleague
                                                                         talking in the background, code read back
                                                                         wrong and corrected, all four answers
                     scenario-2-off-topic-then-wrong-number            — asks for Python code (refused), Medicare vs
                                                                         Medicaid, "you have the wrong person" → hang up
WRITEUP.md         stack & design, cut list, what's next
```
