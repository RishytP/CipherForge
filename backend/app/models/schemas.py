"""Pydantic schemas for the CipherForge detection and anonymisation API.

These models are the public contract. They deliberately never contain the
original-to-placeholder mapping: that mapping is request-scoped and stays
inside the backend (see ``app.anonymization.engine``).
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

# --------------------------------------------------------------------------
# Enumerations
# --------------------------------------------------------------------------


class EntityType(str, Enum):
    """Canonical entity labels used across detection and anonymisation."""

    PERSON = "PERSON"
    EMAIL_ADDRESS = "EMAIL_ADDRESS"
    PHONE_NUMBER = "PHONE_NUMBER"
    LOCATION = "LOCATION"
    ORGANIZATION = "ORGANIZATION"
    ADDRESS = "ADDRESS"
    POSTAL_CODE = "POSTAL_CODE"
    IP_ADDRESS = "IP_ADDRESS"
    URL = "URL"
    DATE_TIME = "DATE_TIME"
    CREDIT_CARD = "CREDIT_CARD"
    IN_AADHAAR = "IN_AADHAAR"
    IN_PAN = "IN_PAN"
    ID_NUMBER = "ID_NUMBER"
    CREDENTIAL = "CREDENTIAL"
    OTHER = "OTHER"


class DetectionMethod(str, Enum):
    """Which layer produced (or dominated) a detection."""

    PATTERN = "pattern"
    NLP = "nlp"
    CONTEXT = "context"
    COMBINED = "combined"


class ConfidenceLevel(str, Enum):
    """Bucketed confidence, including an explicit ``uncertain`` state.

    The engine must never silently present an uncertain detection as certain.
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNCERTAIN = "uncertain"


class SensitivityDecision(str, Enum):
    """Whether a detected entity is sensitive in its current context.

    Recognition ("is this an entity?") and sensitivity ("should it be
    protected?") are separate questions; this enum is the answer to the second.
    """

    PROTECT = "PROTECT"
    KEEP = "KEEP"
    REVIEW = "REVIEW"


# --------------------------------------------------------------------------
# Detection primitives
# --------------------------------------------------------------------------


class DetectedEntity(BaseModel):
    """A single detected span in the source text."""

    entity_type: EntityType
    start: int = Field(..., ge=0, description="Inclusive start offset in the original text.")
    end: int = Field(..., ge=0, description="Exclusive end offset in the original text.")
    text: str = Field(..., description="Exact substring of the original text.")
    score: float = Field(..., ge=0.0, le=1.0, description="Calibrated confidence in [0, 1].")
    confidence_level: ConfidenceLevel
    detection_method: DetectionMethod
    recognizer: Optional[str] = Field(
        None, description="Name of the recogniser or layer that produced the span."
    )
    contextual_evidence: List[str] = Field(
        default_factory=list,
        description="Human-readable reasons that raised or lowered confidence.",
    )
    needs_review: bool = Field(
        False, description="True when the engine is not confident enough to auto-act."
    )
    sensitivity: SensitivityDecision = Field(
        SensitivityDecision.PROTECT,
        description=(
            "Contextual sensitivity decision: PROTECT (anonymise), KEEP (leave "
            "unchanged) or REVIEW (ambiguous). Defaults to PROTECT for backward "
            "compatibility with callers that build entities directly."
        ),
    )
    sensitivity_reason: str = Field(
        "unspecified",
        description="Short machine-readable reason code for the sensitivity decision.",
    )
    sensitivity_rationale: str = Field(
        "",
        description="Human-readable explanation of why the span was protected, kept or flagged.",
    )
    is_personal_data: bool = Field(
        False,
        description="True when the span identifies a person or holds their personal data.",
    )

    @field_validator("end")
    @classmethod
    def _end_after_start(cls, v: int, info) -> int:  # noqa: ANN001
        start = info.data.get("start")
        if start is not None and v <= start:
            raise ValueError("end must be greater than start")
        return v


class EntitySummary(BaseModel):
    """Count of detections per entity type, for compact reporting."""

    entity_type: EntityType
    count: int
    max_score: float


class SensitivitySummary(BaseModel):
    """Counts of detections per sensitivity decision."""

    protect: int = 0
    keep: int = 0
    review: int = 0


# --------------------------------------------------------------------------
# Requests
# --------------------------------------------------------------------------


class _BaseTextRequest(BaseModel):
    text: str = Field(..., description="Raw user text to process.")

    @field_validator("text")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if v is None or not v.strip():
            raise ValueError("text must not be empty or whitespace only")
        return v


class AnalyzeRequest(_BaseTextRequest):
    """Request body for ``POST /api/v1/analyze``."""

    language: str = Field("en", max_length=8)
    entities: Optional[List[EntityType]] = Field(
        None, description="Restrict detection to these entity types."
    )
    score_threshold: Optional[float] = Field(
        None, ge=0.0, le=1.0, description="Override the default minimum score."
    )
    include_uncertain: bool = Field(
        True, description="Include detections below the confident threshold, flagged for review."
    )


class AnonymizeRequest(_BaseTextRequest):
    """Request body for ``POST /api/v1/anonymize``."""

    language: str = Field("en", max_length=8)
    entities: Optional[List[EntityType]] = None
    score_threshold: Optional[float] = Field(None, ge=0.0, le=1.0)
    strategy: str = Field(
        "placeholder",
        description="Replacement strategy: placeholder | redact | mask | hash.",
    )
    operator_config: Optional[Dict[str, str]] = Field(
        None, description="Optional per-entity operator overrides, e.g. {'PERSON': 'redact'}."
    )


class ProcessRequest(_BaseTextRequest):
    """Request body for ``POST /api/v1/process`` (full pipeline)."""

    language: str = Field("en", max_length=8)
    entities: Optional[List[EntityType]] = None
    score_threshold: Optional[float] = Field(None, ge=0.0, le=1.0)
    strategy: str = Field("placeholder")
    include_explanation: bool = Field(True)


# --------------------------------------------------------------------------
# Responses
# --------------------------------------------------------------------------


class AnalyzeResponse(BaseModel):
    text_length: int
    entities: List[DetectedEntity]
    summary: List[EntitySummary]
    sensitivity_summary: SensitivitySummary = Field(
        default_factory=SensitivitySummary,
        description="Counts of PROTECT / KEEP / REVIEW decisions across detected entities.",
    )
    uncertain_count: int = Field(
        0, description="Detections the engine flagged as needing review."
    )
    engine_version: str


class ReplacementMetadata(BaseModel):
    """Public, non-reversible metadata about one replacement."""

    entity_type: EntityType
    placeholder: str
    start: int
    end: int
    score: float
    detection_method: DetectionMethod
    replaced_original: bool = Field(
        True, description="False when the span was kept verbatim (e.g. below threshold)."
    )
    sensitivity: SensitivityDecision = Field(
        SensitivityDecision.PROTECT,
        description="Sensitivity decision that governed this span.",
    )
    sensitivity_reason: str = Field("unspecified")
    sensitivity_rationale: str = Field("")
    action: str = Field(
        "anonymized",
        description="Final action taken: anonymized | retained | flagged.",
    )


class AnonymizeResponse(BaseModel):
    sanitized_text: str
    replacements: List[ReplacementMetadata]
    summary: List[EntitySummary]
    sensitivity_summary: SensitivitySummary = Field(default_factory=SensitivitySummary)
    uncertain_count: int = 0
    strategy: str
    engine_version: str


class PipelineStep(BaseModel):
    name: str
    detail: str


class ProcessResponse(BaseModel):
    sanitized_text: str
    entities: List[DetectedEntity]
    replacements: List[ReplacementMetadata]
    summary: List[EntitySummary]
    sensitivity_summary: SensitivitySummary = Field(default_factory=SensitivitySummary)
    uncertain_count: int = 0
    explanation: List[PipelineStep] = Field(default_factory=list)
    disclaimer: str = Field(
        "CipherForge reduces exposure of detected sensitive data. It does not "
        "guarantee complete anonymisation; review uncertain detections before use."
    )
    engine_version: str
