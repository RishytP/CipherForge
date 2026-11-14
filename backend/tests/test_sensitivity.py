"""Sensitivity-classification regression tests.

These pin the behaviour of the new classification stage: the Microsoft
salary-package bug, the required regression examples, credential handling,
configurable policy, and the measured sensitivity metrics.

Nothing here mocks the engine: every test runs the real analyzer, anonymiser and
sensitivity classifier.
"""

from __future__ import annotations

import pytest

from app.anonymization.engine import AnonymizationEngine
from app.detection.analyzer import PiiAnalyzer
from app.detection.sensitivity import SensitivityClassifier
from app.models.schemas import EntityType, SensitivityDecision
from tests.sensitivity_eval import evaluate_sensitivity, load_sensitivity_dataset


def _sensitivity(analyzer, text, span_text):
    """Return the sensitivity decision for the entity whose text is ``span_text``."""
    for e in analyzer.analyze(text).entities:
        if e.text == span_text:
            return e.sensitivity
    raise AssertionError(f"{span_text!r} was not detected in {text!r}")


# --------------------------------------------------------------------------
# The original bug
# --------------------------------------------------------------------------


def test_microsoft_salary_question_not_anonymised(analyzer, anonymizer):
    """The headline regression: a public company in a salary question stays."""
    text = "How do I get a 1 crore package at Microsoft?"
    result = anonymizer.anonymize(text, analyzer.analyze(text).entities)
    assert result.sanitized_text == text, "Microsoft must not be anonymised"
    assert "Microsoft" in result.sanitized_text


@pytest.mark.parametrize(
    "text,keep",
    [
        ("I want to work at Google.", "Google"),
        ("Microsoft announced a new product.", "Microsoft"),
        ("My friend studies at IIT Delhi.", "IIT Delhi"),
    ],
)
def test_public_entities_are_kept(analyzer, anonymizer, text, keep):
    result = anonymizer.anonymize(text, analyzer.analyze(text).entities)
    assert keep in result.sanitized_text
    assert result.sanitized_text == text


# --------------------------------------------------------------------------
# Required regression examples: expected decision per span
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,span,expected",
    [
        ("How do I get a 1 crore package at Microsoft?", "Microsoft", SensitivityDecision.KEEP),
        ("I want to work at Google.", "Google", SensitivityDecision.KEEP),
        ("Microsoft announced a new product.", "Microsoft", SensitivityDecision.KEEP),
        ("My friend studies at IIT Delhi.", "IIT Delhi", SensitivityDecision.KEEP),
        ("I live in Delhi.", "Delhi", SensitivityDecision.PROTECT),
        ("My email is rahul@example.com.", "rahul@example.com", SensitivityDecision.PROTECT),
        ("My password is Microsoft123.", "Microsoft123", SensitivityDecision.PROTECT),
        ("Rahul Sharma works at Microsoft.", "Rahul Sharma", SensitivityDecision.PROTECT),
        ("Rahul Sharma works at Microsoft.", "Microsoft", SensitivityDecision.KEEP),
        ("Send my personal records to Microsoft.", "Microsoft", SensitivityDecision.PROTECT),
    ],
)
def test_required_example_decisions(analyzer, text, span, expected):
    assert _sensitivity(analyzer, text, span) is expected


def test_undisclosed_client_has_nothing_to_protect(analyzer, anonymizer):
    text = "I work for an undisclosed client."
    result = anonymizer.anonymize(text, analyzer.analyze(text).entities)
    assert result.sanitized_text == text


def test_sensitive_entities_still_protected(analyzer, anonymizer):
    """The fix must not weaken protection of genuinely sensitive data."""
    text = "My email is rahul@example.com and my password is Microsoft123."
    result = anonymizer.anonymize(text, analyzer.analyze(text).entities)
    assert "rahul@example.com" not in result.sanitized_text
    assert "Microsoft123" not in result.sanitized_text
    assert "[EMAIL_1]" in result.sanitized_text
    assert "[CREDENTIAL_1]" in result.sanitized_text


# --------------------------------------------------------------------------
# Credentials
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,secret",
    [
        ("My password is Microsoft123.", "Microsoft123"),
        ("my api key: sk-abcd1234efgh", "sk-abcd1234efgh"),
        ("the password is hunter2", "hunter2"),
    ],
)
def test_credential_value_detected(analyzer, text, secret):
    creds = [e for e in analyzer.analyze(text).entities if e.entity_type is EntityType.CREDENTIAL]
    assert creds, f"no credential detected in {text!r}"
    assert creds[0].text == secret
    assert creds[0].sensitivity is SensitivityDecision.PROTECT


def test_credential_word_is_not_the_entity(analyzer):
    """Only the secret is the entity, not the label 'password'."""
    entities = analyzer.analyze("My password is Microsoft123.").entities
    assert all(e.text != "password" for e in entities)


# --------------------------------------------------------------------------
# Contextual sensitivity (informal, lowercase, framing)
# --------------------------------------------------------------------------


def test_personal_location_protected(analyzer):
    assert _sensitivity(analyzer, "I live in Delhi.", "Delhi") is SensitivityDecision.PROTECT
    assert _sensitivity(analyzer, "i live in pune and work at wipro", "pune") is SensitivityDecision.PROTECT


def test_third_party_place_kept(analyzer):
    assert (
        _sensitivity(analyzer, "we visited Goa last summer", "Goa")
        is SensitivityDecision.KEEP
    )


def test_personal_transfer_frame_protects_recipient(analyzer):
    text = "Send my personal records to Microsoft."
    assert _sensitivity(analyzer, text, "Microsoft") is SensitivityDecision.PROTECT


def test_fiction_person_is_review_not_protect(analyzer):
    assert (
        _sensitivity(analyzer, "Rahul is a character in this story.", "Rahul")
        is SensitivityDecision.REVIEW
    )


def test_bare_place_is_review(analyzer):
    assert (
        _sensitivity(analyzer, "The meeting is in Delhi tomorrow.", "Delhi")
        is SensitivityDecision.REVIEW
    )


def test_organization_is_never_personal_data_by_default(analyzer):
    for text, span in [
        ("I want to work at Google.", "Google"),
        ("Microsoft announced a new product.", "Microsoft"),
        ("Acme Corp won the contract last year.", "Acme Corp"),
    ]:
        for e in analyzer.analyze(text).entities:
            if e.text == span:
                assert e.is_personal_data is False, (text, span)


# --------------------------------------------------------------------------
# Configurable policy
# --------------------------------------------------------------------------


def test_protect_organizations_policy_opt_in(monkeypatch):
    monkeypatch.setenv("CIPHERFORGE_PROTECT_ORGANIZATIONS", "true")
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        classifier = SensitivityClassifier()
        result = classifier.classify(
            "I want to work at Google.", EntityType.ORGANIZATION, 18, 24, 0.6
        )
        assert result.decision is SensitivityDecision.PROTECT
        assert result.reason == "org_confidentiality_policy"
    finally:
        get_settings.cache_clear()


def test_protect_locations_policy_opt_in(monkeypatch):
    monkeypatch.setenv("CIPHERFORGE_PROTECT_LOCATIONS", "true")
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        classifier = SensitivityClassifier()
        # "The meeting is in Delhi tomorrow." -> Delhi at 19:24
        result = classifier.classify(
            "The meeting is in Delhi tomorrow.", EntityType.LOCATION, 19, 24, 1.0
        )
        assert result.decision is SensitivityDecision.PROTECT
    finally:
        get_settings.cache_clear()


# --------------------------------------------------------------------------
# Anonymiser honours the decision
# --------------------------------------------------------------------------


def test_keep_span_reported_as_retained(analyzer, anonymizer):
    text = "I want to work at Google."
    result = anonymizer.anonymize(text, analyzer.analyze(text).entities)
    kept = [r for r in result.replacements if r.action == "retained"]
    assert kept, "expected a retained span"
    assert all(r.replaced_original is False for r in kept)
    assert kept[0].sensitivity is SensitivityDecision.KEEP


def test_review_span_anonymised_but_flagged(analyzer, anonymizer):
    text = "Rahul is a character in this story."
    result = anonymizer.anonymize(text, analyzer.analyze(text).entities)
    flagged = [r for r in result.replacements if r.sensitivity is SensitivityDecision.REVIEW]
    assert flagged, "expected a REVIEW span"
    # Privacy-first: a REVIEW span is still anonymised, but marked as flagged.
    assert flagged[0].replaced_original is True
    assert flagged[0].action == "anonymized"


def test_legacy_entity_without_sensitivity_still_protected():
    """A DetectedEntity built by hand (no sensitivity set) defaults to PROTECT."""
    from app.models.schemas import ConfidenceLevel, DetectedEntity, DetectionMethod

    text = "Email rahul@example.com"
    ent = DetectedEntity(
        entity_type=EntityType.EMAIL_ADDRESS,
        start=6,
        end=23,
        text="rahul@example.com",
        score=0.95,
        confidence_level=ConfidenceLevel.HIGH,
        detection_method=DetectionMethod.PATTERN,
    )
    result = AnonymizationEngine().anonymize(text, [ent])
    assert "rahul@example.com" not in result.sanitized_text


# --------------------------------------------------------------------------
# Dataset integrity and metrics
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def sensitivity_dataset():
    return load_sensitivity_dataset()


def test_sensitivity_dataset_offsets_are_exact(sensitivity_dataset):
    for example in sensitivity_dataset["examples"]:
        text = example["text"]
        for span in example["entities"]:
            start, end = span["start"], span["end"]
            assert text[start:end] == span["text"], example["id"]
            assert span["expected_sensitivity"] in {"PROTECT", "KEEP", "REVIEW"}


def test_sensitivity_dataset_covers_all_classes(sensitivity_dataset):
    classes = {
        span["expected_sensitivity"]
        for e in sensitivity_dataset["examples"]
        for span in e["entities"]
    }
    assert classes == {"PROTECT", "KEEP", "REVIEW"}


def test_sensitivity_dataset_includes_required_examples(sensitivity_dataset):
    ids = {e["id"] for e in sensitivity_dataset["examples"]}
    assert {"rq01", "rq02", "rq03", "rq04", "rq05", "rq06", "rq07", "rq08", "rq09", "rq10"} <= ids


def test_sensitivity_metrics_meet_baseline(analyzer, anonymizer, sensitivity_dataset):
    report = evaluate_sensitivity(analyzer, anonymizer, sensitivity_dataset)
    # Floors sit well below the measured values so legitimate refactors pass
    # while a real collapse fails.
    assert report.micro.precision >= 0.85, report.as_dict()
    assert report.micro.recall >= 0.85, report.as_dict()
    assert report.macro_f1 >= 0.80, report.as_dict()


def test_false_positive_anonymisation_rate_is_zero(analyzer, anonymizer, sensitivity_dataset):
    """No KEEP span in the regression set may be anonymised."""
    report = evaluate_sensitivity(analyzer, anonymizer, sensitivity_dataset)
    assert report.keep_spans > 0
    assert report.keep_anonymized == 0, report.as_dict()


def test_false_negative_protection_rate_is_zero(analyzer, anonymizer, sensitivity_dataset):
    """No PROTECT span in the regression set may be left visible."""
    report = evaluate_sensitivity(analyzer, anonymizer, sensitivity_dataset)
    assert report.protect_spans > 0
    assert report.protect_leaked == 0, report.as_dict()


def test_review_is_used_but_rare(analyzer, anonymizer, sensitivity_dataset):
    report = evaluate_sensitivity(analyzer, anonymizer, sensitivity_dataset)
    # REVIEW must be exercised (not dead code) but must not dominate.
    assert report.review_entities >= 1
    assert report.review_frequency <= 0.5, report.as_dict()
