"""Feedback storage for the CipherForge developer workbench.

Design rules (from the workbench brief, and non-negotiable):

* Feedback is **never** applied to detection automatically. It is queued for a
  human to review before it can join an evaluation dataset.
* Thresholds never change as a side effect of a single correction.
* Raw text is stored only when ``CIPHERFORGE_FEEDBACK_STORE_RAW_TEXT`` is set.
  By default we store the sanitised snippet and character offsets instead, so
  the queue does not accumulate real personal information.
* Any unsubmitted entry can be discarded.

Storage layout (all JSON, no database):

    <feedback_dir>/
        pending.json          -- entries awaiting review
        dataset_v<N>.json     -- accepted, versioned evaluation datasets

A single-process lock serialises writes. This is a development tool, not a
multi-tenant service, so this is deliberately simple.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import get_settings

_LOCK = threading.Lock()
_REPO_ROOT = Path(__file__).resolve().parents[3]


# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------


def _feedback_dir() -> Path:
    settings = get_settings()
    path = Path(settings.feedback_dir)
    if not path.is_absolute():
        path = _REPO_ROOT / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def _pending_path() -> Path:
    return _feedback_dir() / "pending.json"


def _dataset_path(version: int) -> Path:
    return _feedback_dir() / f"dataset_v{version}.json"


def _atomic_write(path: Path, payload: Dict[str, Any]) -> None:
    """Write JSON atomically so a crash cannot corrupt the queue."""
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _read_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


# --------------------------------------------------------------------------
# Pending queue
# --------------------------------------------------------------------------

FEEDBACK_KINDS = ("correct", "incorrect", "missed")
MISMATCH_TYPES = ("classification", "boundary", "unspecified")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def list_pending() -> List[dict]:
    with _LOCK:
        data = _read_json(_pending_path(), {"version": 1, "entries": []})
    return data.get("entries", [])


def add_pending(entry: Dict[str, Any]) -> dict:
    """Queue a feedback entry for human review.

    ``entry`` must already be validated and privacy-scrubbed by the API layer.
    """
    record = {
        "id": str(uuid.uuid4()),
        "created_at": _now(),
        "status": "pending",
        **entry,
    }
    with _LOCK:
        data = _read_json(_pending_path(), {"version": 1, "entries": []})
        data.setdefault("entries", []).append(record)
        _atomic_write(_pending_path(), data)
    return record


def delete_pending(entry_id: str) -> bool:
    """Discard an unsaved feedback entry. Returns True if it existed."""
    with _LOCK:
        data = _read_json(_pending_path(), {"version": 1, "entries": []})
        entries = data.get("entries", [])
        remaining = [e for e in entries if e.get("id") != entry_id]
        if len(remaining) == len(entries):
            return False
        data["entries"] = remaining
        _atomic_write(_pending_path(), data)
    return True


# --------------------------------------------------------------------------
# Versioned datasets
# --------------------------------------------------------------------------


def _next_dataset_version() -> int:
    existing = []
    for path in _feedback_dir().glob("dataset_v*.json"):
        try:
            existing.append(int(path.stem.split("_v")[1]))
        except (IndexError, ValueError):
            continue
    return (max(existing) + 1) if existing else 1


def list_datasets() -> List[dict]:
    out = []
    for path in sorted(_feedback_dir().glob("dataset_v*.json")):
        data = _read_json(path, {})
        out.append(
            {
                "version": data.get("version"),
                "created_at": data.get("created_at"),
                "entries": len(data.get("entries", [])),
                "file": path.name,
            }
        )
    return out


def accept_pending(entry_ids: Optional[List[str]] = None) -> dict:
    """Move reviewed entries out of the queue into a new dataset version.

    If ``entry_ids`` is None, every pending entry is accepted. Returns a summary
    of what was written. Detection behaviour is unchanged by design.
    """
    with _LOCK:
        data = _read_json(_pending_path(), {"version": 1, "entries": []})
        entries = data.get("entries", [])
        selected = [
            e for e in entries if entry_ids is None or e.get("id") in set(entry_ids)
        ]
        if not selected:
            return {"accepted": 0, "version": None, "remaining": len(entries)}

        version = _next_dataset_version()
        for entry in selected:
            entry["status"] = "accepted"
            entry["reviewed_at"] = _now()

        payload = {
            "version": version,
            "created_at": _now(),
            "source": "workbench-feedback",
            "note": (
                "Human-reviewed feedback. Not merged into the synthetic gold set "
                "or used to tune detection automatically."
            ),
            "entries": selected,
        }
        _atomic_write(_dataset_path(version), payload)

        accepted_ids = {e["id"] for e in selected}
        data["entries"] = [e for e in entries if e.get("id") not in accepted_ids]
        _atomic_write(_pending_path(), data)

    return {"accepted": len(selected), "version": version, "remaining": len(data["entries"])}
