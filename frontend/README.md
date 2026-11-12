# CipherForge test console

A dependency-free web console for exercising the CipherForge backend. It is
served by the FastAPI app itself at the root URL, so there is no build step, no
`npm install`, and no CORS configuration:

```bash
cd backend
source ../.venv/bin/activate
uvicorn app.main:app --reload --port 8000
# then open http://127.0.0.1:8000/
```

## Files

| File | Purpose |
| --- | --- |
| `index.html` | Layout: input panel, results panel, sample chips. |
| `styles.css` | Dark console theme. No framework, no external assets. |
| `app.js` | Fetch calls and rendering. Vanilla JS, no dependencies. |

They are mounted by `backend/app/main.py` at `/app`, and `/` serves
`index.html`.

## What it does

* Sends text to `/api/v1/process`, `/analyze` or `/anonymize`.
* Lets you pick a replacement strategy (`placeholder`, `redact`, `mask`, `hash`).
* Shows the sanitised text with placeholders highlighted.
* Lists every detection with its type, offsets, confidence, detection method,
  and the contextual evidence behind the score.
* Distinguishes confident detections from those flagged `needs_review`.
* Shows the pipeline explanation returned by `/process`.
* Ships eight messy sample inputs — the irregular, conversational cases the
  detection engine is actually built for.

## Privacy properties

* The console only ever renders the **sanitised** text returned by the API.
  It does not reconstruct originals from the input box.
* It never receives or accesses the backend's original-to-placeholder mapping.
  (`backend/tests/test_api.py::test_console_does_not_expose_mapping` enforces
  that the bundle never touches a `mapping` field.)
* It makes no outbound requests to any third party — only same-origin calls to
  the CipherForge API.

## Why not Next.js

The brief's Milestone 5 calls for a Next.js + TypeScript + Tailwind Secure Chat
interface. That is still the plan for the real product UI. This static console
exists so the backend is testable today without adding a JS toolchain to the
critical path. When the Next.js app is built, it should replace this directory
and consume the same endpoints.
