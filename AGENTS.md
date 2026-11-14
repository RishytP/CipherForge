# AGENTS.md — CipherForge

Repository-specific notes for agents working on this codebase.

## What this project is

CipherForge is a context-aware PII detection and anonymisation backend. It is a
privacy layer intended to sit between users and AI systems. The hard problem is
handling **informal, irregular human text**, not clean formatted text.

## Layout

- `backend/app/` — the FastAPI service.
  - `detection/` — Layers A–E. `analyzer.py` orchestrates; the other five
    files each own one layer. Keep it that way: no layer logic in the
    orchestrator. Layers A–D answer *what is this span*; `sensitivity.py`
    (Layer E) answers the separate question *is it sensitive here*, returning
    PROTECT / KEEP / REVIEW. Never merge the two concerns.
  - `anonymization/` — strictly separate from detection. Consumes detected
    spans only; never sees raw text with no detections.
  - `api/routes.py` — the core public endpoints (`/analyze`, `/anonymize`,
    `/process`). Do not change their contract.
  - `api/workbench.py` — developer-workbench endpoints (`/api/v1/workbench/*`).
    Additive only.
  - `feedback/store.py` — JSON feedback queue + versioned datasets. Feedback is
    queued for human review; it must **never** alter detection automatically.
  - `models/schemas.py` — the public API contract.
  - `models/workbench_schemas.py` — workbench + feedback contract.
  - `security/validation.py` — input validation and log redaction.
- `backend/data/feedback/` — runtime feedback queue (git-ignored). Plain JSON,
  no database.
- `backend/tests/` — pytest suite plus the evaluation harness and dataset
  builder.
- `docs/LIMITATIONS.md` — the honest account of what fails. Update it whenever
  you change detection or anonymisation behaviour.
- `frontend/workbench/` — the CipherForge **Workbench**, served by the backend
  at `/`, assets at `/app/workbench`. Dependency-free ES modules under
  `js/` (one module per view). No build step, no npm. It must only render
  sanitised text and must never read the mapping.
- `frontend/` (root files) — the original single-page console, now served at
  `/legacy`, assets at `/app`. Kept for reference; new work goes in the
  workbench.

## Commands

```bash
cd backend
source ../.venv/bin/activate
python -m pytest -q                     # 123 tests
python -m tests.evaluation              # entity-detection P/R/F1 report
python -m tests.sensitivity_eval        # sensitivity P/R/F1 + FP/FN report
python tests/fixtures/build_dataset.py  # regenerate dataset (rewrites JSON)
python tests/fixtures/build_sensitivity_dataset.py  # regenerate sensitivity set
uvicorn app.main:app --reload --port 8000
# then open http://127.0.0.1:8000/  (workbench) or /legacy (old console)
```

## Conventions and invariants

- **Never log raw user text.** Use `redact_for_log`; log only lengths.
- **Never return or persist the placeholder→original mapping.** It is
  request-scoped and stays in memory.
- **Never send text to an external service.** This milestone is offline by
  design.
- **Do not hard-code credentials.** Configuration is `CIPHERFORGE_*` env vars.
- **Keep thresholds in `config.py`.** Detection logic must read them, not embed
  magic numbers.
- **Anonymiser edits spans by slicing.** Never rewrite text with regex; that
  corrupts offsets and punctuation.
- **Placeholders are numbered in reading order** (left-to-right), even though
  the engine builds output right-to-left.
- **Detection ≠ sensitivity.** A confident detection is not automatically
  sensitive. Organisations are kept visible by default; only an explicit
  confidentiality policy (`CIPHERFORGE_PROTECT_ORGANIZATIONS`) or a
  personal-data frame protects them. Public places are kept unless tied to the
  speaker (`I live in …`). Do not "fix" a kept entity by forcing PROTECT.
- **Sensitivity decisions must stay explainable.** Every decision carries a
  machine-readable `reason` and a human `rationale`. Add new cue rules as
  explicit, named branches — never as an opaque score.
- **Honest metrics.** The synthetic dataset was written by the same author as
  the recognisers. Never present its numbers as real-world accuracy. See
  `docs/LIMITATIONS.md`.
- Adding a recogniser means adding a test **and** at least one dataset example.
- **Workbench feedback never changes detection.** It is queued for review and,
  when accepted, written to a versioned evaluation dataset. Do not wire feedback
  into thresholds or recognisers automatically.
- **Feedback is privacy-first.** Raw text is stored only when
  `CIPHERFORGE_FEEDBACK_STORE_RAW_TEXT=true`; default is the sanitised snippet.
- **No command execution from requests.** The workbench evaluation endpoint
  calls `tests.evaluation` by Python import; never shell out or accept paths.

## Environment notes

- Python 3.13 in the local venv (`.venv` at repo root); requires 3.11+.
- spaCy model `en_core_web_sm` must be present. If a model download fails, note
  it in the README and fall back to the gazetteer/cue patterns rather than
  claiming the NLP layer works.
- `presidio-anonymizer` is pinned in `requirements.txt` but the custom
  anonymiser in `anonymization/` is what the API actually uses.
