"""Placeholder generation and entity-identity normalisation.

The anonymiser must use a *consistent* placeholder for repeated mentions of the
same real-world entity within a single request ("Rahul" ... "Rahul Sharma"
should ideally share an identity when they refer to the same person). True
coreference resolution is hard; this module provides a pragmatic, documented
approximation:

* Exact case-insensitive matches always share an identity.
* A shorter name that is a token-subset of a longer name in the same document
  (e.g. "Rahul" inside "Rahul Sharma") is treated as the same identity, unless
  the shorter form is a common word.

The mapping produced here is request-scoped and never leaves the backend.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from app.models.schemas import EntityType

_STOPWORDS = {
    "the", "a", "an", "of", "and", "at", "on", "in", "near", "mr", "mrs",
    "ms", "dr", "sir", "madam", "shri", "smt", "my", "old",
}

# Short, human-readable labels for placeholders (the spec uses [PERSON_1] and
# [EMAIL_1], not [EMAIL_ADDRESS_1]).
_SHORT_LABELS = {
    EntityType.PERSON: "PERSON",
    EntityType.EMAIL_ADDRESS: "EMAIL",
    EntityType.PHONE_NUMBER: "PHONE",
    EntityType.LOCATION: "LOCATION",
    EntityType.ORGANIZATION: "ORG",
    EntityType.ADDRESS: "ADDRESS",
    EntityType.POSTAL_CODE: "PINCODE",
    EntityType.IP_ADDRESS: "IP",
    EntityType.URL: "URL",
    EntityType.DATE_TIME: "DATE",
    EntityType.CREDIT_CARD: "CARD",
    EntityType.IN_AADHAAR: "AADHAAR",
    EntityType.IN_PAN: "PAN",
    EntityType.ID_NUMBER: "ID",
    EntityType.OTHER: "OTHER",
}


def short_label(entity_type: EntityType) -> str:
    """Return the placeholder label used for an entity type."""
    return _SHORT_LABELS.get(entity_type, entity_type.value)


def normalize_identity_key(text: str, entity_type: EntityType) -> str:
    """Return a normalised key used to decide identity equality."""
    cleaned = re.sub(r"[^\w\s@.\-+]", " ", text.lower())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    # For structured entities, ignore separators entirely.
    if entity_type in (
        EntityType.EMAIL_ADDRESS,
        EntityType.PHONE_NUMBER,
        EntityType.IP_ADDRESS,
        EntityType.POSTAL_CODE,
        EntityType.IN_AADHAAR,
        EntityType.IN_PAN,
        EntityType.CREDIT_CARD,
    ):
        cleaned = re.sub(r"[\s\-\.\(\)]", "", cleaned)
    return cleaned


def _token_set(key: str) -> frozenset:
    return frozenset(t for t in key.split() if t and t not in _STOPWORDS)


@dataclass
class IdentityRegistry:
    """Assigns stable placeholders to entities within one request."""

    counters: Dict[str, int] = field(default_factory=dict)
    # identity key -> assigned placeholder
    assigned: Dict[Tuple[str, str], str] = field(default_factory=dict)
    # (type, token-set) seen, for subset matching on person names
    person_tokens: List[Tuple[frozenset, str]] = field(default_factory=list)

    def placeholder_for(
        self,
        text: str,
        entity_type: EntityType,
        coreference: bool = True,
    ) -> Tuple[str, bool]:
        """Return ``(placeholder, is_new_identity)`` for a mention."""
        key = normalize_identity_key(text, entity_type)
        lookup = (entity_type.value, key)

        if lookup in self.assigned:
            return self.assigned[lookup], False

        if coreference and entity_type is EntityType.PERSON:
            match = self._match_person_subset(key)
            if match is not None:
                self.assigned[lookup] = match
                return match, False

        self.counters[entity_type.value] = self.counters.get(entity_type.value, 0) + 1
        placeholder = f"[{short_label(entity_type)}_{self.counters[entity_type.value]}]"
        self.assigned[lookup] = placeholder

        if entity_type is EntityType.PERSON:
            self.person_tokens.append((_token_set(key), placeholder))
        return placeholder, True

    def _match_person_subset(self, key: str) -> Optional[str]:
        """Match a person mention to an already-registered identity.

        Matches when the token sets are in a subset/superset relation, so
        "Rahul" and "Rahul Sharma" share a placeholder regardless of the order
        in which the engine happens to process the mentions.
        """
        tokens = _token_set(key)
        if not tokens:
            return None
        for seen_tokens, placeholder in self.person_tokens:
            if seen_tokens and (tokens <= seen_tokens or tokens >= seen_tokens):
                return placeholder
        return None


__all__ = ["IdentityRegistry", "normalize_identity_key"]
