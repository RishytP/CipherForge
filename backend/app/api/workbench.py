"""Developer workbench endpoints.

These endpoints power the CipherForge Workbench UI. They are additive: the core
``/api/v1/analyze``, ``/anonymize`` and ``/process`` contracts are untouched.

Safety posture
--------------
* The evaluation endpoint calls the harness **in-process** via a normal Python
  import. It never shells out and never accepts a command, path or argument from
  the request body. There is no command execution surface here.
* The feedback endpoints write only to a local JSON queue. They never modify the
  detection engine, its thresholds, or the gold dataset.
* Every endpoint is disabled when ``CIPHERFORGE_WORKBENCH_ENABLED`` is false.
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, HTTPException

from app.config import get_settings
from app.detection.analyzer import PiiAnalyzer
from app.feedback import store as feedback_store
from app.models.workbench_schemas import (
    ArchitectureLayer,
    CategoryMetrics,
    DatasetExample,
    DatasetResponse,
    EvaluationResponse,
    FeedbackAcceptRequest,
    FeedbackAcceptResponse,
    FeedbackCreateRequest,
    FeedbackEntry,
    FeedbackListResponse,
    GoldSpan,
    SpanNote,
    SystemInfoResponse,
    TypeMetrics,
)

logger = logging.getLogger("cipherforge.workbench")

router = APIRouter(prefix="/api/v1/workbench", tags=["workbench"])

_analyzer = PiiAnalyzer()
_settings = get_settings()

# The evaluation endpoint runs the analyzer over the whole dataset, which takes
# a few seconds. Cache the result for this many seconds so repeated dashboard
# loads are cheap. The dataset is static, so this is safe.
_EVAL_CACHE_TTL = 300.0
_eval_cache: dict = {"at": 0.0, "payload": None}


def _eval_cached() -> EvaluationResponse:
    now = time.monotonic()
    if _eval_cache["payload"] is not None and (now - _eval_cache["at"]) < _EVAL_CACHE_TTL:
        return _eval_cache["payload"]
    payload = _run_evaluation()
    _eval_cache["at"] = now
    _eval_cache["payload"] = payload
    return payload

CAVEAT = (
    "These metrics come from the bundled synthetic dataset, which was written "
    "alongside the recognisers by the same author. They are a regression guard "
    "and an internal-consistency check, NOT a claim of real-world accuracy. "
    "See docs/LIMITATIONS.md."
)

LIMITATIONS = [
    "Coreference across sentences is unreliable (e.g. 'Rahul Sharma and Rahul are the same person').",
    "Lowercase names with no cue word and no spaCy tag are missed.",
    "Ambiguous lone capitalised tokens are demoted to review rather than decided.",
    "Only English is supported; code-mixed / transliterated text is largely unsupported.",
    "Vague locative references with no number or cue ('the flat above the bakery') are missed.",
    "Organisation detection is effectively untested (very few gold spans).",
    "Confidence scores rank detections but are not calibrated probabilities.",
]

PRIVACY_NOTES = [
    "Detection runs entirely in-process. No text is sent to any external model.",
    "Raw input is never logged; only a length descriptor is emitted on error paths.",
    "The original-to-placeholder mapping is request-scoped and never returned to the browser.",
    "Feedback stores the sanitised snippet and offsets by default, not raw text.",
    "Feedback never changes detection behaviour or thresholds automatically.",
]

LAYERS = [
    ArchitectureLayer(
        name="Layer A - Structured patterns",
        summary="Regex and checksum recognisers with variations in spacing and punctuation.",
        implementation="app/detection/patterns.py",
    ),
    ArchitectureLayer(
        name="Layer B - NLP entity recognition",
        summary="spaCy statistical NER through a curated Presidio registry.",
        implementation="app/detection/nlp.py",
    ),
    ArchitectureLayer(
        name="Layer C - Contextual analysis",
        summary="Cue lexicons, self-reference detection, spaCy mis-tag correction, framing.",
        implementation="app/detection/contextual.py",
    ),
    ArchitectureLayer(
        name="Layer D - Confidence & ambiguity",
        summary="Scoring, discrete levels incl. uncertain, overlap resolution, review flags.",
        implementation="app/detection/confidence.py",
    ),
]


def _require_enabled() -> None:
    if not get_settings().workbench_enabled:
        raise HTTPException(status_code=404, detail="workbench is disabled")


# --------------------------------------------------------------------------
# System / overview
# --------------------------------------------------------------------------


@router.get("/system", response_model=SystemInfoResponse)
def system_info() -> SystemInfoResponse:
    """Engine status, architecture summary, dataset counts and caveats."""
    _require_enabled()
    from tests.evaluation import load_dataset

    try:
        dataset = load_dataset()
    except Exception:  # pragma: no cover - defensive
        dataset = {"examples": [], "version": None}

    examples = dataset.get("examples", [])
    gold = sum(len(e.get("entities", [])) for e in examples)

    return SystemInfoResponse(
        app_name=_settings.app_name,
        engine_version=_settings.engine_version,
        dataset_version=str(dataset.get("version")) if dataset.get("version") else None,
        dataset_examples=len(examples),
        dataset_gold_spans=gold,
        spacy_model=_settings.spacy_model,
        thresholds={
            "high": _settings.high_threshold,
            "medium": _settings.medium_threshold,
            "low": _settings.low_threshold,
            "default_drop_below": _settings.default_score_threshold,
        },
        supported_languages=list(_settings.supported_languages),
        layers=LAYERS,
        limitations=LIMITATIONS,
        privacy_notes=PRIVACY_NOTES,
        workbench_enabled=_settings.workbench_enabled,
    )


# --------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------


@router.get("/evaluation", response_model=EvaluationResponse)
def evaluation(refresh: bool = False) -> EvaluationResponse:
    """Run the existing evaluation harness over the bundled dataset.

    Calls ``tests.evaluation.evaluate`` directly. No shell, no arbitrary input.
    Results are cached briefly; pass ``refresh=true`` to force a re-run.
    """
    _require_enabled()
    if refresh:
        _eval_cache["at"] = 0.0
        _eval_cache["payload"] = None
    return _eval_cached()


def _run_evaluation() -> EvaluationResponse:
    from tests.evaluation import evaluate, load_dataset

    dataset = load_dataset()
    report = evaluate(_analyzer, dataset)

    examples = dataset.get("examples", [])
    gold = sum(len(e.get("entities", [])) for e in examples)

    return EvaluationResponse(
        overall=report.overall.as_dict(),
        per_type=[
            TypeMetrics(
                entity_type=etype,
                **{k: m.as_dict()[k] for k in ("precision", "recall", "f1", "tp", "fp", "fn")},
            )
            for etype, m in sorted(report.per_type.items())
        ],
        per_category=[
            CategoryMetrics(
                category=cat,
                **{k: m.as_dict()[k] for k in ("precision", "recall", "f1", "tp", "fp", "fn")},
            )
            for cat, m in sorted(report.per_category.items())
        ],
        false_positives=[
            SpanNote(
                example_id=f["id"], entity_type=f["type"], text=f["text"], context=f["context"]
            )
            for f in report.false_positives
        ],
        false_negatives=[
            SpanNote(
                example_id=f["id"], entity_type=f["type"], text=f["text"], context=f["context"]
            )
            for f in report.failures
        ],
        dataset_examples=len(examples),
        dataset_gold_spans=gold,
        dataset_version=str(dataset.get("version")) if dataset.get("version") else None,
        caveat=CAVEAT,
    )


# --------------------------------------------------------------------------
# Dataset (Test Lab)
# --------------------------------------------------------------------------


@router.get("/dataset", response_model=DatasetResponse)
def dataset() -> DatasetResponse:
    """Return the synthetic evaluation dataset for the Test Lab."""
    _require_enabled()
    from tests.evaluation import load_dataset

    data = load_dataset()
    examples = []
    categories: set[str] = set()
    for e in data.get("examples", []):
        categories.update(e.get("categories", []))
        # ``expect_uncertain`` is usually a list of types but is a bare bool in
        # some examples; normalise so the schema stays a list everywhere.
        raw_uncertain = e.get("expect_uncertain", [])
        if isinstance(raw_uncertain, list):
            expect_uncertain = raw_uncertain
        elif raw_uncertain:
            expect_uncertain = ["*"]
        else:
            expect_uncertain = []

        examples.append(
            DatasetExample(
                id=e["id"],
                text=e["text"],
                categories=e.get("categories", []),
                notes=e.get("notes"),
                entities=[GoldSpan(**g) for g in e.get("entities", [])],
                expect_uncertain=expect_uncertain,
                expect_none=bool(e.get("expect_none", False)),
            )
        )

    return DatasetResponse(
        version=str(data["version"]) if data.get("version") else None,
        description=data.get("description"),
        count=len(examples),
        categories=sorted(categories),
        examples=examples,
    )


# --------------------------------------------------------------------------
# Feedback
# --------------------------------------------------------------------------


def _to_entry(record: dict) -> FeedbackEntry:
    return FeedbackEntry(
        id=record["id"],
        created_at=record["created_at"],
        status=record.get("status", "pending"),
        kind=record["kind"],
        raw_text_stored=record.get("raw_text_stored", False),
        text=record.get("text"),
        sanitized_text=record.get("sanitized_text"),
        entity_type=record.get("entity_type"),
        expected_type=record.get("expected_type"),
        start=record.get("start"),
        end=record.get("end"),
        mismatch_type=record.get("mismatch_type", "unspecified"),
        comment=record.get("comment"),
        score=record.get("score"),
        detection_method=record.get("detection_method"),
        confidence_level=record.get("confidence_level"),
    )


@router.get("/feedback", response_model=FeedbackListResponse)
def list_feedback() -> FeedbackListResponse:
    """List pending feedback entries and existing feedback dataset versions."""
    _require_enabled()
    return FeedbackListResponse(
        pending=[_to_entry(r) for r in feedback_store.list_pending()],
        datasets=feedback_store.list_datasets(),
        raw_text_storage_enabled=_settings.feedback_store_raw_text,
    )


@router.post("/feedback", response_model=FeedbackEntry)
def create_feedback(request: FeedbackCreateRequest) -> FeedbackEntry:
    """Queue a feedback entry for human review.

    Never changes detection behaviour. Raw text is retained only when
    ``CIPHERFORGE_FEEDBACK_STORE_RAW_TEXT`` is enabled.
    """
    _require_enabled()
    store_raw = _settings.feedback_store_raw_text

    record = {
        "kind": request.kind,
        "raw_text_stored": store_raw,
        # Privacy: default to the sanitised snippet; store raw text only if the
        # operator has explicitly opted in.
        "text": request.text if store_raw else None,
        "sanitized_text": request.sanitized_text,
        "entity_type": request.entity_type,
        "expected_type": request.expected_type,
        "start": request.start,
        "end": request.end,
        "mismatch_type": request.mismatch_type,
        "comment": request.comment,
        "score": request.score,
        "detection_method": request.detection_method,
        "confidence_level": request.confidence_level,
    }

    if request.kind == "missed" and request.text and not store_raw:
        # Keep a short sanitised context window so the reviewer can still judge
        # the miss without us storing the whole raw message.
        start = request.start or 0
        end = request.end or (start + 40)
        record["sanitized_text"] = request.text[max(0, start - 20) : end + 20]

    created = feedback_store.add_pending(record)
    return _to_entry(created)


@router.delete("/feedback/{entry_id}")
def delete_feedback(entry_id: str) -> dict:
    """Discard a pending (unreviewed) feedback entry."""
    _require_enabled()
    deleted = feedback_store.delete_pending(entry_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="feedback entry not found")
    return {"deleted": entry_id}


@router.post("/feedback/accept", response_model=FeedbackAcceptResponse)
def accept_feedback(request: FeedbackAcceptRequest) -> FeedbackAcceptResponse:
    """Move reviewed entries into a new versioned feedback dataset.

    This writes an evaluation dataset only. It does not touch the gold dataset
    or the detection engine.
    """
    _require_enabled()
    result = feedback_store.accept_pending(request.entry_ids)
    return FeedbackAcceptResponse(
        accepted=result["accepted"],
        version=result["version"],
        remaining=result["remaining"],
        note=(
            "Written to a versioned feedback dataset for later human merge. "
            "Detection behaviour and thresholds are unchanged."
        ),
    )
