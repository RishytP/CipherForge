"""Layer D: confidence scoring, ambiguity handling and overlap resolution.

Responsibilities
----------------
* Map a numeric score to a discrete :class:`ConfidenceLevel`, including an
  explicit ``uncertain`` bucket.
* Decide whether a detection should be flagged ``needs_review``.
* Resolve overlapping detections deterministically so the anonymiser receives
  a clean, non-overlapping set of spans.

The engine never silently upgrades an uncertain detection to certain: a
``needs_review`` flag is carried all the way to the API response.
"""

from __future__ import annotations

from typing import List

from app.config import get_settings
from app.models.schemas import ConfidenceLevel, DetectedEntity

# When two spans overlap, the more specific / higher priority entity wins.
_TYPE_PRIORITY = {
    "EMAIL_ADDRESS": 100,
    "CREDIT_CARD": 95,
    "IN_AADHAAR": 90,
    "IN_PAN": 90,
    "PHONE_NUMBER": 85,
    "URL": 82,
    "IP_ADDRESS": 80,
    "POSTAL_CODE": 60,
    "ADDRESS": 55,
    "PERSON": 50,
    "LOCATION": 40,
    "ORGANIZATION": 35,
    "DATE_TIME": 20,
    "OTHER": 10,
}


def score_to_level(score: float) -> ConfidenceLevel:
    """Bucket a numeric score, honouring the configurable thresholds."""
    s = get_settings()
    if score >= s.high_threshold:
        return ConfidenceLevel.HIGH
    if score >= s.medium_threshold:
        return ConfidenceLevel.MEDIUM
    if score >= s.low_threshold:
        return ConfidenceLevel.LOW
    return ConfidenceLevel.UNCERTAIN


def should_review(score: float, force_review: bool = False) -> bool:
    """Return True when a detection is not confident enough to auto-act on."""
    s = get_settings()
    return force_review or score < s.high_threshold


def finalize_entity(
    entity_type: str,
    start: int,
    end: int,
    text: str,
    score: float,
    detection_method: str,
    recognizer: str,
    evidence: List[str],
    force_review: bool = False,
) -> DetectedEntity:
    """Build a :class:`DetectedEntity`, clamping the score to [0, 1]."""
    clamped = max(0.0, min(1.0, score))
    return DetectedEntity(
        entity_type=entity_type,
        start=start,
        end=end,
        text=text,
        score=round(clamped, 4),
        confidence_level=score_to_level(clamped),
        detection_method=detection_method,
        recognizer=recognizer,
        contextual_evidence=evidence,
        needs_review=should_review(clamped, force_review),
    )


def _type_key(entity: DetectedEntity) -> str:
    value = entity.entity_type
    return value.value if hasattr(value, "value") else str(value)


def _priority(entity: DetectedEntity) -> tuple:
    """Sort key: higher priority type, then longer span, then higher score."""
    return (
        _TYPE_PRIORITY.get(_type_key(entity), 0),
        entity.end - entity.start,
        entity.score,
    )


def resolve_overlaps(entities: List[DetectedEntity]) -> List[DetectedEntity]:
    """Return a non-overlapping, position-sorted subset of ``entities``.

    Overlaps are resolved greedily: candidates are ordered by (type priority,
    span length, score) descending and a candidate is kept only if it does not
    overlap an already-kept span. This is deterministic and keeps the most
    specific match (e.g. an email over a bare URL inside it).
    """
    ordered = sorted(entities, key=_priority, reverse=True)
    kept: List[DetectedEntity] = []
    for candidate in ordered:
        if any(_overlaps(candidate, k) for k in kept):
            continue
        kept.append(candidate)
    kept.sort(key=lambda e: (e.start, e.end))
    return kept


def _overlaps(a: DetectedEntity, b: DetectedEntity) -> bool:
    return a.start < b.end and b.start < a.end


__all__ = [
    "score_to_level",
    "should_review",
    "finalize_entity",
    "resolve_overlaps",
]
