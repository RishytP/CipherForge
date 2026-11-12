"""CipherForge FastAPI application entrypoint.

Run locally:

    cd backend
    uvicorn app.main:app --reload --port 8000

The app performs no outbound network calls and persists nothing by default.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.config import get_settings

settings = get_settings()

# The console is served from the same origin as the API, so no CORS is needed
# for normal use. CORS is still enabled for the local Next.js dev server and
# the work-host ports, for anyone running a separate frontend during dev.
FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("cipherforge")

app = FastAPI(
    title=settings.app_name,
    version=settings.engine_version,
    description=(
        "Context-aware PII detection and anonymisation gateway. "
        "This service does not guarantee complete anonymisation."
    ),
)

# The frontend runs on a different origin during development. In production
# this should be restricted to known origins via configuration.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:12000",
        "http://127.0.0.1:12000",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

app.include_router(router)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Never leak internals (or input) in error responses."""
    logger.exception("unhandled error on %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "internal processing error"},
    )


@app.get("/health", tags=["ops"])
def health() -> dict:
    return {"status": "ok", "version": settings.engine_version}


@app.get("/", tags=["ops"], include_in_schema=False)
def root():
    """Serve the test console, or fall back to an API description."""
    index = FRONTEND_DIR / "index.html"
    if index.is_file():
        return FileResponse(index)
    return {
        "name": settings.app_name,
        "version": settings.engine_version,
        "endpoints": ["/api/v1/analyze", "/api/v1/anonymize", "/api/v1/process"],
        "disclaimer": (
            "CipherForge reduces exposure of detected sensitive data but does "
            "not guarantee complete anonymisation."
        ),
    }


# Static assets for the console (styles.css, app.js). Mounted last so it can
# never shadow an API route.
if FRONTEND_DIR.is_dir():
    app.mount("/app", StaticFiles(directory=str(FRONTEND_DIR)), name="app")
