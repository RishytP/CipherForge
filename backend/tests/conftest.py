"""Shared pytest fixtures.

The analyzer is built once per session (loading spaCy is expensive) and reused
across tests. These are integration tests against the real detection stack, not
mocks.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make the backend package importable when pytest runs from the repo root.
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


@pytest.fixture(scope="session")
def analyzer():
    from app.detection.analyzer import PiiAnalyzer

    return PiiAnalyzer()


@pytest.fixture(scope="session")
def anonymizer():
    from app.anonymization.engine import AnonymizationEngine

    return AnonymizationEngine()


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def entities_of(response, entity_type: str | None = None):
    """Return detected entity texts, optionally filtered by type."""
    items = response.entities
    if entity_type:
        items = [e for e in items if e.entity_type.value == entity_type]
    return [e.text for e in items]
