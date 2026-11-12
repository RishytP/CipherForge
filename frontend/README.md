# Frontend (not started)

This directory is reserved for Milestone 5: replacing the previous prototype's
hard-coded JavaScript privacy logic with real calls to the CipherForge backend.

The plan is Next.js + TypeScript + Tailwind CSS, with a Secure Chat interface
that:

1. Sends raw text to `POST /api/v1/process`.
2. Renders the sanitised text, not the original.
3. Displays each detected entity with its type, confidence level and evidence.
4. Visually distinguishes detected, uncertain (`needs_review`) and kept-verbatim
   spans.

Nothing is implemented here yet. The backend endpoints it will consume are
documented in the root `README.md`.
