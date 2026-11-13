"""CipherForge FastAPI application entrypoint.

Run locally:

    cd backend
    uvicorn app.main:app --reload --port 8000

The app performs no outbound network calls and persists nothing by default.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.api.workbench import router as workbench_router
from app.config import get_settings

settings = get_settings()

# Static assets served by the backend. Two UIs live under here:
#   frontend/workbench/  -- the developer workbench (served at /)
#   frontend/            -- the original single-page console (served at /legacy)
WORKBENCH_DIR = Path(__file__).resolve().parents[2] / "frontend" / "workbench"
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
app.include_router(workbench_router)


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
    """Serve the developer workbench, falling back to a JSON description."""
    index = WORKBENCH_DIR / "index.html"
    if index.is_file():
        return FileResponse(index)
    return {
        "name": settings.app_name,
        "version": settings.engine_version,
        "ui": "not built",
        "endpoints": [
            "/api/v1/analyze",
            "/api/v1/anonymize",
            "/api/v1/process",
            "/api/v1/workbench/system",
        ],
        "disclaimer": (
            "CipherForge reduces exposure of detected sensitive data but does "
            "not guarantee complete anonymisation."
        ),
    }


@app.get("/legacy", tags=["ops"], include_in_schema=False)
def legacy_console():
    """Serve the original single-page console, kept for convenience."""
    index = FRONTEND_DIR / "index.html"
    if index.is_file():
        return FileResponse(index)
    raise HTTPException(status_code=404, detail="legacy console not found")


# Static assets. Registered last so they can never shadow an API route.
# The workbench lives at /app/workbench/*, the legacy console at /app/*.
if WORKBENCH_DIR.is_dir():
    app.mount("/app/workbench", StaticFiles(directory=str(WORKBENCH_DIR)), name="workbench")
if FRONTEND_DIR.is_dir():
    app.mount("/app", StaticFiles(directory=str(FRONTEND_DIR)), name="app")
