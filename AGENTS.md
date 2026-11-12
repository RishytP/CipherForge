# AGENTS.md — CipherForge

Repository-specific notes for agents working on this codebase.

## What this project is

CipherForge is a context-aware PII detection and anonymisation backend. It is a
privacy layer intended to sit between users and AI systems. The hard problem is
handling **informal, irregular human text**, not clean formatted text.

## Layout

- `backend/app/` — the FastAPI service.
  - `detection/` — Layers A–D. `analyzer.py` orchestrates; the other four files
    each own one layer. Keep it that way: no layer logic in the orchestrator.
  - `anonymization/` — strictly separate from detection. Consumes detected
    spans only; never sees raw text with no detections.
  - `models/schemas.py` — the public API contract.
  - `security/validation.py` — input validation and log redaction.
- `backend/tests/` — pytest suite plus the evaluation harness and dataset
  builder.
- `docs/LIMITATIONS.md` — the honest account of what fails. Update it whenever
  you change detection or anonymisation behaviour.
- `frontend/` — dependency-free test console (`index.html`, `styles.css`,
  `app.js`). Served by the backend at `/`, assets at `/app`. No build step, no
  npm. It must only render sanitised text and must never read the mapping.

## Commands

```bash
cd backend
source ../.venv/bin/activate
python -m pytest -q                     # 65 tests
python -m tests.evaluation              # P/R/F1 report
python tests/fixtures/build_dataset.py  # regenerate dataset (rewrites JSON)
uvicorn app.main:app --reload --port 8000
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
- **Honest metrics.** The synthetic dataset was written by the same author as
  the recognisers. Never present its numbers as real-world accuracy. See
  `docs/LIMITATIONS.md`.
- Adding a recogniser means adding a test **and** at least one dataset example.

## Environment notes

- Python 3.13 in the local venv (`.venv` at repo root); requires 3.11+.
- spaCy model `en_core_web_sm` must be present. If a model download fails, note
  it in the README and fall back to the gazetteer/cue patterns rather than
  claiming the NLP layer works.
- `presidio-anonymizer` is pinned in `requirements.txt` but the custom
  anonymiser in `anonymization/` is what the API actually uses.
