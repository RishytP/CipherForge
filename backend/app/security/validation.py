"""Input validation and safe logging helpers.

Privacy rule: raw prompts are never logged by default. ``redact_for_log``
returns a length-only descriptor unless ``CIPHERFORGE_LOG_RAW_TEXT`` is set.
"""

from __future__ import annotations

from app.config import get_settings


class InputValidationError(ValueError):
    """Raised when a request payload violates a size or type constraint."""


def validate_text(text: object) -> str:
    """Validate type and length of incoming free text.

    Returns the original text unchanged (detection offsets depend on exact
    characters). Raises :class:`InputValidationError` on violation.
    """
    settings = get_settings()

    if not isinstance(text, str):
        raise InputValidationError("text must be a string")

    length = len(text)
    if length < settings.min_input_chars:
        raise InputValidationError("text is too short to analyse")
    if length > settings.max_input_chars:
        raise InputValidationError(
            f"text exceeds maximum length of {settings.max_input_chars} characters"
        )
    if not text.strip():
        raise InputValidationError("text must not be empty or whitespace only")

    # Reject strings dominated by control characters (a cheap abuse guard).
    control = sum(1 for ch in text if ord(ch) < 32 and ch not in "\n\t\r")
    if length and control / length > 0.2:
        raise InputValidationError("text contains too many control characters")

    return text


def redact_for_log(text: str) -> str:
    """Return a privacy-safe descriptor of ``text`` for logging."""
    settings = get_settings()
    if settings.log_raw_text:
        return text
    return f"<text len={len(text)} sha256_present={bool(text)}>"
