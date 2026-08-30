# Automated testing — scenario index

> **Listen first:** [▶ the one call with every edge case](00-ALL-EDGE-CASES-one-real-audio-call-FR1-FR4-FR5-FR6-FR7-FR8-FR12-FR16-FR17-FR18.mp3) ([transcript](00-ALL-EDGE-CASES-one-real-audio-call-FR1-FR4-FR5-FR6-FR7-FR8-FR12-FR16-FR17-FR18.transcript.txt) · [result](00-ALL-EDGE-CASES-one-real-audio-call-FR1-FR4-FR5-FR6-FR7-FR8-FR12-FR16-FR17-FR18.json)) — simulated supplier, real audio.
> The three **human** phone calls are in [`../recordings/`](../recordings/): [▶ straightforward](../recordings/scenario-0-straightforward-all-four-answers.mp3) · [▶ awkward-but-complete](../recordings/scenario-1-complete-hold-and-wrong-code-readback.mp3) · [▶ off-topic then wrong number](../recordings/scenario-2-off-topic-then-wrong-number.mp3).
> All three are summarised in the main [README → Recordings](../README.md#-recordings--start-here).

Two kinds of evidence, both produced by the **real caller agent** (same prompt, same tools, same model). Click a link in a row to open that scenario's audio, transcript or result JSON.

## A. One real-audio call that hits every edge case

Runs through the real speech pipeline — LiveKit WebRTC room, Deepgram speech-to-text and text-to-speech, gpt-4.1 — against a simulated supplier (a second voice agent playing a scripted persona). Real audio, scripted behaviour. In one call the supplier: asks "who's this for?", puts you on hold for 20 s while talking to a colleague in the background, answers two questions in one breath, reads the code back as K0003, asks "are you the patient?", asks "what's two plus two?", and finally gives a delivery date.

| requirements | scenario | outcome | what happened | 🔊 audio | 📄 transcript | 🧾 result |
|---|---|---|---|---|---|---|
| FR-1, FR-4, FR-5, FR-6, FR-7, FR-8, FR-12, FR-16, FR-17, FR-18 | `00-ALL-EDGE-CASES-one-real-audio-call-FR1-FR4-FR5-FR6-FR7-FR8-FR12-FR16-FR17-FR18` | `complete` | 1 holds (28 s); 3 questions back; 1 wrong read-back caught; 1 off-topic refused; background talk ignored; 4/4 answers, 150 s | [▶ mp3](00-ALL-EDGE-CASES-one-real-audio-call-FR1-FR4-FR5-FR6-FR7-FR8-FR12-FR16-FR17-FR18.mp3) | [open](00-ALL-EDGE-CASES-one-real-audio-call-FR1-FR4-FR5-FR6-FR7-FR8-FR12-FR16-FR17-FR18.transcript.txt) | [json](00-ALL-EDGE-CASES-one-real-audio-call-FR1-FR4-FR5-FR6-FR7-FR8-FR12-FR16-FR17-FR18.json) |

## B. Thirteen scripted scenarios, one requirement group each (text mode)

Ran 2026-08-29 23:46 against `gpt-4.1`. Same agent, same prompt and tools, but the supplier's lines are fed as text (no audio, no phone), so each scenario is repeatable in seconds and asserts 4–6 checks. PASS/FAIL per check is written inside each transcript. No audio for these by design — the audio path is proven in A and in `recordings/`.

| # | requirements | scenario | outcome | checks | result | 📄 transcript | 🧾 result JSON |
|---|---|---|---|---|---|---|---|
| 1 | FR-1, FR-5, FR-17 | `01-FR1-FR5-FR17-straightforward-all-four-answers-under-a-minute` | `complete` | 5/5 | ✅ pass | [open](01-FR1-FR5-FR17-straightforward-all-four-answers-under-a-minute.transcript.txt) | [json](01-FR1-FR5-FR17-straightforward-all-four-answers-under-a-minute.json) |
| 2 | FR-6, FR-4 | `02-FR6-FR4-hold-silence-then-comes-back-mid-thought` | `partial` | 6/6 | ✅ pass | [open](02-FR6-FR4-hold-silence-then-comes-back-mid-thought.transcript.txt) | [json](02-FR6-FR4-hold-silence-then-comes-back-mid-thought.json) |
| 3 | FR-6, FR-12 | `03-FR6-FR12-hold-with-background-talk-not-for-us` | `partial` | 6/6 | ✅ pass | [open](03-FR6-FR12-hold-with-background-talk-not-for-us.transcript.txt) | [json](03-FR6-FR12-hold-with-background-talk-not-for-us.json) |
| 4 | FR-7, FR-4, FR-17 | `04-FR7-FR4-FR17-callee-asks-who-is-this-for-and-are-you-the-patient` | `partial` | 6/6 | ✅ pass | [open](04-FR7-FR4-FR17-callee-asks-who-is-this-for-and-are-you-the-patient.transcript.txt) | [json](04-FR7-FR4-FR17-callee-asks-who-is-this-for-and-are-you-the-patient.json) |
| 5 | FR-4, FR-12 | `05-FR4-FR12-two-answers-in-one-sentence-not-reasked` | `partial` | 5/5 | ✅ pass | [open](05-FR4-FR12-two-answers-in-one-sentence-not-reasked.transcript.txt) | [json](05-FR4-FR12-two-answers-in-one-sentence-not-reasked.json) |
| 6 | FR-8, FR-16 | `06-FR8-FR16-code-read-back-as-K0003-corrected` | `partial` | 4/4 | ✅ pass | [open](06-FR8-FR16-code-read-back-as-K0003-corrected.transcript.txt) | [json](06-FR8-FR16-code-read-back-as-K0003-corrected.json) |
| 7 | FR-10, FR-6 | `07-FR10-FR6-wrong-person-hold-then-new-person-picks-up` | `failed` | 5/5 | ✅ pass | [open](07-FR10-FR6-wrong-person-hold-then-new-person-picks-up.transcript.txt) | [json](07-FR10-FR6-wrong-person-hold-then-new-person-picks-up.json) |
| 8 | FR-2, FR-12, FR-13 | `08-FR2-FR12-FR13-does-not-deliver-to-zip-disqualified` | `disqualified` | 5/5 | ✅ pass | [open](08-FR2-FR12-FR13-does-not-deliver-to-zip-disqualified.transcript.txt) | [json](08-FR2-FR12-FR13-does-not-deliver-to-zip-disqualified.json) |
| 9 | FR-11, FR-13 | `09-FR11-FR13-voicemail-greeting-message-left` | `voicemail` | 4/4 | ✅ pass | [open](09-FR11-FR13-voicemail-greeting-message-left.transcript.txt) | [json](09-FR11-FR13-voicemail-greeting-message-left.json) |
| 10 | FR-18, FR-17 | `10-FR18-FR17-off-topic-four-times-then-hang-up` | `failed` | 6/6 | ✅ pass | [open](10-FR18-FR17-off-topic-four-times-then-hang-up.transcript.txt) | [json](10-FR18-FR17-off-topic-four-times-then-hang-up.json) |
| 11 | FR-10b, FR-13 | `11-FR10b-FR13-wrong-number-apologise-and-hang-up` | `wrong_number` | 4/4 | ✅ pass | [open](11-FR10b-FR13-wrong-number-apologise-and-hang-up.transcript.txt) | [json](11-FR10b-FR13-wrong-number-apologise-and-hang-up.json) |
| 12 | FR-19, FR-14 | `12-FR19-FR14-garbled-word-is-asked-again-not-recorded-as-no` | `partial` | 5/5 | ✅ pass | [open](12-FR19-FR14-garbled-word-is-asked-again-not-recorded-as-no.transcript.txt) | [json](12-FR19-FR14-garbled-word-is-asked-again-not-recorded-as-no.json) |
| 13 | FR-3, FR-13 | `13-FR3-FR13-not-taking-medicare-disqualified` | `disqualified` | 5/5 | ✅ pass | [open](13-FR3-FR13-not-taking-medicare-disqualified.transcript.txt) | [json](13-FR3-FR13-not-taking-medicare-disqualified.json) |

**13/13 scripted scenarios passed; the real-audio call completed with all four answers.**

`partial` / `failed` outcomes in B are expected where a script stops right after the behaviour under test — the outcome honestly says how much had been learned by then.

## What these can't prove

A human talking over the agent mid-sentence, a real 8 kHz phone line garbling a code, real office noise. Those are in the manual recordings: [`../recordings/`](../recordings/) — see the main [README](../README.md#testing--what-was-tested-how-and-where-the-evidence-is).

Requirement numbers (FR-x) refer to the table in the main [README → Requirements](../README.md#requirements-it-was-built-against).
