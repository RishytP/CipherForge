"""Schemas for the developer workbench endpoints.

These are additive to the core detection/anonymisation contract in
``app.models.schemas``. They expose (a) read-only system and evaluation
information and (b) the feedback review workflow.

Nothing here exposes the original-to-placeholder mapping.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

# --------------------------------------------------------------------------
# System / overview
# --------------------------------------------------------------------------


class ArchitectureLayer(BaseModel):
    name: str
    summary: str
    implementation: str


class SystemInfoResponse(BaseModel):
    app_name: str
    engine_version: str
    dataset_version: Optional[str] = None
    dataset_examples: int = 0
    dataset_gold_spans: int = 0
    spacy_model: str
    thresholds: dict
    supported_languages: List[str]
    layers: List[ArchitectureLayer]
    limitations: List[str]
    privacy_notes: List[str]
    workbench_enabled: bool = True


# --------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------


class TypeMetrics(BaseModel):
    entity_type: str
    precision: float
    recall: float
    f1: float
    tp: int
    fp: int
    fn: int


class CategoryMetrics(BaseModel):
    category: str
    precision: float
    recall: float
    f1: float
    tp: int
    fp: int
    fn: int


class SpanNote(BaseModel):
    example_id: str
    entity_type: str
    text: str
    context: str
    needs_review: bool = False


class EvaluationResponse(BaseModel):
    overall: dict
    per_type: List[TypeMetrics]
    per_category: List[CategoryMetrics]
    false_positives: List[SpanNote]
    false_negatives: List[SpanNote]
    dataset_examples: int
    dataset_gold_spans: int
    dataset_version: Optional[str] = None
    caveat: str


# --------------------------------------------------------------------------
# Dataset (Test Lab)
# --------------------------------------------------------------------------


class GoldSpan(BaseModel):
    entity_type: str
    start: int
    end: int
    text: str


class DatasetExample(BaseModel):
    id: str
    text: str
    categories: List[str]
    notes: Optional[str] = None
    entities: List[GoldSpan]
    expect_uncertain: List[str] = Field(default_factory=list)
    expect_none: bool = False


class DatasetResponse(BaseModel):
    version: Optional[str] = None
    description: Optional[str] = None
    count: int
    categories: List[str]
    examples: List[DatasetExample]


# --------------------------------------------------------------------------
# Feedback
# --------------------------------------------------------------------------


class FeedbackCreateRequest(BaseModel):
    """Create a feedback entry for human review.

    ``kind``:
      * ``correct``   -- a detection the reviewer confirms as right.
      * ``incorrect`` -- a detection the reviewer believes is wrong.
      * ``missed``    -- sensitive text the engine did not detect.

    ``mismatch_type`` distinguishes a wrong *classification* (wrong entity type)
    from a wrong *boundary* (right type, wrong span), when applicable.
    """

    kind: str = Field(..., description="correct | incorrect | missed")
    text: Optional[str] = Field(
        None,
        max_length=20_000,
        description="The original message. Only retained when raw-text storage is enabled.",
    )
    sanitized_text: Optional[str] = Field(
        None,
        max_length=20_000,
        description="Sanitised version of the message (stored by default instead of raw text).",
    )
    entity_type: Optional[str] = None
    expected_type: Optional[str] = Field(
        None, description="For incorrect detections: the type the reviewer thinks is right."
    )
    start: Optional[int] = Field(None, ge=0)
    end: Optional[int] = Field(None, ge=0)
    mismatch_type: str = Field("unspecified")
    score: Optional[float] = Field(None, ge=0.0, le=1.0)
    detection_method: Optional[str] = None
    confidence_level: Optional[str] = None
    comment: Optional[str] = Field(None, max_length=2_000)

    @field_validator("kind")
    @classmethod
    def _known_kind(cls, v: str) -> str:
        from app.feedback.store import FEEDBACK_KINDS

        if v not in FEEDBACK_KINDS:
            raise ValueError(f"kind must be one of {FEEDBACK_KINDS}")
        return v

    @field_validator("mismatch_type")
    @classmethod
    def _known_mismatch(cls, v: str) -> str:
        from app.feedback.store import MISMATCH_TYPES

        if v not in MISMATCH_TYPES:
            raise ValueError(f"mismatch_type must be one of {MISMATCH_TYPES}")
        return v

    @field_validator("end")
    @classmethod
    def _end_after_start(cls, v: Optional[int], info) -> Optional[int]:  # noqa: ANN001
        start = info.data.get("start")
        if v is not None and start is not None and v <= start:
            raise ValueError("end must be greater than start")
        return v


class FeedbackEntry(BaseModel):
    id: str
    created_at: str
    status: str
    kind: str
    raw_text_stored: bool = False
    text: Optional[str] = None
    sanitized_text: Optional[str] = None
    entity_type: Optional[str] = None
    expected_type: Optional[str] = None
    start: Optional[int] = None
    end: Optional[int] = None
    mismatch_type: str = "unspecified"
    comment: Optional[str] = None
    score: Optional[float] = None
    detection_method: Optional[str] = None
    confidence_level: Optional[str] = None


class FeedbackListResponse(BaseModel):
    pending: List[FeedbackEntry]
    datasets: List[dict]
    raw_text_storage_enabled: bool


class FeedbackAcceptRequest(BaseModel):
    entry_ids: Optional[List[str]] = Field(
        None, description="Accept only these ids. None means accept all pending."
    )


class FeedbackAcceptResponse(BaseModel):
    accepted: int
    version: Optional[int] = None
    remaining: int
    note: str
