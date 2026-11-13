"""Runtime configuration for CipherForge.

All tunables (thresholds, limits, model names) live here so behaviour can be
adjusted without editing detection logic. Nothing here reads secrets from the
environment beyond an optional settings prefix; no credentials are ever
hard-coded.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CIPHERFORGE_", extra="ignore")

    app_name: str = "CipherForge"
    engine_version: str = "0.1.0"

    # --- spaCy / Presidio ---
    spacy_model: str = "en_core_web_sm"
    supported_languages: tuple[str, ...] = ("en",)

    # --- Input validation ---
    max_input_chars: int = 20_000
    min_input_chars: int = 1

    # --- Confidence thresholds ---
    # A detection at or above `high_threshold` is treated as confident.
    high_threshold: float = 0.75
    # Between `medium` and `high` the engine is fairly sure but not certain.
    medium_threshold: float = 0.5
    # Between `low` and `medium` the engine flags for review.
    low_threshold: float = 0.3
    # Anything below `low_threshold` is dropped unless explicitly requested.
    default_score_threshold: float = 0.3

    # --- Contextual scoring ---
    # Maximum amount (added/subtracted) contextual analysis may shift a score.
    context_max_boost: float = 0.30
    context_max_penalty: float = 0.35
    # Window (in characters) around a span searched for contextual cues.
    context_window_chars: int = 60

    # --- NLP behaviour ---
    nlp_person_base_score: float = 0.60
    nlp_org_base_score: float = 0.55
    nlp_location_base_score: float = 0.55

    # --- Anonymisation ---
    default_strategy: str = "placeholder"
    mask_char: str = "*"
    mask_keep_last: int = 4
    # Privacy-first default: spans flagged for review are still anonymised, so
    # an uncertain detection never leaks raw PII. Set to False to keep such
    # spans verbatim and surface them for human review instead.
    anonymize_uncertain: bool = True

    # --- Privacy / logging ---
    log_raw_text: bool = False

    # --- Developer workbench ---
    # The workbench is a development/evaluation tool, not a production surface.
    # It exposes read-only system/evaluation endpoints and a feedback queue.
    # Disable it to reduce the attack surface in a production deployment.
    workbench_enabled: bool = True
    # Feedback entries capture raw text only when this is explicitly enabled.
    # Off by default: feedback defaults to storing a sanitised snippet and
    # offsets, never real personal information.
    feedback_store_raw_text: bool = False
    # Directory for the pending feedback queue and versioned feedback datasets.
    feedback_dir: str = "backend/data/feedback"


@lru_cache
def get_settings() -> Settings:
    return Settings()
