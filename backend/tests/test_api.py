"""API tests for the FastAPI layer."""

from __future__ import annotations

import pytest


def test_health(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_analyze_returns_entities_with_positions(client):
    res = client.post(
        "/api/v1/analyze",
        json={"text": "Email me at rahul@example.com"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["entities"], "expected at least one entity"
    email = body["entities"][0]
    assert email["entity_type"] == "EMAIL_ADDRESS"
    assert email["start"] == 12
    assert email["end"] == 29
    assert 0.0 <= email["score"] <= 1.0
    assert email["detection_method"] in {"pattern", "nlp", "context", "combined"}


def test_analyze_never_returns_mapping(client):
    res = client.post("/api/v1/analyze", json={"text": "Email rahul@example.com"})
    assert "mapping" not in res.json()


def test_anonymize_replaces_and_hides_mapping(client):
    res = client.post(
        "/api/v1/anonymize",
        json={"text": "Hey, I'm Rahul Sharma. Email me at rahul@example.com."},
    )
    assert res.status_code == 200
    body = res.json()
    assert "rahul@example.com" not in body["sanitized_text"]
    assert "[PERSON_1]" in body["sanitized_text"]
    assert "[EMAIL_1]" in body["sanitized_text"]
    assert "mapping" not in body
    # Replacement metadata must not leak originals.
    for repl in body["replacements"]:
        assert "rahul@example.com" not in repl["placeholder"]


def test_anonymize_strategy_redact(client):
    res = client.post(
        "/api/v1/anonymize",
        json={"text": "Email rahul@example.com now", "strategy": "redact"},
    )
    assert res.status_code == 200
    assert "rahul@example.com" not in res.json()["sanitized_text"]


def test_anonymize_invalid_strategy_returns_422(client):
    res = client.post(
        "/api/v1/anonymize",
        json={"text": "Email rahul@example.com", "strategy": "nope"},
    )
    assert res.status_code == 422


def test_process_returns_explanation(client):
    res = client.post(
        "/api/v1/process",
        json={"text": "I'm Meera, reach me at meera@example.com or 9876543210."},
    )
    assert res.status_code == 200
    body = res.json()
    assert "sanitized_text" in body
    assert body["explanation"], "pipeline explanation should not be empty"
    names = {step["name"] for step in body["explanation"]}
    assert {"validate", "detect", "score", "anonymize"} <= names


def test_empty_text_rejected(client):
    res = client.post("/api/v1/analyze", json={"text": "   "})
    assert res.status_code == 422


def test_oversized_text_rejected(client):
    from app.config import get_settings

    limit = get_settings().max_input_chars
    res = client.post("/api/v1/analyze", json={"text": "a" * (limit + 1)})
    assert res.status_code == 422


def test_wrong_type_rejected(client):
    res = client.post("/api/v1/analyze", json={"text": 12345})
    assert res.status_code == 422


def test_entity_filter_in_request(client):
    res = client.post(
        "/api/v1/analyze",
        json={"text": "Rahul at rahul@example.com", "entities": ["EMAIL_ADDRESS"]},
    )
    assert res.status_code == 200
    types = {e["entity_type"] for e in res.json()["entities"]}
    assert types <= {"EMAIL_ADDRESS"}


def test_analyze_uncertain_flag_present(client):
    res = client.post(
        "/api/v1/analyze",
        json={"text": "Rahul is a character in this story."},
    )
    body = res.json()
    assert body["uncertain_count"] >= 1
    assert any(e["needs_review"] for e in body["entities"])
