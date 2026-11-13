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
| Synthetic dataset (≥ 50 examples) | ✅ 73 examples, 115 gold spans |
| Precision / recall / F1 harness | ✅ measured (see below) |
| pytest suite | ✅ 80 tests passing |
| Test console (frontend) | ✅ working, served at `/legacy` |
| Developer Workbench (frontend) | ✅ working, served at `/` |
| Next.js frontend | ⏳ not adopted (see decision below) |
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

### Workbench endpoints (`/api/v1/workbench/...`)

Powers the developer UI. Read-only except for the feedback queue. Disable the
whole surface with `CIPHERFORGE_WORKBENCH_ENABLED=false`.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/system` | Engine version, thresholds, dataset counts, layers, limitations. |
| `GET` | `/evaluation` | Runs the evaluation harness in-process (cached; `?refresh=true` to re-run). |
| `GET` | `/dataset` | The synthetic dataset with gold spans. |
| `GET` | `/feedback` | Pending feedback entries + written dataset versions. |
| `POST` | `/feedback` | Queue a feedback entry for review. |
| `DELETE` | `/feedback/{id}` | Discard a pending entry. |
| `POST` | `/feedback/accept` | Write reviewed entries to a new dataset version. |

The evaluation endpoint calls `tests.evaluation` **by direct Python import**. It
never shells out and never accepts a command, path, or argument from the request
body, so it is not a command-execution surface.

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

### Developer Workbench (frontend)

The primary UI is now the **CipherForge Workbench**, an interactive tool for
developers and reviewers, served by the backend at the root URL:

**<http://127.0.0.1:8000/>**

It is a development and evaluation interface, **not** a production chatbot. It
has six views:

| View | What it does |
| --- | --- |
| **Overview** | Engine status, dataset counts, latest evaluation, known limitations. |
| **Secure Chat** | Type a message exactly as written; inspect detections and sanitised output. |
| **Test Lab** | Run dataset examples through the pipeline and compare predictions to gold labels (a live pass/partial/fail verdict per example). |
| **Evaluation** | Precision / recall / F1 overall, by entity type and by category, with the false positives and false negatives listed. |
| **Feedback** | Review queued corrections; accept them into a versioned dataset or discard them. |
| **About** | Versions, thresholds, architecture, and what the tool is not. |

In **Secure Chat**, typing a message and pressing *Process message* sends **the
raw text exactly as typed** to `POST /api/v1/process`. The view then shows the
original text with each detected span highlighted and clickable, alongside the
sanitised output with `[TYPE_n]` placeholders. Clicking an entity in the
inspector highlights its exact characters in the original. Every entity card
shows the backend's own confidence score, discrete level, detection method, and
contextual evidence — none of which the UI computes.

The workbench only ever renders the **sanitised** output and the metadata the
API does not consider sensitive. It never receives or reconstructs the
original-to-placeholder mapping.

#### Feedback defaults are privacy-first

Submitting feedback queues an entry for **human review**. It does **not** change
detection rules, entity types, or confidence thresholds — ever, automatically.
Accepting an entry writes it to a versioned *evaluation dataset* for a later
human merge.

By default feedback stores the **sanitised snippet and offsets**, not raw text.
Raw-text storage is opt-in via `CIPHERFORGE_FEEDBACK_STORE_RAW_TEXT=true`. The
feedback queue lives in `backend/data/feedback/` (git-ignored) as plain JSON —
no database.

#### Why not Next.js

The brief listed Next.js first for the frontend. After inspecting the existing
project, the workbench was built as dependency-free static ES modules served by
the backend, for concrete reasons:

* The existing console was already dependency-free and served same-origin; there
  was no build step, no `npm install`, and no CORS to manage.
* `npm install next` pulls roughly 330 MB into `node_modules`. For an internal
  developer tool that renders a handful of panels, that cost is not justified.
* Serving the UI from the API's own origin means the workbench can never drift
  from the API contract, and there is one process to run.

The code is organised as small ES modules under `frontend/workbench/js/`
(`api.js`, `ui.js`, `entity-inspector.js` and one module per view), so porting to
Next.js later is a mechanical move of view logic into React components. This is a
deliberate trade-off, documented rather than hidden.

### Legacy test console

The original single-page console is still available at **`/legacy`** (assets at
`/app/*`). It is kept because it is small, working, and covered by tests. New
work goes into the Workbench.

### Tests

```bash
cd backend
python -m pytest -q                    # full suite (80 tests)
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
    api/workbench.py        /api/v1/workbench/* (system, evaluation, dataset, feedback)
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
    models/workbench_schemas.py  workbench + feedback contract
    feedback/store.py       JSON feedback queue + versioned datasets
    security/validation.py  input validation + log redaction
  tests/
    test_detection.py       Layer A–D behaviour
    test_anonymization.py   placeholders, strategies, consistency
    test_api.py             endpoint contract + input validation
    test_dataset.py         dataset integrity + metric regression floors
    test_workbench.py       workbench endpoints + feedback lifecycle
    evaluation.py           P/R/F1 harness
    fixtures/
      build_dataset.py      generates the dataset
      synthetic_dataset.json
docs/
  LIMITATIONS.md            honest account of what fails and why
frontend/
  workbench/                developer workbench (served at /)
    index.html, styles.css
    js/                     api.js, ui.js, entity-inspector.js, one module per view
  index.html, styles.css, app.js   legacy console (served at /legacy)
```

---

## Privacy and security posture

* **No raw prompt logging by default.** Only a length descriptor is logged, and
  only on error paths, via `app.security.validation.redact_for_log`.
* **No outbound network calls.** This milestone never sends text to an external
  model.
* **No database, and no request persistence.** The placeholder mapping lives
  only for the duration of a request. Nothing about a request is stored — the
  only thing written to disk is the **workbench feedback queue**, and that is
  opt-in, human-reviewed, and stores the sanitised snippet and offsets rather
  than raw text by default.
* **Input validation.** Type checks, a configurable maximum length, and blank
  input rejection. Oversized or malformed input returns `422`.
* **No hard-coded credentials.** Configuration is environment-driven via
  `CIPHERFORGE_*` variables and nothing else.
* **No command-execution surface.** The workbench evaluation endpoint calls the
  harness by Python import; it accepts no path or command from the request.
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
