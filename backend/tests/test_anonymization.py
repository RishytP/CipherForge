"""Anonymisation tests: placeholders, strategies, consistency, positions."""

from __future__ import annotations

import re

import pytest

from app.anonymization.engine import AnonymizationEngine
from app.models.schemas import EntityType


def _entities(text, pairs):
    """Build DetectedEntity objects from (type, substring) pairs.

    Repeated substrings are located in order of appearance.
    """
    from app.models.schemas import ConfidenceLevel, DetectedEntity, DetectionMethod

    out = []
    cursor = 0
    for etype, sub in pairs:
        start = text.index(sub, cursor)
        cursor = start + len(sub)
        out.append(
            DetectedEntity(
                entity_type=EntityType(etype),
                start=start,
                end=start + len(sub),
                text=sub,
                score=0.95,
                confidence_level=ConfidenceLevel.HIGH,
                detection_method=DetectionMethod.PATTERN,
            )
        )
    return out


def test_placeholder_replacement_preserves_punctuation(anonymizer):
    text = "Hey, I'm Rahul Sharma. Email me at rahul@example.com."
    ents = _entities(
        text,
        [("PERSON", "Rahul Sharma"), ("EMAIL_ADDRESS", "rahul@example.com")],
    )
    res = anonymizer.anonymize(text, ents)
    assert res.sanitized_text == "Hey, I'm [PERSON_1]. Email me at [EMAIL_1]."


def test_repeated_entity_uses_same_placeholder(anonymizer):
    text = "Rahul called. Later Rahul emailed rahul@example.com."
    ents = _entities(
        text,
        [("PERSON", "Rahul"), ("PERSON", "Rahul"), ("EMAIL_ADDRESS", "rahul@example.com")],
    )
    res = anonymizer.anonymize(text, ents)
    assert res.sanitized_text.count("[PERSON_1]") == 2
    assert "[PERSON_2]" not in res.sanitized_text


def test_full_and_short_name_share_identity(anonymizer):
    text = "Rahul Sharma is here. Rahul left."
    ents = _entities(
        text, [("PERSON", "Rahul Sharma"), ("PERSON", "Rahul")]
    )
    res = anonymizer.anonymize(text, ents)
    assert res.sanitized_text.count("[PERSON_1]") == 2


def test_distinct_entities_get_distinct_placeholders(anonymizer):
    text = "Rahul and Meera met at 9876543210."
    ents = _entities(text, [("PERSON", "Rahul"), ("PERSON", "Meera")])
    res = anonymizer.anonymize(text, ents)
    assert "[PERSON_1]" in res.sanitized_text
    assert "[PERSON_2]" in res.sanitized_text


def test_anonymization_never_returns_mapping_in_metadata(anonymizer):
    text = "Email rahul@example.com"
    ents = _entities(text, [("EMAIL_ADDRESS", "rahul@example.com")])
    res = anonymizer.anonymize(text, ents)
    # Mapping exists in-process but is not part of the replacement metadata.
    for meta in res.replacements:
        assert not hasattr(meta, "original")
        assert "rahul@example.com" not in meta.model_dump_json()


def test_redact_strategy(anonymizer):
    text = "Email rahul@example.com now"
    ents = _entities(text, [("EMAIL_ADDRESS", "rahul@example.com")])
    res = anonymizer.anonymize(text, ents, strategy="redact")
    assert res.sanitized_text == "Email  now"


def test_mask_strategy_keeps_last_characters(anonymizer):
    text = "Call 9876543210"
    ents = _entities(text, [("PHONE_NUMBER", "9876543210")])
    res = anonymizer.anonymize(text, ents, strategy="mask")
    assert "3210" in res.sanitized_text
    assert "987654" not in res.sanitized_text


def test_hash_strategy_is_stable_within_a_request(anonymizer):
    # The salt is per-request, so the same value repeated in one request hashes
    # to the same token, while the output never contains the original.
    text = "Email rahul@example.com or rahul@example.com"
    ents = _entities(
        text, [("EMAIL_ADDRESS", "rahul@example.com"), ("EMAIL_ADDRESS", "rahul@example.com")]
    )
    res = anonymizer.anonymize(text, ents, strategy="hash")
    assert res.sanitized_text.count("[HASH_") == 2
    assert len(set(re.findall(r"\[HASH_[0-9a-f]+\]", res.sanitized_text))) == 1
    assert "rahul" not in res.sanitized_text


def test_hash_strategy_differs_across_requests(anonymizer):
    text = "Email rahul@example.com"
    ents = _entities(text, [("EMAIL_ADDRESS", "rahul@example.com")])
    first = anonymizer.anonymize(text, ents, strategy="hash")
    second = anonymizer.anonymize(text, ents, strategy="hash")
    # A per-request salt prevents cross-request correlation.
    assert first.sanitized_text != second.sanitized_text


def test_unsupported_strategy_raises(anonymizer):
    text = "Email rahul@example.com"
    ents = _entities(text, [("EMAIL_ADDRESS", "rahul@example.com")])
    with pytest.raises(ValueError):
        anonymizer.anonymize(text, ents, strategy="nonsense")


def test_low_confidence_kept_verbatim_and_flagged(anonymizer):
    from app.models.schemas import ConfidenceLevel, DetectionMethod, DetectedEntity

    # A span scoring below the review threshold is left verbatim and reported
    # as not replaced.
    text = "Maybe Rahul is here"
    ent = DetectedEntity(
        entity_type=EntityType.PERSON,
        start=6,
        end=11,
        text="Rahul",
        score=0.3,
        confidence_level=ConfidenceLevel.UNCERTAIN,
        detection_method=DetectionMethod.CONTEXT,
        needs_review=True,
    )
    res = anonymizer.anonymize(text, [ent], review_threshold=0.5)
    assert "Rahul" in res.sanitized_text
    assert res.replacements[0].replaced_original is False


def test_uncertain_span_anonymised_by_default(anonymizer):
    from app.models.schemas import ConfidenceLevel, DetectionMethod, DetectedEntity

    # Privacy-first default: a span flagged for review but above the review
    # threshold is still anonymised, so an uncertain detection never leaks PII.
    text = "Maybe Rahul is here"
    ent = DetectedEntity(
        entity_type=EntityType.PERSON,
        start=6,
        end=11,
        text="Rahul",
        score=0.8,
        confidence_level=ConfidenceLevel.HIGH,
        detection_method=DetectionMethod.CONTEXT,
        needs_review=True,
    )
    res = anonymizer.anonymize(text, [ent], review_threshold=0.5)
    assert "Rahul" not in res.sanitized_text


def test_end_to_end_detection_then_anonymization(analyzer, anonymizer):
    text = "Hey, I'm Rahul Sharma. Email me at rahul@example.com or call 9876543210."
    detected = analyzer.analyze(text).entities
    res = anonymizer.anonymize(text, detected)
    assert "rahul@example.com" not in res.sanitized_text
    assert "9876543210" not in res.sanitized_text
    assert "[PERSON_1]" in res.sanitized_text
    assert "Email me at" in res.sanitized_text


def test_positions_stay_consistent_after_multiple_replacements(analyzer, anonymizer):
    text = "A: rahul@example.com B: 9876543210 C: I told Meera."
    detected = analyzer.analyze(text).entities
    res = anonymizer.anonymize(text, detected)
    # Structure of the line is preserved: same labels and order.
    assert res.sanitized_text.startswith("A: [")
    assert " B: [" in res.sanitized_text
    assert " C: I told [" in res.sanitized_text
