"""Anonymisation engine.

Kept strictly separate from detection: it consumes a list of already-detected
:class:`DetectedEntity` spans and produces sanitised text plus non-reversible
metadata. It never receives raw detections from the frontend and never returns
the original-to-placeholder mapping.

Guarantees
----------
* Sentence structure and punctuation are preserved: replacements are applied
  by slicing the original string at span boundaries, never by regex rewriting.
* Character positions are never corrupted: we resolve overlaps first (done
  upstream in ``detection.confidence``) and apply edits right-to-left.
* Repeated mentions of the same entity get a consistent placeholder.
* The mapping is request-scoped and discarded when the request ends.

Strategies
----------
* ``placeholder``: ``[PERSON_1]`` (default).
* ``redact``: remove the span, leaving nothing.
* ``mask``: keep the last N characters, mask the rest with ``*``.
* ``hash``: deterministic short salted hash (non-reversible, stable per request).
"""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from app.anonymization.placeholders import IdentityRegistry
from app.config import get_settings
from app.models.schemas import (
    DetectedEntity,
    EntityType,
    ReplacementMetadata,
    SensitivityDecision,
)

_SUPPORTED_STRATEGIES = {"placeholder", "redact", "mask", "hash"}


@dataclass
class AnonymizationResult:
    sanitized_text: str
    replacements: List[ReplacementMetadata]
    # Request-scoped mapping. Kept in-process only; never serialised to a
    # response and never persisted.
    mapping: Dict[str, str] = field(default_factory=dict)


class AnonymizationEngine:
    """Replaces detected spans with placeholders or other strategies."""

    def __init__(self) -> None:
        self.settings = get_settings()

    # -- public API --------------------------------------------------------

    def anonymize(
        self,
        text: str,
        entities: List[DetectedEntity],
        strategy: Optional[str] = None,
        operator_config: Optional[Dict[str, str]] = None,
        review_threshold: Optional[float] = None,
    ) -> AnonymizationResult:
        """Return sanitised text and replacement metadata.

        Spans below ``review_threshold`` are kept verbatim and reported with
        ``replaced_original=False`` so callers can see what was intentionally
        left untouched.
        """
        strategy = (strategy or self.settings.default_strategy).lower()
        if strategy not in _SUPPORTED_STRATEGIES:
            raise ValueError(
                f"unsupported strategy '{strategy}'; "
                f"expected one of {sorted(_SUPPORTED_STRATEGIES)}"
            )

        threshold = (
            review_threshold
            if review_threshold is not None
            else self.settings.default_score_threshold
        )
        operator_config = operator_config or {}

        ordered = sorted(entities, key=lambda e: (e.start, e.end))
        registry = IdentityRegistry()
        # Salt is per-request so hashes cannot be correlated across requests.
        salt = secrets.token_hex(8)

        mapping: Dict[str, str] = {}
        # Assign placeholders left-to-right so numbering follows reading order
        # ([PERSON_1] is the first mention in the text).
        plan: List[tuple] = []
        for entity in ordered:
            original = text[entity.start:entity.end]
            # The sensitivity stage decides *whether* to act; this engine only
            # decides *how*. A KEEP span is left verbatim; a REVIEW span is
            # anonymised (privacy-first) but reported as flagged for a human.
            decision = _decision_of(entity)
            below_threshold = entity.score < threshold
            keep = decision is SensitivityDecision.KEEP or below_threshold or (
                entity.needs_review and not self.settings.anonymize_uncertain
            )
            if keep:
                plan.append((entity, original, False, decision))
                continue

            entity_strategy = operator_config.get(
                entity.entity_type.value, strategy
            ).lower()
            if entity_strategy not in _SUPPORTED_STRATEGIES:
                entity_strategy = strategy

            if entity_strategy == "placeholder":
                placeholder, _ = registry.placeholder_for(
                    original, _as_entity_type(entity.entity_type)
                )
                mapping[placeholder] = original
            elif entity_strategy == "redact":
                placeholder = ""
            elif entity_strategy == "mask":
                placeholder = self._mask(original)
            else:  # hash
                placeholder = self._hash(original, salt)
            plan.append((entity, placeholder, True, decision))

        replacements: List[ReplacementMetadata] = []
        # Build output right-to-left so earlier offsets stay valid.
        cursor = len(text)
        pieces: List[str] = []
        for entity, placeholder, replaced, decision in reversed(plan):
            pieces.append(text[entity.end:cursor])
            cursor = entity.start
            pieces.append(placeholder)
            replacements.append(
                _meta(entity, placeholder=placeholder, replaced=replaced, decision=decision)
            )

        pieces.append(text[:cursor])
        sanitized = "".join(reversed(pieces))
        replacements.reverse()

        return AnonymizationResult(
            sanitized_text=sanitized,
            replacements=replacements,
            mapping=mapping,
        )

    # -- strategies --------------------------------------------------------

    def _mask(self, value: str) -> str:
        keep = self.settings.mask_keep_last
        char = self.settings.mask_char
        if len(value) <= keep:
            return char * len(value)
        return char * (len(value) - keep) + value[-keep:]

    @staticmethod
    def _hash(value: str, salt: str) -> str:
        digest = hashlib.sha256((salt + value).encode("utf-8")).hexdigest()[:10]
        return f"[HASH_{digest}]"


def _as_entity_type(value) -> EntityType:
    return value if isinstance(value, EntityType) else EntityType(value)


def _decision_of(entity: DetectedEntity) -> SensitivityDecision:
    """Read the sensitivity decision, tolerating raw values and legacy callers."""
    value = getattr(entity, "sensitivity", None)
    if value is None:
        # Legacy entity without a sensitivity stage: protect (previous behaviour).
        return SensitivityDecision.PROTECT
    return value if isinstance(value, SensitivityDecision) else SensitivityDecision(value)


def _meta(
    entity: DetectedEntity, placeholder: str, replaced: bool, decision: SensitivityDecision
) -> ReplacementMetadata:
    if replaced:
        action = "anonymized"
    elif decision is SensitivityDecision.REVIEW:
        action = "flagged"
    else:
        action = "retained"
    return ReplacementMetadata(
        entity_type=_as_entity_type(entity.entity_type),
        placeholder=placeholder,
        start=entity.start,
        end=entity.end,
        score=entity.score,
        detection_method=entity.detection_method,
        replaced_original=replaced,
        sensitivity=decision,
        sensitivity_reason=getattr(entity, "sensitivity_reason", "unspecified"),
        sensitivity_rationale=getattr(entity, "sensitivity_rationale", ""),
        action=action,
    )


__all__ = ["AnonymizationEngine", "AnonymizationResult"]
