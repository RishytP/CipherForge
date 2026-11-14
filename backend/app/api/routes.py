"""HTTP routes for CipherForge.

Endpoints
---------
* ``POST /api/v1/analyze``   -- detection only
* ``POST /api/v1/anonymize`` -- detection + sanitisation
* ``POST /api/v1/process``   -- full pipeline with a structured explanation

Privacy notes
-------------
* Raw text is never logged. Only a length descriptor is emitted, and only on
  error paths, using :func:`app.security.validation.redact_for_log`.
* The original-to-placeholder mapping produced by the anonymiser is never
  included in any response.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.anonymization.engine import AnonymizationEngine
from app.config import get_settings
from app.detection.analyzer import PiiAnalyzer
from app.models.schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    AnonymizeRequest,
    AnonymizeResponse,
    EntityType,
    PipelineStep,
    ProcessRequest,
    ProcessResponse,
)
from app.security.validation import InputValidationError, redact_for_log, validate_text

logger = logging.getLogger("cipherforge.api")

router = APIRouter(prefix="/api/v1", tags=["cipherforge"])

_analyzer = PiiAnalyzer()
_anonymizer = AnonymizationEngine()
_settings = get_settings()


def _validated_text(raw: str) -> str:
    try:
        return validate_text(raw)
    except InputValidationError as exc:
        logger.warning("rejected input: %s", redact_for_log(raw))
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _entity_filter(entities) -> list[str] | None:
    if not entities:
        return None
    return [e.value if isinstance(e, EntityType) else str(e) for e in entities]


@router.post("/analyze", response_model=AnalyzeResponse)
def analyze(request: AnalyzeRequest) -> AnalyzeResponse:
    """Detect PII and return spans, positions, scores and methods."""
    text = _validated_text(request.text)
    return _analyzer.analyze(
        text=text,
        language=request.language,
        entities=request.entities,
        score_threshold=request.score_threshold,
        include_uncertain=request.include_uncertain,
    )


@router.post("/anonymize", response_model=AnonymizeResponse)
def anonymize(request: AnonymizeRequest) -> AnonymizeResponse:
    """Detect PII and return sanitised text plus non-reversible metadata."""
    text = _validated_text(request.text)
    analysis = _analyzer.analyze(
        text=text,
        language=request.language,
        entities=request.entities,
        score_threshold=request.score_threshold,
    )
    try:
        result = _anonymizer.anonymize(
            text=text,
            entities=analysis.entities,
            strategy=request.strategy,
            operator_config=request.operator_config,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return AnonymizeResponse(
        sanitized_text=result.sanitized_text,
        replacements=result.replacements,
        summary=analysis.summary,
        sensitivity_summary=analysis.sensitivity_summary,
        uncertain_count=analysis.uncertain_count,
        strategy=request.strategy,
        engine_version=_settings.engine_version,
    )


@router.post("/process", response_model=ProcessResponse)
def process(request: ProcessRequest) -> ProcessResponse:
    """Run the complete pipeline: detect -> score -> anonymise -> explain."""
    text = _validated_text(request.text)

    analysis = _analyzer.analyze(
        text=text,
        language=request.language,
        entities=request.entities,
        score_threshold=request.score_threshold,
    )

    result = _anonymizer.anonymize(
        text=text,
        entities=analysis.entities,
        strategy=request.strategy,
    )

    explanation = []
    if request.include_explanation:
        explanation = _build_explanation(text, analysis, result)

    return ProcessResponse(
        sanitized_text=result.sanitized_text,
        entities=analysis.entities,
        replacements=result.replacements,
        summary=analysis.summary,
        sensitivity_summary=analysis.sensitivity_summary,
        uncertain_count=analysis.uncertain_count,
        explanation=explanation,
        engine_version=_settings.engine_version,
    )


def _build_explanation(text, analysis, result) -> list[PipelineStep]:
    settings = get_settings()
    steps = [
        PipelineStep(
            name="validate",
            detail=f"Accepted {len(text)} characters (limit {settings.max_input_chars}).",
        ),
        PipelineStep(
            name="detect",
            detail=(
                f"Found {len(analysis.entities)} candidate entities across "
                f"pattern, NLP and contextual layers."
            ),
        ),
        PipelineStep(
            name="score",
            detail=(
                f"{len(analysis.entities) - analysis.uncertain_count} confident, "
                f"{analysis.uncertain_count} flagged for review "
                f"(high threshold {settings.high_threshold})."
            ),
        ),
        PipelineStep(
            name="classify",
            detail=(
                f"Sensitivity: {analysis.sensitivity_summary.protect} PROTECT, "
                f"{analysis.sensitivity_summary.keep} KEEP, "
                f"{analysis.sensitivity_summary.review} REVIEW. Detection and "
                "sensitivity are decided separately."
            ),
        ),
        PipelineStep(
            name="anonymize",
            detail=(
                f"Replaced {sum(1 for r in result.replacements if r.replaced_original)} "
                f"spans; kept "
                f"{sum(1 for r in result.replacements if not r.replaced_original)} "
                f"spans verbatim."
            ),
        ),
    ]
    for entity in analysis.entities:
        evidence = "; ".join(entity.contextual_evidence) or "no contextual cues"
        steps.append(
            PipelineStep(
                name=f"entity:{entity.entity_type.value}",
                detail=(
                    f"'{entity.text}' at [{entity.start}:{entity.end}] "
                    f"score={entity.score} level={entity.confidence_level.value} "
                    f"method={entity.detection_method.value} "
                    f"sensitivity={entity.sensitivity.value} "
                    f"({entity.sensitivity_reason}) -> {evidence}"
                ),
            )
        )
    return steps
