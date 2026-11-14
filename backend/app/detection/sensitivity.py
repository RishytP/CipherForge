"""Sensitivity classification stage.

This module sits *between* entity detection and anonymisation. Detection answers
"is this an entity, and what kind?"; sensitivity classification answers the
separate question "does this entity identify a person, or is it something that
should stay visible?"

The original bug this fixes
---------------------------
The engine used to treat every confident detection as sensitive, so a public
organisation ("Microsoft", "Google") or a public place ("Delhi") was anonymised.
Recognition and sensitivity are genuinely different questions, so they now live
in different modules.

Decisions
---------
* ``PROTECT`` -- personal / sensitive data that must be anonymised.
* ``KEEP``    -- non-sensitive text (public organisations, public places, generic
                 references) that should remain unchanged.
* ``REVIEW``  -- ambiguous: the engine declines to make an over-confident
                 decision and surfaces the span for a human instead.

Design notes
------------
* The classifier is deliberately **rule- and evidence-driven**, not another
  statistical model. Every decision carries a machine-readable reason and a
  human-readable rationale, which is what makes the pipeline explainable.
* It does **not** classify every PERSON / ORGANIZATION / LOCATION as sensitive.
  Only context that ties a named entity to an actual person (self-reference,
  contact detail, private/undisclosed framing) escalates it to PROTECT.
* Organisation names are kept by default. They are only protected when an
  explicit confidentiality policy opts in (see ``Settings``), never as personal
  information implicitly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

from app.config import get_settings
from app.models.schemas import EntityType, SensitivityDecision

# --------------------------------------------------------------------------
# Cue lexicons (kept local to this module so it can be tuned independently)
# --------------------------------------------------------------------------

# Ties a *named* entity to an actual, identifiable person.
_PERSONAL_TIE_CUES = [
    r"\bmy name is\b", r"\bmy name'?s\b", r"\bname is\b", r"\bnamed\b",
    r"\bi'?m\b", r"\bi am\b", r"\bmyself\b", r"\bthis is\b",
    r"\bmy friend\b", r"\bmy colleague\b", r"\bmy brother\b", r"\bmy sister\b",
    r"\bmy (?:boss|manager|wife|husband|son|daughter|mother|father|partner)\b",
    r"\bworks (?:at|for|with)\b", r"\bi work (?:at|for|with)\b",
    r"\bmy (?:number|no|num|mobile|phone|whatsapp|email|mail|address|id|pan|aadhaar)\b",
    r"\bcontact me\b", r"\breach me\b", r"\bcall me\b",
]

# The span names a private or undisclosed party -> the name itself is sensitive.
_PRIVATE_ENTITY_CUES = [
    r"\bundisclosed\b", r"\bconfidential\b", r"\bprivate client\b",
    r"\bmy client\b", r"\banonymous\b", r"\bwish to remain\b", r"\bsecret\b",
]

# A personal-data transfer frame: the recipient/object of the sentence is
# sensitive *because of what is being sent*, not because of who they are.
_PERSONAL_DATA_FRAME_CUES = [
    r"\bmy (?:personal|private|medical|financial|bank|salary|confidential)\b",
    r"\bpersonal (?:records|details|data|information|documents|files|documents)\b",
    r"\bmedical records\b", r"\bbank (?:details|statement|account)\b",
    r"\bmy (?:records|documents|files|papers|documents)\b",
    r"\bsalary (?:slip|details)\b",
]

# Public institutions and well-known organisations: never personal by default.
_PUBLIC_ORG_HINTS = [
    r"\b(?:google|microsoft|amazon|apple|meta|facebook|netflix|adobe|oracle|"
    r"ibm|intel|nvidia|samsung|sony|tcs|infosys|wipro|accenture|deloitte|"
    r"goldman|mckinsey)\b",
    r"\b(?:university|institute|college|school|iit|iim|nit|iiit|aiims|"
    r"hospital|bank|airlines|airways|railway)\b",
    r"\b(?:ministry|department|municipal|corporation|council|authority)\b",
]

# A bare public place reference (no street number, no locative tying it to a
# home) is not identifying on its own.
_STREET_NUMBER_RE = re.compile(r"\b\d{1,4}\s*[,/-]?\s*[A-Za-z]")
_ROAD_WORD_RE = re.compile(
    r"\b(?:road|rd|street|st|lane|ln|marg|avenue|ave|boulevard|drive|nagar|"
    r"colony|sector|block|flat|house|apartment|apartments|floor)\b",
    re.IGNORECASE,
)
# Locative framing that binds a place to the speaker ("I live in Delhi").
_PERSONAL_LOCATION_CUES = [
    r"\bi live\b", r"\bi stay\b", r"\bi reside\b", r"\bmy (?:home|house|flat|address)\b",
    r"\bi'?m from\b", r"\bi am from\b", r"\bcome from\b", r"\bmy address\b",
    r"\bmy (?:city|town|village|hometown)\b",
]
# "studies at X", "visiting X" -> the place is a third-party reference.
_THIRD_PARTY_PLACE_CUES = [
    r"\bstudies at\b", r"\bstudy at\b", r"\bworks at\b", r"\bworks in\b",
    r"\bvisiting\b", r"\bvisited\b", r"\bwent to\b", r"\bgoes to\b",
    r"\btravel(?:s|ling)? to\b", r"\bflight to\b", r"\bfrom the\b",
]


@dataclass
class SensitivityResult:
    """Outcome of sensitivity classification for one detected entity."""

    decision: SensitivityDecision
    reason: str
    rationale: str
    evidence: List[str] = field(default_factory=list)
    is_personal_data: bool = False


# Entity types that are personal data by their very nature. There is no benign
# reading of an email address, phone number, government ID or credential.
_INHERENTLY_PERSONAL = {
    EntityType.EMAIL_ADDRESS: "contact detail uniquely tied to a person",
    EntityType.PHONE_NUMBER: "contact detail uniquely tied to a person",
    EntityType.IN_AADHAAR: "government identity number",
    EntityType.IN_PAN: "government identity number",
    EntityType.CREDIT_CARD: "financial identifier",
    EntityType.CREDENTIAL: "authentication secret",
    EntityType.ADDRESS: "residential/postal address",
}

# Types that describe a party, not a person. Sensitive only with context.
_CONTEXTUAL_TYPES = {EntityType.PERSON, EntityType.ORGANIZATION, EntityType.LOCATION}


class SensitivityClassifier:
    """Decides PROTECT / KEEP / REVIEW for a detected entity, with evidence."""

    def __init__(self) -> None:
        self.settings = get_settings()

    # -- public API --------------------------------------------------------

    def classify(
        self,
        text: str,
        entity_type: EntityType,
        start: int,
        end: int,
        score: float,
        needs_review: bool = False,
    ) -> SensitivityResult:
        span_text = text[start:end]

        # 1. Inherently personal data: protect, always.
        if entity_type in _INHERENTLY_PERSONAL:
            return SensitivityResult(
                decision=SensitivityDecision.PROTECT,
                reason="inherently_personal_type",
                rationale=(
                    f"{entity_type.value} is {_INHERENTLY_PERSONAL[entity_type]}; "
                    "it is protected by default."
                ),
                evidence=[f"entity type {entity_type.value} is personal data by nature"],
                is_personal_data=True,
            )

        # 2. Party-like entities (person / organisation / location): context decides.
        if entity_type in _CONTEXTUAL_TYPES:
            return self._classify_party(text, entity_type, start, end, score, needs_review)

        # 3. Anything else (URL, IP, postal code, ...) is not personal by default.
        return SensitivityResult(
            decision=SensitivityDecision.KEEP,
            reason="not_personal_by_default",
            rationale=(
                f"{entity_type.value} is not personal data on its own; kept unchanged."
            ),
            evidence=[f"entity type {entity_type.value} carries no inherent personal identity"],
        )

    # -- party-like entities ----------------------------------------------

    def _classify_party(
        self,
        text: str,
        entity_type: EntityType,
        start: int,
        end: int,
        score: float,
        needs_review: bool,
    ) -> SensitivityResult:
        span_text = text[start:end]
        left = _left(text, start, 60)
        right = _right(text, end, 40)
        whole = text[max(0, start - 60):min(len(text), end + 60)]
        lowered_whole = whole.lower()

        if entity_type is EntityType.ORGANIZATION:
            return self._classify_organization(
                span_text, left, right, whole, lowered_whole, score
            )
        if entity_type is EntityType.LOCATION:
            return self._classify_location(
                span_text, left, right, whole, score, needs_review
            )
        return self._classify_person(span_text, left, right, whole, score, needs_review)

    def _classify_organization(
        self, span_text, left, right, whole, lowered_whole, score
    ) -> SensitivityResult:
        # Explicit confidentiality policy: opt-in, off by default.
        if self.settings.protect_organizations:
            return SensitivityResult(
                decision=SensitivityDecision.PROTECT,
                reason="org_confidentiality_policy",
                rationale=(
                    "Organisation protection is enabled by policy "
                    "(CIPHERFORGE_PROTECT_ORGANIZATIONS)."
                ),
                evidence=["organisation confidentiality policy is on"],
                is_personal_data=False,
            )

        # The sentence is about sending the speaker's own private data to this
        # party, so the recipient is sensitive in this context.
        if _match(left + " " + right, _PERSONAL_DATA_FRAME_CUES):
            return SensitivityResult(
                decision=SensitivityDecision.PROTECT,
                reason="personal_data_transfer_frame",
                rationale=(
                    f"'{span_text}' is the recipient in a frame that moves the "
                    "speaker's own personal data, so it is sensitive here."
                ),
                evidence=[f"personal-data frame near '{span_text}'"],
                is_personal_data=True,
            )

        # A private / undisclosed party: the name itself is sensitive.
        if _match(left, _PRIVATE_ENTITY_CUES):
            return SensitivityResult(
                decision=SensitivityDecision.PROTECT,
                reason="private_party_reference",
                rationale=(
                    f"'{span_text}' is described as a private or undisclosed party."
                ),
                evidence=["private/undisclosed framing"],
                is_personal_data=True,
            )

        # A public organisation named with no personal tie stays visible.
        if _match(span_text, _PUBLIC_ORG_HINTS) or _match(whole, _PUBLIC_ORG_HINTS):
            return SensitivityResult(
                decision=SensitivityDecision.KEEP,
                reason="public_organization",
                rationale=(
                    f"'{span_text}' is a public organisation name, not personal data."
                ),
                evidence=[f"'{span_text}' matches a known public organisation"],
            )

        # Unknown organisation: no personal tie, no confidentiality policy. Keep
        # it, but say why plainly rather than pretending certainty.
        return SensitivityResult(
            decision=SensitivityDecision.KEEP,
            reason="organization_not_personal",
            rationale=(
                f"'{span_text}' is an organisation, not a person; it is kept unless "
                "a confidentiality policy is configured."
            ),
            evidence=["organisation names are not personal data by default"],
        )

    def _classify_location(
        self, span_text, left, right, whole, score, needs_review
    ) -> SensitivityResult:
        has_number = bool(_STREET_NUMBER_RE.search(span_text)) or bool(
            re.search(r"\d", span_text)
        )
        road_word = bool(_ROAD_WORD_RE.search(span_text))

        # A full street address (number + road) is identifying.
        if has_number and road_word:
            return SensitivityResult(
                decision=SensitivityDecision.PROTECT,
                reason="identifying_street_address",
                rationale=(
                    f"'{span_text}' includes a street number and road, which can "
                    "locate a specific premises."
                ),
                evidence=["street number plus road name"],
                is_personal_data=True,
            )

        # A bare road with no number is weak, non-identifying evidence.
        if road_word and not has_number:
            return SensitivityResult(
                decision=SensitivityDecision.REVIEW,
                reason="bare_road_reference",
                rationale=(
                    f"'{span_text}' names a road with no number; it may or may not "
                    "identify a place, so it is flagged for review."
                ),
                evidence=["road name without a street number"],
            )

        # Personal locative framing ("I live in Delhi") -> the speaker's place.
        if _match(left, _PERSONAL_LOCATION_CUES):
            return SensitivityResult(
                decision=SensitivityDecision.PROTECT,
                reason="personal_location_reference",
                rationale=(
                    f"'{span_text}' is tied to the speaker by locative framing "
                    "('I live in ...'), so it is treated as personal."
                ),
                evidence=[f"personal locative cue near '{span_text}'"],
                is_personal_data=True,
            )

        # Third-party reference ("my friend studies at IIT Delhi", "visiting Goa")
        # -> a public/other place, not the speaker's own location.
        if _match(left, _THIRD_PARTY_PLACE_CUES):
            return SensitivityResult(
                decision=SensitivityDecision.KEEP,
                reason="third_party_place_reference",
                rationale=(
                    f"'{span_text}' is referenced as a third-party or public place, "
                    "not as the speaker's own location."
                ),
                evidence=["third-party place framing"],
            )

        # A bare place name with no personal tie: ambiguous by default. Do not
        # pretend certainty. If policy opts in, protect instead.
        if self.settings.protect_locations:
            return SensitivityResult(
                decision=SensitivityDecision.PROTECT,
                reason="location_protection_policy",
                rationale=(
                    f"'{span_text}' is protected because location protection is "
                    "enabled by policy (CIPHERFORGE_PROTECT_LOCATIONS)."
                ),
                evidence=["location protection policy is on"],
                is_personal_data=True,
            )
        return SensitivityResult(
            decision=SensitivityDecision.REVIEW,
            reason="bare_place_name",
            rationale=(
                f"'{span_text}' is a place name with no context tying it to a "
                "person; flagged for review rather than protected or dropped."
            ),
            evidence=["place name without personal context"],
        )

    def _classify_person(
        self, span_text, left, right, whole, score, needs_review
    ) -> SensitivityResult:
        # Self-reference / explicit personal tie -> a real, identifiable person.
        # Cues may sit before ("my name is Rahul") or after ("Rahul works at X").
        tie = _match(left, _PERSONAL_TIE_CUES) or _match(right, _PERSONAL_TIE_CUES)
        if tie:
            return SensitivityResult(
                decision=SensitivityDecision.PROTECT,
                reason="personal_reference",
                rationale=(
                    f"'{span_text}' is tied to a real person by '{tie.strip()}'."
                ),
                evidence=[f"personal cue: '{tie.strip()}'"],
                is_personal_data=True,
            )

        # A private/undisclosed party name is sensitive even in third person.
        if _match(left, _PRIVATE_ENTITY_CUES):
            return SensitivityResult(
                decision=SensitivityDecision.PROTECT,
                reason="private_party_reference",
                rationale=f"'{span_text}' is described as a private/undisclosed party.",
                evidence=["private/undisclosed framing"],
                is_personal_data=True,
            )

        # Personal-data transfer frame naming this person.
        if _match(whole, _PERSONAL_DATA_FRAME_CUES):
            return SensitivityResult(
                decision=SensitivityDecision.PROTECT,
                reason="personal_data_transfer_frame",
                rationale=(
                    f"'{span_text}' appears in a frame that moves personal data, so "
                    "it is sensitive here."
                ),
                evidence=["personal-data frame nearby"],
                is_personal_data=True,
            )

        # A third-person named individual: still a real person's name, so protect,
        # but we say so explicitly and let low confidence drive review upstream.
        if not needs_review:
            return SensitivityResult(
                decision=SensitivityDecision.PROTECT,
                reason="person_name",
                rationale=(
                    f"'{span_text}' is a person's name; names are personal data."
                ),
                evidence=["detected as a person name"],
                is_personal_data=True,
            )

        # Ambiguous / low-confidence name (e.g. fiction, bare capitalisation).
        return SensitivityResult(
            decision=SensitivityDecision.REVIEW,
            reason="ambiguous_person",
            rationale=(
                f"'{span_text}' may be a name but the engine is not confident it "
                "identifies a real person; flagged for review."
            ),
            evidence=["low-confidence or ambiguous name"],
        )


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _left(text: str, start: int, radius: int) -> str:
    return text[max(0, start - radius):start]


def _right(text: str, end: int, radius: int) -> str:
    return text[end:min(len(text), end + radius)]


def _match(text: str, patterns: List[str]) -> Optional[str]:
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(0)
    return None


__all__ = [
    "SensitivityClassifier",
    "SensitivityDecision",
    "SensitivityResult",
]
