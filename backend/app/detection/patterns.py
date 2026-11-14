"""Layer A: structured pattern detection.

This module defines custom Presidio recognisers for entities that have a
recognisable structure (email, phone, postal code, IP, IDs, ...) and that the
default Presidio registry either misses or gets wrong for Indian / informal
input.

Design notes
------------
* Patterns are written to tolerate irregular spacing and punctuation, because
  real users write ``98 765-43210`` rather than ``+91 98765 43210``.
* The default Presidio registry is *not* used wholesale: it produces noisy
  matches such as ``UK_NHS`` for a 10-digit Indian mobile number. We register
  a curated set instead.
* Pattern matches are validated by post-processing callbacks where a pure
  regex would be too permissive (e.g. phone digit counts, pincode ranges).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List, Optional

import phonenumbers
from presidio_analyzer import AnalysisExplanation, Pattern, PatternRecognizer, RecognizerResult
from presidio_analyzer.entity_recognizer import EntityRecognizer

from app.models.schemas import EntityType

# --------------------------------------------------------------------------
# Shared regex fragments
# --------------------------------------------------------------------------

_WS = r"[ \t\u00a0]*"
# Indian country code may appear as +91, 0091, 91-, (91)
_IN_CC = r"(?:\+?91[\-\s]?|0)?"


def _strip_separators(value: str) -> str:
    return re.sub(r"[\s\-\.\(\)]", "", value)


# --------------------------------------------------------------------------
# Email
# --------------------------------------------------------------------------

_EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+\-]+"
    r"(?:@|\s*\(at\)\s*|\s+at\s+)"
    r"[A-Za-z0-9.\-]+\s*(?:\.|\(dot\)|\s+dot\s+)\s*[A-Za-z]{2,}",
    re.IGNORECASE,
)


class EmailRecognizer(PatternRecognizer):
    """Email addresses, including obfuscated and oddly-spaced variants."""

    PATTERNS = [
        Pattern(
            name="email_standard",
            regex=r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}",
            score=0.95,
        ),
        Pattern(
            name="email_obfuscated",
            regex=r"[A-Za-z0-9._%+\-]+\s*(?:\(at\)|\bat\b)\s*[A-Za-z0-9.\-]+\s*(?:\(dot\)|\bdot\b)\s*[A-Za-z]{2,}",
            score=0.85,
        ),
        Pattern(
            name="email_spaced_dots",
            regex=(
                r"[A-Za-z0-9._%+\-]+(?:\s+\.\s+[A-Za-z0-9_%+\-]+)*\s*@\s*"
                r"[A-Za-z0-9\-]+(?:\s+\.\s+[A-Za-z0-9\-]+)+"
            ),
            score=0.8,
        ),
    ]

    CONTEXT = ["email", "mail", "e-mail", "id", "contact", "reach", "write"]

    def __init__(self) -> None:
        super().__init__(
            supported_entity=EntityType.EMAIL_ADDRESS.value,
            name="CipherForgeEmailRecognizer",
            patterns=self.PATTERNS,
            context=self.CONTEXT,
        )

    def validate_result(self, pattern_text: str) -> bool:  # noqa: D102
        normalised = pattern_text
        normalised = re.sub(r"\s*\(at\)\s*", "@", normalised, flags=re.IGNORECASE)
        normalised = re.sub(r"\s+at\s+", "@", normalised, flags=re.IGNORECASE)
        normalised = re.sub(r"\s*\(dot\)\s*", ".", normalised, flags=re.IGNORECASE)
        normalised = re.sub(r"\s+dot\s+", ".", normalised, flags=re.IGNORECASE)
        # Collapse spaced dots / at signs introduced by informal spacing.
        normalised = re.sub(r"\s*\.\s*", ".", normalised)
        normalised = re.sub(r"\s*@\s*", "@", normalised)
        return bool(
            re.fullmatch(
                r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", normalised
            )
        )


# --------------------------------------------------------------------------
# Phone numbers (international + Indian, tolerant of odd spacing)
# --------------------------------------------------------------------------

# Digits with optional separators (single space, hyphen, dot) or grouped by
# whitespace. Boundaries avoid eating surrounding words. The regex is
# intentionally permissive; ``validate_result`` rejects implausible numbers.
_PHONE_LOOSE = (
    r"(?<!\w)"
    r"(?:\+?\d{1,3}[\-\s]?)?"
    r"(?:\(\d{2,4}\)[\-\s]?)?"
    r"\d{2,5}"
    r"(?:(?:[\s\-\.]+\d{2,5}){1,4}|\d{4,9})"
    r"(?!\w)"
)


class PhoneRecognizer(PatternRecognizer):
    """Phone numbers, validated and normalised with libphonenumber.

    The regex is intentionally permissive; ``validate_result`` rejects
    anything that libphonenumber cannot parse as a plausible number in the
    configured regions. This keeps ``98 765-43210`` while rejecting dates and
    arbitrary digit runs.
    """

    PATTERNS = [Pattern(name="phone_loose", regex=_PHONE_LOOSE, score=0.55)]

    CONTEXT = [
        "phone", "mobile", "no", "number", "call", "contact", "reach",
        "whatsapp", "cell", "ph", "telephone", "dial",
    ]

    REGIONS = ("IN", "US", "GB")

    def __init__(self) -> None:
        super().__init__(
            supported_entity=EntityType.PHONE_NUMBER.value,
            name="CipherForgePhoneRecognizer",
            patterns=self.PATTERNS,
            context=self.CONTEXT,
        )

    def validate_result(self, pattern_text: str) -> bool:
        digits = _strip_separators(pattern_text)
        if digits.startswith("00"):
            digits = "+" + digits[2:]
        core = re.sub(r"\D", "", digits)
        if not (7 <= len(core) <= 15):
            return False
        # Reject runs of a single repeated digit (e.g. 0000000) which are
        # rarely real numbers, but allow repeating patterns like 9898989898.
        if len(set(core)) == 1:
            return False
        for region in self.REGIONS:
            try:
                num = phonenumbers.parse(digits, region)
            except phonenumbers.NumberParseException:
                continue
            if phonenumbers.is_possible_number(num) and phonenumbers.is_valid_number(num):
                return True
        # Indian mobiles are frequently written without +91; accept a bare
        # 10-digit number starting 6-9 when region parsing validates it.
        if re.fullmatch(r"[6-9]\d{9}", core):
            return True
        return False


# --------------------------------------------------------------------------
# Postal code (Indian PIN first, then generic)
# --------------------------------------------------------------------------

class PostalCodeRecognizer(PatternRecognizer):
    """Indian 6-digit PIN codes (allowing spaced digits)."""

    PATTERNS = [
        Pattern(
            name="in_pincode",
            regex=r"(?<!\d)[1-9]\d{5}(?!\d)",
            score=0.45,
        ),
        Pattern(
            name="in_pincode_spaced",
            regex=r"(?<!\d)[1-9](?:\s\d){5}(?!\d)",
            score=0.4,
        ),
    ]

    CONTEXT = ["pin", "pincode", "postal", "zip", "post", "code", "address"]

    def __init__(self) -> None:
        super().__init__(
            supported_entity=EntityType.POSTAL_CODE.value,
            name="CipherForgePostalCodeRecognizer",
            patterns=self.PATTERNS,
            context=self.CONTEXT,
        )

    def validate_result(self, pattern_text: str) -> bool:
        core = re.sub(r"\D", "", pattern_text)
        return bool(re.fullmatch(r"[1-9]\d{5}", core))


# --------------------------------------------------------------------------
# IP address
# --------------------------------------------------------------------------

class IpAddressRecognizer(PatternRecognizer):
    PATTERNS = [
        Pattern(
            name="ipv4",
            regex=r"(?<![\d.])(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?![\d.])",
            score=0.9,
        ),
    ]

    CONTEXT = ["ip", "server", "host", "address", "network"]

    def __init__(self) -> None:
        super().__init__(
            supported_entity=EntityType.IP_ADDRESS.value,
            name="CipherForgeIpRecognizer",
            patterns=self.PATTERNS,
            context=self.CONTEXT,
        )


# --------------------------------------------------------------------------
# Indian Aadhaar-like and PAN-like identifiers
# --------------------------------------------------------------------------

class AadhaarRecognizer(PatternRecognizer):
    """12-digit Aadhaar-style numbers, spaced as 4-4-4 (loosely)."""

    PATTERNS = [
        Pattern(
            name="aadhaar_grouped",
            regex=r"(?<!\d)[2-9]\d{3}\s?\d{4}\s?\d{4}(?!\d)",
            score=0.5,
        ),
    ]

    CONTEXT = ["aadhaar", "aadhar", "uid", "uidai", "adhar"]

    def __init__(self) -> None:
        super().__init__(
            supported_entity=EntityType.IN_AADHAAR.value,
            name="CipherForgeAadhaarRecognizer",
            patterns=self.PATTERNS,
            context=self.CONTEXT,
        )

    def validate_result(self, pattern_text: str) -> bool:
        core = re.sub(r"\D", "", pattern_text)
        if not re.fullmatch(r"[2-9]\d{11}", core):
            return False
        # Verhoeff checksum is the real Aadhaar guard; implement the standard
        # tables so obviously fake 12-digit runs are not auto-labelled.
        return _verhoeff_valid(core)


class PanRecognizer(PatternRecognizer):
    """Indian PAN: 5 letters, 4 digits, 1 letter (e.g. ABCDE1234F)."""

    PATTERNS = [
        Pattern(
            name="pan",
            regex=r"(?<![A-Za-z0-9])[A-Za-z]{5}\s?\d{4}\s?[A-Za-z](?![A-Za-z0-9])",
            score=0.6,
        ),
    ]

    CONTEXT = ["pan", "permanent", "account", "tax", "card", "income"]

    def __init__(self) -> None:
        super().__init__(
            supported_entity=EntityType.IN_PAN.value,
            name="CipherForgePanRecognizer",
            patterns=self.PATTERNS,
            context=self.CONTEXT,
        )

    def validate_result(self, pattern_text: str) -> bool:
        core = re.sub(r"\s", "", pattern_text).upper()
        if not re.fullmatch(r"[A-Z]{5}\d{4}[A-Z]", core):
            return False
        # Fourth character encodes holder type; fifth is a surname initial.
        return core[3] in "PCHFATBLJG"


# --------------------------------------------------------------------------
# Verhoeff checksum (used by Aadhaar)
# --------------------------------------------------------------------------

_VERHOEFF_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
    [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8],
    [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2],
    [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
    [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
_VERHOEFF_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
    [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0],
    [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5],
    [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]
_VERHOEFF_INV = [0, 4, 3, 2, 1, 5, 6, 7, 8, 9]


def _verhoeff_valid(number: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(number)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[i % 8][int(ch)]]
    return c == 0


# --------------------------------------------------------------------------
# Address fragments (Indian street-address shapes)
# --------------------------------------------------------------------------

class AddressRecognizer(PatternRecognizer):
    """Heuristic address-fragment detection.

    Free-form addresses are genuinely hard and cannot be captured by regex
    alone. These patterns cover the most common *Indian* street shapes such as
    ``24, MG Road`` or ``Flat 3B, Green Park Colony``. Clause-level addresses
    introduced by a cue ("my address is ...") are handled separately by the
    contextual layer, which is more robust for prose. This recogniser is
    deliberately conservative; addresses remain the weakest layer.
    """

    PATTERNS = [
        Pattern(
            name="in_street",
            regex=(
                r"(?<!\w)\d{1,4}[,\s]+"
                r"(?:[A-Za-z][A-Za-z.'\-]*\s+){0,4}"
                r"(?:road|rd\.?|street|st\.?|lane|ln\.?|marg|colony|nagar|"
                r"sector|block|layout|extension|extn\.?|apartment|apartments|"
                r"flats?|chowk|circle|gali|cross)(?!\w)"
            ),
            score=0.5,
        ),
        Pattern(
            name="in_flat_block",
            regex=(
                r"(?<!\w)(?:flat|plot|house|door|d\.?no\.?|no\.?)\s*"
                r"[A-Za-z0-9\-/]{1,6}\s*,?\s*"
                r"(?:[A-Za-z][A-Za-z.'\-]*\s*){1,4}"
                r"(?:colony|nagar|layout|apartments?|enclave|residency|vihar|puram)(?!\w)"
            ),
            score=0.55,
        ),
    ]

    CONTEXT = ["address", "live", "stay", "reside", "flat", "house", "reach", "location"]

    def __init__(self) -> None:
        super().__init__(
            supported_entity=EntityType.ADDRESS.value,
            name="CipherForgeAddressRecognizer",
            patterns=self.PATTERNS,
            context=self.CONTEXT,
        )


# --------------------------------------------------------------------------
# URL
# --------------------------------------------------------------------------

class UrlRecognizer(PatternRecognizer):
    """URLs including host:port and IP-based hosts.

    Presidio's built-in URL recogniser is inconsistent about ports and
    IP-literal hosts, so we use an explicit pattern.
    """

    PATTERNS = [
        Pattern(
            name="url",
            regex=(
                r"(?:https?://|www\.)"
                r"[A-Za-z0-9.\-]+(?::\d{1,5})?"
                r"(?:/[^\s]*)?"
            ),
            score=0.85,
        ),
    ]

    CONTEXT = ["url", "link", "site", "website", "http", "https"]

    def __init__(self) -> None:
        super().__init__(
            supported_entity=EntityType.URL.value,
            name="CipherForgeUrlRecognizer",
            patterns=self.PATTERNS,
            context=self.CONTEXT,
        )


# --------------------------------------------------------------------------
# Credentials (passwords, API keys, tokens)
# --------------------------------------------------------------------------

# A credential label followed by the secret value. The *value* is what we
# protect, not the label, so this is implemented as a custom recognizer that
# can emit a sub-span (Presidio's PatternRecognizer only reports full matches).
_CREDENTIAL_LABELS = (
    r"password|passwd|pwd|passphrase|passcode|secret|api[\s_-]?key|"
    r"access[\s_-]?token|auth[\s_-]?token|bearer|token|otp"
)
_CREDENTIAL_RE = re.compile(
    r"(?i)\b(?:" + _CREDENTIAL_LABELS + r")\b\s*(?:is|was|:|=|->)?\s*"
    r"([^\s,;]+)"
)

# A trailing period/comma is sentence punctuation, not part of the secret.
_TRAILING_PUNCT_RE = re.compile(r"[.!?;:,)\]}'\"]+$")


class CredentialRecognizer(EntityRecognizer):
    """Detect the *value* of a password / API key / token.

    Emits a span covering only the secret, so ``My password is Microsoft123.``
    protects ``Microsoft123`` and leaves the rest of the sentence intact.
    """

    def __init__(self) -> None:
        super().__init__(
            supported_entities=[EntityType.CREDENTIAL.value],
            name="CipherForgeCredentialRecognizer",
            context=["password", "secret", "token", "key", "otp"],
        )

    def load(self) -> None:  # noqa: D102
        self.is_loaded = True

    def get_supported_entities(self) -> List[str]:  # noqa: D102
        return [EntityType.CREDENTIAL.value]

    def analyze(self, text, entities, nlp_artifacts=None, regex_flags=None):  # noqa: D102
        results: List[RecognizerResult] = []
        for match in _CREDENTIAL_RE.finditer(text):
            value = match.group(1)
            start, end = match.start(1), match.end(1)
            # Trim sentence punctuation from the captured value.
            trimmed = _TRAILING_PUNCT_RE.sub("", value)
            end = start + len(trimmed)
            if len(trimmed) < 4:
                continue
            results.append(
                RecognizerResult(
                    entity_type=EntityType.CREDENTIAL.value,
                    start=start,
                    end=end,
                    score=0.85,
                    analysis_explanation=AnalysisExplanation(
                        recognizer=self.name,
                        original_score=0.85,
                        pattern_name="credential_value",
                        pattern=_CREDENTIAL_RE.pattern,
                        textual_explanation=(
                            "Credential label followed by a secret value."
                        ),
                    ),
                    recognition_metadata={
                        RecognizerResult.RECOGNIZER_NAME_KEY: self.name,
                        RecognizerResult.RECOGNIZER_IDENTIFIER_KEY: self.id,
                    },
                )
            )
        return results


# --------------------------------------------------------------------------
# Location gazetteer (Indian cities/states spaCy's small model misses)
# --------------------------------------------------------------------------

_INDIAN_LOCATIONS = [
    "Mumbai", "Delhi", "New Delhi", "Bengaluru", "Bangalore", "Hyderabad",
    "Chennai", "Kolkata", "Pune", "Ahmedabad", "Jaipur", "Surat", "Lucknow",
    "Kanpur", "Nagpur", "Indore", "Thane", "Bhopal", "Visakhapatnam", "Patna",
    "Vadodara", "Ghaziabad", "Ludhiana", "Agra", "Nashik", "Faridabad",
    "Meerut", "Rajkot", "Varanasi", "Srinagar", "Aurangabad", "Dhanbad",
    "Amritsar", "Navi Mumbai", "Allahabad", "Prayagraj", "Ranchi", "Howrah",
    "Coimbatore", "Jabalpur", "Gwalior", "Vijayawada", "Jodhpur", "Madurai",
    "Raipur", "Kota", "Guwahati", "Chandigarh", "Thiruvananthapuram",
    "Kochi", "Mysuru", "Mysore", "Mangaluru", "Bhubaneswar", "Dehradun",
    "Goa", "Gurgaon", "Gurugram", "Noida", "Kerala", "Karnataka",
    "Maharashtra", "Tamil Nadu", "Telangana", "Gujarat", "Rajasthan",
    "Punjab", "Haryana", "Odisha", "Assam", "Bihar", "Jharkhand",
]

# Lower-cased gazetteer set, used by the contextual layer to decide whether a
# single-token LOCATION/PERSON confusion is really a known place.
INDIAN_LOCATION_NAMES = {name.lower() for name in _INDIAN_LOCATIONS}


class LocationGazetteerRecognizer(PatternRecognizer):
    """Gazetteer fallback for Indian places the spaCy small model misses.

    This complements, and does not replace, spaCy NER. The score is modest so
    contextual analysis and thresholds still govern the final decision.
    """

    PATTERNS = [
        Pattern(
            name=f"in_loc_{i}",
            regex=r"(?<![\w])" + re.escape(name) + r"(?![\w])",
            score=0.5,
        )
        for i, name in enumerate(_INDIAN_LOCATIONS)
    ]

    CONTEXT = ["live", "from", "in", "at", "near", "city", "address", "based"]

    def __init__(self) -> None:
        super().__init__(
            supported_entity=EntityType.LOCATION.value,
            name="CipherForgeLocationGazetteer",
            patterns=self.PATTERNS,
            context=self.CONTEXT,
        )


# --------------------------------------------------------------------------
# Recogniser registry
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class RecognizerSpec:
    name: str
    entity_type: EntityType
    recognizer: PatternRecognizer


def build_recognizers() -> List[PatternRecognizer]:
    """Return the curated Layer A recogniser set."""
    return [
        EmailRecognizer(),
        PhoneRecognizer(),
        PostalCodeRecognizer(),
        IpAddressRecognizer(),
        AadhaarRecognizer(),
        PanRecognizer(),
        AddressRecognizer(),
        UrlRecognizer(),
        CredentialRecognizer(),
        LocationGazetteerRecognizer(),
    ]


__all__ = [
    "build_recognizers",
    "EmailRecognizer",
    "PhoneRecognizer",
    "PostalCodeRecognizer",
    "IpAddressRecognizer",
    "AadhaarRecognizer",
    "PanRecognizer",
    "AddressRecognizer",
    "UrlRecognizer",
    "CredentialRecognizer",
    "LocationGazetteerRecognizer",
]
