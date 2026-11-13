"""Tests for the developer workbench endpoints.

These exercise the real feedback store against a temporary directory and the
real workbench router through the FastAPI test client. No mocks.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """A test client whose feedback store points at a temp directory.

    The settings object is cached, so we clear the cache and re-import the
    store's resolved path by re-reading settings each call.
    """
    monkeypatch.setenv("CIPHERFORGE_FEEDBACK_DIR", str(tmp_path))
    get_settings.cache_clear()
    yield TestClient(app)
    get_settings.cache_clear()


def test_system_endpoint_reports_real_counts(client):
    r = client.get("/api/v1/workbench/system")
    assert r.status_code == 200
    body = r.json()
    assert body["app_name"] == "CipherForge"
    assert len(body["layers"]) == 4
    # Counts must match the actual dataset, not a hard-coded value.
    assert body["dataset_examples"] >= 50
    assert body["dataset_gold_spans"] >= body["dataset_examples"]


def test_evaluation_endpoint_matches_harness(client):
    r = client.get("/api/v1/workbench/evaluation")
    assert r.status_code == 200
    body = r.json()
    o = body["overall"]
    # Internally consistent: precision = tp / (tp + fp), etc.
    assert o["tp"] + o["fn"] == body["dataset_gold_spans"]
    assert abs(o["precision"] - o["tp"] / (o["tp"] + o["fp"])) < 1e-3
    assert body["per_type"]
    assert body["caveat"]


def test_dataset_endpoint_returns_all_examples(client):
    r = client.get("/api/v1/workbench/dataset")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] >= 50
    # Offsets must be valid within each example's text.
    for ex in body["examples"]:
        for g in ex["entities"]:
            assert 0 <= g["start"] < g["end"] <= len(ex["text"])
            assert ex["text"][g["start"] : g["end"]] == g["text"]


def test_feedback_lifecycle(client):
    # Start empty.
    listing = client.get("/api/v1/workbench/feedback").json()
    assert listing["pending"] == []
    assert listing["datasets"] == []

    # Create an entry. Raw text must NOT be stored by default.
    r = client.post(
        "/api/v1/workbench/feedback",
        json={
            "kind": "incorrect",
            "text": "my name is rahul, email me at rahul@example.com",
            "sanitized_text": "my name is [PERSON_1], email me at [EMAIL_1]",
            "entity_type": "PERSON",
            "expected_type": "PERSON",
            "start": 11,
            "end": 16,
            "mismatch_type": "classification",
            "score": 0.9,
        },
    )
    assert r.status_code == 200
    created = r.json()
    assert created["id"]
    assert created["raw_text_stored"] is False
    assert created["text"] is None
    assert created["sanitized_text"].startswith("my name is [PERSON_1]")

    listing = client.get("/api/v1/workbench/feedback").json()
    assert len(listing["pending"]) == 1

    # Accept into a dataset version.
    r = client.post("/api/v1/workbench/feedback/accept", json={"entry_ids": None})
    assert r.status_code == 200
    accepted = r.json()
    assert accepted["accepted"] == 1
    assert accepted["version"] == 1
    assert accepted["remaining"] == 0

    listing = client.get("/api/v1/workbench/feedback").json()
    assert listing["pending"] == []
    assert any(d["version"] == 1 for d in listing["datasets"])


def test_feedback_rejects_unknown_kind(client):
    r = client.post("/api/v1/workbench/feedback", json={"kind": "bogus"})
    assert r.status_code == 422


def test_feedback_rejects_bad_span(client):
    r = client.post(
        "/api/v1/workbench/feedback",
        json={"kind": "incorrect", "start": 10, "end": 5},
    )
    assert r.status_code == 422


def test_feedback_discard(client):
    created = client.post(
        "/api/v1/workbench/feedback", json={"kind": "missed", "start": 0, "end": 3}
    ).json()
    r = client.delete(f"/api/v1/workbench/feedback/{created['id']}")
    assert r.status_code == 200
    assert client.get("/api/v1/workbench/feedback").json()["pending"] == []


def test_feedback_discard_missing_returns_404(client):
    r = client.delete("/api/v1/workbench/feedback/does-not-exist")
    assert r.status_code == 404


def test_accept_with_no_pending_is_a_noop(client):
    r = client.post("/api/v1/workbench/feedback/accept", json={"entry_ids": None})
    assert r.status_code == 200
    body = r.json()
    assert body["accepted"] == 0
    assert body["version"] is None


def test_workbench_can_be_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("CIPHERFORGE_WORKBENCH_ENABLED", "false")
    monkeypatch.setenv("CIPHERFORGE_FEEDBACK_DIR", str(tmp_path))
    get_settings.cache_clear()
    try:
        c = TestClient(app)
        assert c.get("/api/v1/workbench/system").status_code == 404
    finally:
        get_settings.cache_clear()


def test_core_endpoints_unchanged(client):
    """The workbench must not disturb the core contracts."""
    r = client.post("/api/v1/process", json={"text": "Email me at a@b.com"})
    assert r.status_code == 200
    body = r.json()
    assert "sanitized_text" in body
    assert "entities" in body
    # The original-to-placeholder mapping must never be returned.
    assert "mapping" not in body
