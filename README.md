# CipherForge

A context-aware privacy layer that sits between users and AI systems. It
detects personally identifiable information (PII) in free-form text, scores how
confident it is, and replaces what it finds with consistent placeholders —
while keeping the surrounding sentence intact.

The core engineering problem this project attacks is the one previous
prototypes failed at: **real people do not write clean text**. They type
`"hey so im rahul... u can reach me at rahul@gmail.com ig"`, not
`"My name is Rahul. My email is rahul@gmail.com."` CipherForge is built around
that reality.

> CipherForge **reduces exposure** of detected sensitive data. It does **not**
> guarantee complete anonymisation. Every response carries this disclaimer and
> uncertain detections are surfaced, not hidden.

---

## Status of this milestone

This is the first functional backend (Milestones 1–4 of the brief). It is
implemented, running, and tested.

| Deliverable | State |
| --- | --- |
| FastAPI backend, 3 endpoints | ✅ working |
| Multi-layer PII detection (A–D) | ✅ working |
| Custom recognisers | ✅ 9 recognisers |
| Anonymisation with consistent placeholders | ✅ working |
| Synthetic dataset (≥ 50 examples) | ✅ 73 examples, 114 gold spans |
| Precision / recall / F1 harness | ✅ measured (see below) |
| pytest suite | ✅ 69 tests passing |
| Test console (frontend) | ✅ working, served at `/` |
| Next.js frontend | ⏳ deferred (static console covers testing) |
| External model routing | ⏳ out of scope for this milestone |

**Measured on the synthetic dataset** (`python -m tests.evaluation`):

```
OVERALL  P=0.983  R=0.983  F1=0.983  (tp=113 fp=2 fn=2 exact=0.982)
```

These are real, reproducible numbers — but read them honestly: the dataset and
the recognisers were written by the same author, so this measures internal
consistency, **not** real-world accuracy. See
[`docs/LIMITATIONS.md`](docs/LIMITATIONS.md) for exactly where the engine is
still weak.

---

## How detection works

Detection is a pipeline of four independent layers. Each layer can be tested
and replaced on its own; nothing is buried in one giant function.

```
                       ┌──────────────────────────────────────────┐
   raw text  ─────────▶│ Layer A  structured patterns (Presidio)   │
                       │          emails, phones, PINs, IPs, PAN…   │
                       ├──────────────────────────────────────────┤
                       │ Layer B  NLP entities (spaCy NER)          │
                       │          people, orgs, places              │
                       ├──────────────────────────────────────────┤
                       │ Layer C  contextual analysis               │
                       │          cue words, self-reference,        │
                       │          re-typing, confidence shifts      │
                       ├──────────────────────────────────────────┤
                       │ Layer D  confidence + overlap resolution   │
                       └───────────────────┬──────────────────────┘
                                           ▼
                          entities: type, offsets, score,
                          method, contextual evidence
```

### Layer A — structured patterns (`app/detection/patterns.py`)

Custom Presidio `PatternRecognizer`s for email (including `rahul (at) gmail
(dot) com` obfuscation and spaced dots), phone numbers (Indian + international,
validated with `phonenumbers`), Indian PIN codes, IPv4/IPv6, Indian PAN,
Aadhaar (Verhoeff checksum validated), URLs, address fragments, and a
location gazetteer for Indian cities spaCy's small model misses.

### Layer B — NLP (`app/detection/nlp.py`)

spaCy `en_core_web_sm` for person/organisation/location entities, run through
Presidio's `AnalyzerEngine`. We deliberately use a **curated** recogniser
registry rather than Presidio's default one, because the defaults produced
false positives such as `UK_NHS` on Indian mobile numbers.

### Layer C — contextual analysis (`app/detection/contextual.py`)

This is the heart of the project and the part previous prototypes lacked. It
does five things:

1. **Cue-based confidence shifts.** `"my name is"` boosts a following name;
   `"not my number"` and `"a character in this story"` lower confidence.
2. **Self-reference name detection.** `"im rahul"`, `"my nme is rahul"`,
   `"this is kavya"` — lowercase, unpunctuated, misspelled. spaCy rarely tags
   these, so we anchor on the cue and validate the following token against
   common-word, title, pronoun and channel-noun blocklists.
3. **Entity re-typing.** spaCy's small model systematically confuses
   categories. `"I told Ananya"` tags *Ananya* as a place (GPE); `"OMG"` gets
   tagged as an organisation; `"PAN"` in `"my PAN is…"` becomes an organisation.
   The contextual layer corrects these using nearby cues and a gazetteer, or
   drops them as noise.
4. **Third-person / fiction framing.** `"Rahul is a character in this story"`
   still detects *Rahul*, but flags it for review instead of silently treating
   it as the speaker's identity.
5. **Non-identifying reference demotion.** `"the old school on MG Road"` has no
   house number, so a bare road name is demoted below threshold rather than
   asserted as an address.

### Layer D — confidence and ambiguity (`app/detection/confidence.py`)

Every detection carries a type, offsets, a score in `[0, 1]`, a discrete
confidence level (`high` / `medium` / `low` / `uncertain`), the method that
produced it, and human-readable evidence. Overlaps are resolved
deterministically by (type priority, span length, score). Spans the engine is
unsure about are marked `needs_review` — the engine never pretends to be
certain.

---

## Anonymisation (`app/anonymization/`)

Strictly separate from detection. It consumes already-detected spans and
produces sanitised text plus non-reversible metadata.

```
Input:   "Hey, I'm Rahul Sharma. Email me at rahul@example.com."
Output:  "Hey, I'm [PERSON_1]. Email me at [EMAIL_1]."
```

* Replacements are applied by slicing the original string at span boundaries,
  so punctuation and structure are preserved and offsets are never corrupted.
* Repeated mentions share a placeholder. `"Rahul Sharma"` and `"Rahul"` are
  treated as the same identity via a documented token-subset approximation.
* Placeholders are numbered in reading order (`[PERSON_1]` is the first mention).
* Strategies: `placeholder` (default), `redact`, `mask`, `hash`.
* The original→placeholder mapping is request-scoped, kept in memory only, and
  **never** serialised into a response.

---

## API

Base path `/api/v1`. Interactive docs at `/docs` when the server is running.

### `POST /api/v1/analyze`

Detect only. Returns spans, positions, scores, methods and evidence.

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/analyze \
  -H 'Content-Type: application/json' \
  -d '{"text":"hey so im rahul... u can reach me at rahul@gmail.com ig"}'
```

### `POST /api/v1/anonymize`

Detect + sanitise. Returns `sanitized_text`, replacement metadata and a summary.

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/anonymize \
  -H 'Content-Type: application/json' \
  -d '{"text":"my no is 98 765-43210, call after 6"}'
```

### `POST /api/v1/process`

The full pipeline: validate → detect → score → anonymise → explain. Returns the
sanitised text, the entities, and a structured step-by-step explanation.

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/process \
  -H 'Content-Type: application/json' \
  -d '{"text":"Hey, I am Meera. Reach me at meera@example.com or 9876543210."}'
```

---

## Running locally

Requires **Python 3.11+** (developed and validated on 3.13).

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m spacy download en_core_web_sm     # if not already present
uvicorn app.main:app --reload --port 8000
```

Then open <http://127.0.0.1:8000/docs>.

### Test console (frontend)

A dependency-free web console is served by the backend itself at the root URL:

**<http://127.0.0.1:8000/>**

It lets you type or pick messy sample text, choose the endpoint and strategy,
and see the sanitised output, each detection with its confidence and evidence,
and the pipeline explanation. It is served from the same origin as the API, so
there is no CORS setup, no build step and no `npm install` required.

It lives in `frontend/` as three static files (`index.html`, `styles.css`,
`app.js`) mounted by the backend at `/app`. The console only ever renders the
*sanitised* text returned by the API; it never receives or reconstructs the
original-to-placeholder mapping.

> A separate Next.js frontend remains the longer-term plan for Milestone 5. The
> static console is what makes the backend testable right now without pulling in
> a JS toolchain.

### Tests

```bash
cd backend
python -m pytest -q                    # full suite (65 tests)
python -m tests.evaluation             # precision / recall / F1 report
python tests/fixtures/build_dataset.py # regenerate the synthetic dataset
```

---

## Project layout

```
backend/
  app/
    main.py                 FastAPI app, CORS, error handling, /health
    config.py               all tunables (thresholds, limits, strategies)
    api/routes.py           /analyze, /anonymize, /process
    detection/
      analyzer.py           orchestrates layers A–D
      patterns.py           Layer A recognisers + gazetteer
      nlp.py                Layer B spaCy/Presidio engine
      contextual.py         Layer C cue lexicons + contextual scoring
      confidence.py         Layer D scoring + overlap resolution
    anonymization/
      engine.py             strategies + span-safe replacement
      placeholders.py       identity registry + placeholder generation
    models/schemas.py       Pydantic request/response contract
    security/validation.py  input validation + log redaction
  tests/
    test_detection.py       Layer A–D behaviour
    test_anonymization.py   placeholders, strategies, consistency
    test_api.py             endpoint contract + input validation
    test_dataset.py         dataset integrity + metric regression floors
    evaluation.py           P/R/F1 harness
    fixtures/
      build_dataset.py      generates the dataset
      synthetic_dataset.json
docs/
  LIMITATIONS.md            honest account of what fails and why
frontend/                   (not started — reserved for Milestone 5)
```

---

## Privacy and security posture

* **No raw prompt logging by default.** Only a length descriptor is logged, and
  only on error paths, via `app.security.validation.redact_for_log`.
* **No outbound network calls.** This milestone never sends text to an external
  model.
* **No persistence.** Nothing is written to a database; the placeholder mapping
  lives only for the duration of a request.
* **Input validation.** Type checks, a configurable maximum length, and blank
  input rejection. Oversized or malformed input returns `422`.
* **No hard-coded credentials.** Configuration is environment-driven via
  `CIPHERFORGE_*` variables and nothing else.
* **Honest error handling.** Unexpected errors return a generic 500 with no
  internals and no input echoed back.

---

## Key technical decisions

* **Curated Presidio registry, not the defaults.** Presidio's default
  recognisers produced real false positives on Indian data. Building an explicit
  registry keeps behaviour predictable and testable.
* **spaCy small model + a gazetteer.** `en_core_web_sm` is fast and adequate,
  but misses many Indian names and places. A curated Indian-location gazetteer
  and cue-based name patterns fill the gap without the cost of a large model.
* **Context is a scoring layer, not a separate detector.** Cues adjust the score
  of candidates the other layers propose. This keeps precision controllable and
  avoids a second source of hallucinated entities.
* **The anonymiser never sees raw text with no detections.** It only ever
  transforms spans the detector produced, so it cannot invent or leak anything.
* **Honest uncertainty.** A `needs_review` flag propagates end to end. The
  system is designed to say "I'm not sure" rather than guess.

See [`docs/LIMITATIONS.md`](docs/LIMITATIONS.md) for what is still weak, and
what should be built next.
