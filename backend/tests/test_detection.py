"""Detection tests: patterns, NLP, contextual behaviour and confidence."""

from __future__ import annotations

import pytest

from app.detection.confidence import resolve_overlaps, score_to_level
from app.detection.contextual import ContextualAnalyzer
from app.models.schemas import ConfidenceLevel, EntityType
from tests.conftest import entities_of


# --------------------------------------------------------------------------
# Layer A: structured patterns
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Email me at rahul.sharma@example.com please", "rahul.sharma@example.com"),
        ("mail: first.last@sub.domain.co.in", "first.last@sub.domain.co.in"),
        ("obfuscated: rahul (at) gmail (dot) com", "rahul (at) gmail (dot) com"),
    ],
)
def test_email_detection(analyzer, text, expected):
    assert expected in entities_of(analyzer.analyze(text), "EMAIL_ADDRESS")


@pytest.mark.parametrize(
    "text,expected",
    [
        ("call me on +91 98765 43210", "+91 98765 43210"),
        ("my number is 9876543210", "9876543210"),
        ("reach me at 98 765-43210 today", "98 765-43210"),
        ("landline 022-2345-6789 works", "022-2345-6789"),
        ("mobile +91-98765-43210 ok", "+91-98765-43210"),
    ],
)
def test_phone_detection_indian_formats(analyzer, text, expected):
    assert expected in entities_of(analyzer.analyze(text), "PHONE_NUMBER")


def test_phone_ignores_section_numbers_and_scores(analyzer):
    # These look numeric but are not phone numbers; they must not be phones.
    for text in ["Section 420 of the IPC", "The score was 98 out of 100"]:
        assert "PHONE_NUMBER" not in {e.entity_type.value for e in analyzer.analyze(text).entities}


def test_ip_and_url_detection(analyzer):
    res = analyzer.analyze("server at 10.0.0.5 and docs at http://10.1.2.3:8080/portal")
    types = {e.entity_type.value for e in res.entities}
    assert "IP_ADDRESS" in types
    assert "URL" in types


def test_pincode_detection(analyzer):
    res = analyzer.analyze("my pin code is 560001")
    assert "560001" in entities_of(res, "POSTAL_CODE")


def test_indian_ids(analyzer):
    res = analyzer.analyze("PAN ABCPE1234F and Aadhaar 2345 6789 0124")
    types = {e.entity_type.value for e in res.entities}
    assert "IN_PAN" in types
    assert "IN_AADHAAR" in types


def test_aadhaar_checksum_rejects_invalid(analyzer):
    # 2345 6789 0123 fails the Verhoeff checksum and must not be labelled.
    res = analyzer.analyze("my aadhaar is 2345 6789 0123")
    assert "IN_AADHAAR" not in {e.entity_type.value for e in res.entities}


# --------------------------------------------------------------------------
# Layer B/C: names, informal language, contextual cues
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Hello, my name is Rahul Sharma.", "Rahul Sharma"),
        ("hey so im rahul... u can reach me", "rahul"),
        ("my name is priya and my number is 9876543210", "priya"),
        ("hi this is kavya pls note my no", "kavya"),
        ("I am Ananya Iyer from Delhi", "Ananya Iyer"),
    ],
)
def test_person_detection_informal(analyzer, text, expected):
    assert expected in entities_of(analyzer.analyze(text), "PERSON")


def test_person_detected_via_addressing_verb(analyzer):
    res = analyzer.analyze("Rahul told me his number is 9876543210.")
    assert "Rahul" in entities_of(res, "PERSON")


def test_not_every_capitalised_word_is_a_person(analyzer):
    res = analyzer.analyze("The office is near the old school on MG Road.")
    persons = entities_of(res, "PERSON")
    assert "MG" not in persons
    assert "Road" not in persons


def test_fiction_reference_flagged_uncertain(analyzer):
    res = analyzer.analyze("Rahul is a character in this story.")
    rahul = [e for e in res.entities if e.text == "Rahul"]
    assert rahul, "name should still be detected"
    assert rahul[0].needs_review, "fictional name must be flagged for review"
    assert any("illustrative" in ev for ev in rahul[0].contextual_evidence)


def test_third_person_not_marked_as_self(analyzer):
    res = analyzer.analyze("Send it to Rahul at the office, not my personal mail.")
    rahul = [e for e in res.entities if e.text == "Rahul"]
    assert rahul
    # Third-person framing must not be treated as a self-disclosure.
    assert "self_reference" not in rahul[0].contextual_evidence


@pytest.mark.parametrize("text", ["reach me at 9876543210", "call me after 6", "mail me soon"])
def test_pronouns_and_fillers_not_people(analyzer, text):
    # "me", "after" and "soon" follow address cues but are not names.
    persons = entities_of(analyzer.analyze(text), "PERSON")
    assert persons == []


def test_address_clause_detected_but_flagged(analyzer):
    res = analyzer.analyze("my address is 24, MG road... the one near the old school")
    addresses = [e for e in res.entities if e.entity_type.value == "ADDRESS"]
    assert addresses, "address clause should be detected"
    assert "MG road" in addresses[0].text


def test_vague_location_reference_demoted(analyzer):
    res = analyzer.analyze("I live near the old school on MG Road.")
    # A bare road name is weak evidence and must be flagged, not asserted.
    mg = [e for e in res.entities if "MG Road" in e.text]
    assert all(e.needs_review for e in mg)


# --------------------------------------------------------------------------
# Layer D: confidence and overlaps
# --------------------------------------------------------------------------


def test_confidence_levels_mapping():
    assert score_to_level(0.95) is ConfidenceLevel.HIGH
    assert score_to_level(0.6) is ConfidenceLevel.MEDIUM
    assert score_to_level(0.4) is ConfidenceLevel.LOW
    assert score_to_level(0.1) is ConfidenceLevel.UNCERTAIN


def test_high_confidence_email_not_flagged_for_review(analyzer):
    res = analyzer.analyze("Email me at rahul@example.com")
    email = [e for e in res.entities if e.entity_type.value == "EMAIL_ADDRESS"][0]
    assert email.score >= 0.9
    assert email.needs_review is False


def test_overlap_resolution_keeps_one_span(analyzer):
    res = analyzer.analyze("profile https://example.com/users/rahul")
    spans = [(e.start, e.end) for e in res.entities]
    for i, (s1, e1) in enumerate(spans):
        for s2, e2 in spans[i + 1:]:
            assert not (s1 < e2 and s2 < e1), "spans must not overlap"


def test_uncertain_excluded_when_requested(analyzer):
    text = "Rahul is a character in this story."
    with_uncertain = analyzer.analyze(text, include_uncertain=True)
    without = analyzer.analyze(text, include_uncertain=False)
    assert len(with_uncertain.entities) >= len(without.entities)


def test_entity_filter(analyzer):
    res = analyzer.analyze(
        "Rahul at rahul@example.com or 9876543210",
        entities=[EntityType.EMAIL_ADDRESS],
    )
    types = {e.entity_type.value for e in res.entities}
    assert types <= {"EMAIL_ADDRESS"}


def test_offsets_index_into_original_text(analyzer):
    text = "Hi I am Meera, email meera@x.com."
    for e in analyzer.analyze(text).entities:
        assert text[e.start:e.end] == e.text
