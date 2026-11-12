"""Dataset integrity and end-to-end evaluation regression tests.

These guard against silent dataset corruption and large regressions in the
measured metrics. The thresholds are deliberately well below the current
measured scores so that legitimate refactors do not fail the build, while a
genuine collapse in detection quality does.
"""

from __future__ import annotations

import pytest

from tests.evaluation import DEFAULT_DATASET, evaluate, load_dataset


@pytest.fixture(scope="module")
def dataset():
    return load_dataset()


def test_dataset_has_at_least_50_examples(dataset):
    assert len(dataset["examples"]) >= 50


def test_dataset_covers_required_categories(dataset):
    tags = set()
    for example in dataset["examples"]:
        tags.update(example.get("categories", []))
    required = {
        "properly_formatted",
        "missing_punctuation",
        "excessive_punctuation",
        "abbreviations",
        "informal",
        "inconsistent_spacing",
        "misspellings",
        "mixed_structures",
        "ambiguous",
        "multiple_entities",
        "coreference",
        "repeated_entities",
        "false_positive",
        "indian_names",
        "indian_phone",
    }
    missing = required - tags
    assert not missing, f"dataset missing categories: {sorted(missing)}"


def test_dataset_gold_spans_are_exact_substrings(dataset):
    for example in dataset["examples"]:
        text = example["text"]
        for span in example["entities"]:
            start, end = span["start"], span["end"]
            assert 0 <= start < end <= len(text), f"{example['id']}: bad offsets"
            assert text[start:end] == span["text"], (
                f"{example['id']}: offsets do not select '{span['text']}'"
            )


def test_dataset_has_negative_examples(dataset):
    negatives = [e for e in dataset["examples"] if e.get("expect_none") or not e["entities"]]
    assert negatives, "dataset must contain false-positive / no-entity examples"


def test_evaluation_meets_baseline(analyzer, dataset):
    report = evaluate(analyzer, dataset)
    overall = report.overall
    # Current measured values are ~0.98; these floors catch real regressions.
    assert overall.precision >= 0.80, overall.as_dict()
    assert overall.recall >= 0.80, overall.as_dict()
    assert overall.f1 >= 0.80, overall.as_dict()


def test_email_and_phone_detection_are_reliable(analyzer, dataset):
    report = evaluate(analyzer, dataset)
    for entity_type in ("EMAIL_ADDRESS", "PHONE_NUMBER"):
        metrics = report.per_type.get(entity_type)
        assert metrics is not None, f"no gold spans for {entity_type}"
        assert metrics.recall >= 0.9, f"{entity_type}: {metrics.as_dict()}"
