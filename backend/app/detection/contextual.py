"""Layer C: contextual analysis.

This is the central CipherForge idea. A pattern match or an NER hit is only a
*candidate*; whether it actually identifies a real subject depends on the words
around it and the intent of the sentence.

Signals implemented here
------------------------
1. **Self-reference cues** -- "my name is X", "i'm X", "call me at X" strongly
   indicate the *speaker* is disclosing their own identifier (raise score).
2. **Contact cues** -- proximity to "email", "phone", "number", "address",
   "call", "reach" raises confidence for the relevant entity type.
3. **Third-person / fictional framing** -- "X is a character in this story",
   "for example X", "hypothetically X" lower confidence: the name may not refer
   to the user.
4. **Negation** -- "not my number", "that's not me" lower confidence.
5. **Common-word and title guards** -- not every capitalised word is a person.
   Single common English words and bare titles ("Mr.", "The office") are
   penalised.
6. **Initial-style names** -- "R.S." is ambiguous; detected as a candidate but
   always flagged for review unless a strong self-reference cue is present.

The module returns a *delta* to the incoming score plus human-readable evidence.
It never overrides a pattern match outright; it shifts confidence so Layer D
can apply thresholds and the uncertain state.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Set, Tuple

from app.config import get_settings
from app.detection.patterns import INDIAN_LOCATION_NAMES
from app.models.schemas import EntityType

# --------------------------------------------------------------------------
# Cue lexicons
# --------------------------------------------------------------------------

# Phrases that introduce the speaker's own identifier.
_SELF_REFERENCE_CUES = [
    r"\bmy name is\b", r"\bmy name'?s\b", r"\bi'?m\b", r"\bi am\b",
    r"\bmyself\b", r"\bthis is\b", r"\bcall me\b", r"\breach me\b",
    r"\bcontact me\b", r"\btext me\b", r"\bmessage me\b", r"\bmail me\b",
    r"\bemail me\b", r"\bmy no\b", r"\bmy number\b", r"\bmy num\b",
    r"\bmy mail\b", r"\bmy email\b", r"\bmy id\b", r"\bmy address\b",
    r"\bmy (?:mobile|cell|phone|whatsapp)\b", r"\bu can reach me\b",
    r"\bim\b",
]

# Cues tying a span to a contact channel.
_CONTACT_CUES: dict[EntityType, List[str]] = {
    EntityType.EMAIL_ADDRESS: [r"\be-?mail\b", r"\bmail\b", r"\bid\b", r"\bwrite to\b", r"\bat\b"],
    EntityType.PHONE_NUMBER: [
        r"\bphone\b", r"\bmobile\b", r"\bno\b", r"\bnumber\b", r"\bnum\b",
        r"\bcall\b", r"\breach\b", r"\bcontact\b", r"\bwhatsapp\b", r"\bcell\b",
        r"\bdial\b",
    ],
    EntityType.ADDRESS: [r"\baddress\b", r"\blive\b", r"\bstay\b", r"\breside\b", r"\bflat\b", r"\bhouse\b"],
    EntityType.LOCATION: [r"\blive\b", r"\bstay\b", r"\bnear\b", r"\bin\b", r"\bat\b", r"\bfrom\b", r"\baddress\b"],
    EntityType.POSTAL_CODE: [r"\bpin\b", r"\bpostal\b", r"\bzip\b", r"\bcode\b"],
    EntityType.IN_AADHAAR: [r"\baadhaar\b", r"\baadhar\b", r"\buid\b", r"\bcard\b"],
    EntityType.IN_PAN: [r"\bpan\b", r"\btax\b", r"\bcard\b"],
}

# Cues that a name is illustrative rather than identifying.
_FICTION_CUES = [
    r"\bcharacter\b", r"\bstory\b", r"\bnovel\b", r"\bmovie\b", r"\bfilm\b",
    r"\bbook\b", r"\bexample\b", r"\bfor instance\b", r"\be\.g\.\b",
    r"\bhypothetically\b", r"\bsuppose\b", r"\bimagine\b", r"\blike\b",
    r"\bsample\b", r"\btest\b", r"\bplaceholder\b", r"\bdemo\b",
]

# Third-person framing: the named person is probably NOT the speaker.
_THIRD_PERSON_CUES = [
    r"\bsaid that\b", r"\btold\b", r"\basked\b", r"\bshould\b", r"\bhim\b",
    r"\bher\b", r"\bthem\b", r"\bnot me\b", r"\bnot my\b", r"\bhis\b",
    r"\bher\b", r"\btheir\b",
]

_NEGATION_CUES = [
    r"\bnot my\b", r"\bisn'?t my\b", r"\bnot mine\b", r"\bthat'?s not me\b",
    r"\bnot me\b", r"\bwrong number\b", r"\bdon'?t call\b", r"\bnever\b",
    r"\bnot real\b",
]

# Common English words that spaCy sometimes tags as PERSON.
_COMMON_WORDS = {
    "the", "a", "an", "this", "that", "these", "those", "there", "here",
    "office", "school", "home", "work", "team", "company", "group", "story",
    "example", "sample", "test", "demo", "hi", "hey", "hello", "yes", "no",
    "ok", "okay", "thanks", "sir", "madam", "ma'am", "please", "lol", "btw",
    "ig", "idk", "imo", "afaik", "tbh", "btwn", "phone", "number", "email",
    "mail", "address", "name", "doctor", "engineer", "manager", "teacher",
    "student", "user", "admin", "guest", "customer", "client", "one", "two",
}

# Titles are not names on their own.
_TITLES = {
    "mr", "mrs", "ms", "miss", "dr", "prof", "sir", "madam", "shri", "smt",
    "sri", "er", "adv", "ca", "capt", "col", "gen", "lt", "sgt", "rev",
}

# Interjections / filler that NER sometimes tags as ORGANIZATION or PERSON.
_NOISE_WORDS = {
    "omg", "hii", "hi", "hey", "hello", "yo", "lol", "btw", "ig", "idk",
    "imo", "tbh", "pls", "plz", "ok", "okay", "hmm", "umm", "yeah", "yep",
    "thanks", "thx", "bye", "omfg", "wtf",
}

# Non-organisation words that NER sometimes tags as ORGANIZATION.
_ORG_BLOCKLIST = {
    "pan", "aadhaar", "aadhar", "uid", "uidai", "email", "mail", "number",
    "phone", "mobile", "name", "address", "order", "invoice", "id", "card",
    "account", "password", "otp", "pin", "code", "no", "num", "whatsapp",
    "story", "example", "sample", "test", "demo", "character", "document",
    "report", "message", "note", "call", "text",
}

# Organisation suffixes; a token ending in one of these is very likely an org.
_ORG_SUFFIXES = (
    "inc", "ltd", "llc", "corp", "corporation", "company", "co", "gmbh",
    "pvt", "limited", "technologies", "systems", "solutions", "labs",
    "university", "institute", "college", "bank", "hospital", "airlines",
)

# Cues indicating the named span is a person being addressed or discussed.
_PERSON_CUES = [
    r"\btold\b", r"\bsaid\b", r"\basked\b", r"\breach\b", r"\bcall\b",
    r"\bcontact\b", r"\bping\b", r"\bmeet\b", r"\bsend (?:it|this|the) to\b",
    r"\bmail (?:it|to)\b", r"\bemail (?:it|to)\b", r"\bmessage\b",
    r"\bname is\b", r"\bnamed\b", r"\bthis is\b",
]

# A standalone road/street reference with no number is not an identifying
# address ("the old school on MG Road"), so it is treated as weak evidence.
_ROAD_ONLY_RE = re.compile(
    r"^(?:[A-Za-z.'\-]+\s+){0,2}(?:road|rd|street|st|lane|ln|marg|highway)$",
    re.IGNORECASE,
)

# Cues that follow an initial-style name such as "R.S." and confirm a person.
_INITIAL_NAME_RE = re.compile(r"\b(?:[A-Za-z]\.\s*){1,4}[A-Za-z]?\.?(?![A-Za-z])")

# Function words that can never start a name captured after a cue. Guards
# against "call me at ..." -> "at" and "reach me on ..." -> "on".
_NON_NAME_WORDS = {
    "at", "on", "in", "to", "for", "from", "by", "with", "via", "is", "as",
    "and", "or", "the", "a", "an", "your", "my", "his", "her", "their", "our",
    "up", "out", "back", "here", "there", "now", "later", "anytime", "today",
    "tomorrow", "please", "pls", "plz", "ok", "okay", "thanks", "you", "u",
    "me", "us", "him", "them", "it", "its", "myself", "yourself",
    "after", "before", "between", "around", "about", "until", "till", "when",
    "if", "so", "then", "only", "just", "again", "soon", "asap", "anytime",
    "morning", "evening", "night", "noon", "midnight", "weekend",
}

# Person verbs/patterns. Kept deliberately narrow (no generic "is"/"are") so
# that "Delhi is a city" or "Apple is a company" are never mistaken for people.
_PERSON_VERBS = r"(?:told|said|asked|messaged|called|texted|will|wants|and\s+her|and\s+his|and\s+their)"

# "Rahul told me ...", "Sneha and her brother ..."
_NAME_BEFORE_VERB_RE = re.compile(
    r"\b([A-Z][a-z]+)\s+" + _PERSON_VERBS + r"\b", re.IGNORECASE
)

# "... told Rahul", "... message Karthik", "... contact Meera"
_VERB_BEFORE_NAME_RE = re.compile(
    r"\b(?:told|asked|messaged|contacted|met|call|reach|message|ping)\s+"
    r"([A-Z][a-z]+)\b",
    re.IGNORECASE,
)

# "... send it to Rahul", "... give this to Meera"
_SEND_TO_NAME_RE = re.compile(
    r"\b(?:send|give|forward|email|mail|hand|pass)\s+"
    r"(?:it|this|that|the\s+\w+)?\s*to\s+([A-Z][a-z]+)\b",
    re.IGNORECASE,
)

# "Rahul is a character/person/...", "X is a character in this story". Kept
# narrow so "Delhi is a city" is not treated as a person.
_NAME_IS_A_RE = re.compile(
    r"\b([A-Z][a-z]+)\s+is\s+(?:a|an)\s+"
    r"(?:character|person|man|woman|boy|girl|student|friend|colleague|guy|"
    r"lady|gentleman|employee|manager|doctor|teacher|writer|singer|actor)\b",
    re.IGNORECASE,
)

# Names the NLP layer misses, introduced by a self-reference cue. Captures up
# to three following word tokens (allowing lowercase, as in "im rahul"); the
# finder trims trailing function/common words and keeps 1-2 name tokens.
_NAME_AFTER_CUE_RE = re.compile(
    r"(?:my\s+n[a-z]*me(?:'?s)?\s+is|i\s*'?m|i\s+am|myself|this\s+is|"
    r"call\s+me|reach\s+me|mail\s+me|email\s+me|name\s*[:\-])\s+"
    r"([A-Za-z][A-Za-z.'\-]*(?:\s+[A-Za-z][A-Za-z.'\-]*){0,2})",
    re.IGNORECASE,
)

# A weak address cue ("i live/stay ...") only counts as an address when the
# following text starts like one: "at <place>", a house number, or a flat/plot.
_ADDRESS_START_RE = re.compile(
    r"^\s*(?:at\b|flat\b|plot\b|house\b|door\b|\d{1,4}\b)",
    re.IGNORECASE,
)


def _looks_like_address_start(text: str, start: int) -> bool:
    return bool(_ADDRESS_START_RE.match(text[start:start + 24]))


# --------------------------------------------------------------------------
# Data structures
# --------------------------------------------------------------------------


@dataclass
class ContextAssessment:
    """Result of contextual analysis for one candidate span."""

    score_delta: float = 0.0
    evidence: List[str] = field(default_factory=list)
    tags: Set[str] = field(default_factory=set)
    force_review: bool = False

    def add(self, delta: float, reason: str, tag: Optional[str] = None) -> None:
        self.score_delta += delta
        self.evidence.append(reason)
        if tag:
            self.tags.add(tag)


@dataclass
class CandidateSpan:
    """A name-like span proposed by the contextual layer itself."""

    start: int
    end: int
    text: str
    entity_type: EntityType
    base_score: float
    evidence: List[str]


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _matches_any(text: str, patterns: List[str]) -> Optional[str]:
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(0)
    return None


def _window(text: str, start: int, end: int, radius: Optional[int] = None) -> str:
    """Return the text window around a span, used for cue proximity."""
    settings = get_settings()
    r = radius if radius is not None else settings.context_window_chars
    lo = max(0, start - r)
    hi = min(len(text), end + r)
    return text[lo:hi]


_EMAIL_OR_URL_RE = re.compile(
    r"(?:https?://\S+|[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})"
)


def _clean_for_cues(window: str) -> str:
    """Remove emails/URLs so cue words inside them don't fire.

    Without this, the domain ``example.com`` would match the illustrative-cue
    word "example" and wrongly penalise an unrelated person name nearby.
    """
    return _EMAIL_OR_URL_RE.sub(" ", window)


def _left_window(text: str, start: int, radius: int) -> str:
    return text[max(0, start - radius):start]


def _right_window(text: str, end: int, radius: int) -> str:
    return text[end:min(len(text), end + radius)]


# --------------------------------------------------------------------------
# The analyzer
# --------------------------------------------------------------------------


class ContextualAnalyzer:
    """Adjusts candidate confidence using surrounding words and sentence intent."""

    def __init__(self) -> None:
        self.settings = get_settings()

    # -- public API --------------------------------------------------------

    def suggest_type(
        self,
        text: str,
        span_text: str,
        entity_type: EntityType,
        start: int,
        end: int,
    ) -> Tuple[Optional[EntityType], List[str]]:
        """Suggest a corrected entity type, or ``None`` to drop the candidate.

        spaCy's small model frequently confuses categories (a person labelled
        ORGANIZATION, a person labelled LOCATION, an interjection labelled
        ORGANIZATION). This applies conservative, cue-driven corrections and
        drops clear noise. It never invents an entity; it only re-labels or
        removes one the other layers proposed.
        """
        evidence: List[str] = []
        tokens = [
            t.lower().rstrip(".")
            for t in re.split(r"[\s.\-]+", span_text)
            if t
        ]
        if not tokens:
            return entity_type, evidence

        left = _clean_for_cues(_left_window(text, start, 40))

        if entity_type is EntityType.ORGANIZATION:
            # Interjections and filler tagged as organisations.
            if len(tokens) == 1 and tokens[0] in _NOISE_WORDS:
                return None, ["dropped: interjection mislabelled as organisation"]
            # Field/attribute nouns tagged as organisations ("my PAN is ...").
            if tokens[-1] in _ORG_BLOCKLIST and not self._has_org_suffix(tokens):
                return None, ["dropped: attribute noun mislabelled as organisation"]
            # A single capitalised token after a person cue is usually a person.
            if (
                len(tokens) == 1
                and not self._has_org_suffix(tokens)
                and _matches_any(left, _PERSON_CUES)
            ):
                return EntityType.PERSON, ["re-typed ORGANIZATION -> PERSON: person cue nearby"]

        if entity_type is EntityType.LOCATION:
            # "I told Ananya", "send it to Rahul" -> a person, not a place.
            # A single-token LOCATION that is not a known place name is usually
            # a person spaCy mislabelled as GPE.
            known_place = len(tokens) == 1 and tokens[0] in INDIAN_LOCATION_NAMES
            near_left = _clean_for_cues(_left_window(text, start, 24))
            person_cue = _matches_any(near_left, _PERSON_CUES)
            if _INITIAL_NAME_RE.fullmatch(span_text.strip()) and not known_place:
                return EntityType.PERSON, ["re-typed LOCATION -> PERSON: initial-style name"]
            if len(tokens) == 1 and not known_place and (
                person_cue or _matches_any(near_left, [r"\bto\b", r"\bfor\b", r"\bwith\b"])
            ):
                return EntityType.PERSON, ["re-typed LOCATION -> PERSON: unknown place after a person cue"]

        if entity_type is EntityType.PERSON:
            # A known place is not a person ("city: Nagpur").
            if len(tokens) == 1 and tokens[0] in INDIAN_LOCATION_NAMES:
                return EntityType.LOCATION, ["re-typed PERSON -> LOCATION: known place name"]
            if len(tokens) == 1 and tokens[0] in _NOISE_WORDS:
                return None, ["dropped: interjection mislabelled as person"]
            if len(tokens) == 1 and tokens[0] in _ORG_BLOCKLIST:
                return None, ["dropped: attribute noun mislabelled as person"]

        return entity_type, evidence

    @staticmethod
    def _has_org_suffix(tokens: List[str]) -> bool:
        return any(t in _ORG_SUFFIXES for t in tokens)

    def assess(
        self,
        text: str,
        span_text: str,
        entity_type: EntityType,
        start: int,
        end: int,
    ) -> ContextAssessment:
        """Assess one candidate span and return a score delta + evidence."""
        result = ContextAssessment()

        if entity_type in (
            EntityType.PERSON,
            EntityType.ORGANIZATION,
            EntityType.LOCATION,
            EntityType.ADDRESS,
        ):
            self._assess_named_entity(text, span_text, entity_type, start, end, result)
        else:
            self._assess_structured_entity(text, entity_type, start, end, result)

        self._assess_negation(text, start, end, result)
        self._clamp(result)
        return result

    def find_cue_based_candidates(
        self,
        text: str,
        existing: List[Tuple[int, int]],
    ) -> List[CandidateSpan]:
        """Propose name candidates the NLP layer missed.

        Only emits spans when a *strong* self-reference or contact cue is
        adjacent, which keeps false positives low. ``existing`` is a list of
        (start, end) already covered by other layers.
        """
        candidates: List[CandidateSpan] = []
        candidates.extend(self._find_initial_names(text, existing))
        candidates.extend(self._find_self_reference_names(text, existing))
        candidates.extend(self._find_person_phrases(text, existing))
        candidates.extend(self._find_address_clauses(text, existing))
        return candidates

    def _find_person_phrases(
        self, text: str, existing: List[Tuple[int, int]]
    ) -> List[CandidateSpan]:
        """Detect person names from common verb/address constructions.

        spaCy's small model misses many Indian and informal names. These three
        narrow, high-precision constructions recover the common ones:
        "X told/said/asked", "told/asked X", "send it to X".
        """
        candidates: List[CandidateSpan] = []
        for regex, label in (
            (_NAME_BEFORE_VERB_RE, "name before a speech verb"),
            (_VERB_BEFORE_NAME_RE, "name after an addressing verb"),
            (_SEND_TO_NAME_RE, "name in a send-to phrase"),
            (_NAME_IS_A_RE, "name before a person noun"),
        ):
            for m in regex.finditer(text):
                span_text = m.group(1)
                start = m.start(1)
                end = m.end(1)
                tokens = [t.lower() for t in re.split(r"\s+", span_text)]
                if any(
                    t in _COMMON_WORDS or t in _NON_NAME_WORDS or t in _NOISE_WORDS
                    for t in tokens
                ):
                    continue
                if self._overlaps((start, end), existing):
                    continue
                candidates.append(
                    CandidateSpan(
                        start=start,
                        end=end,
                        text=span_text,
                        entity_type=EntityType.PERSON,
                        base_score=0.6,
                        evidence=[label],
                    )
                )
        return candidates

    def _find_self_reference_names(
        self, text: str, existing: List[Tuple[int, int]]
    ) -> List[CandidateSpan]:
        """Detect names the NLP layer misses after a self-reference cue.

        This is the key informal-language path: "im rahul", "my name is priya"
        and "this is kavya" are lowercase and unpunctuated, so spaCy's model
        rarely tags them. We anchor on the cue and take the following token(s),
        rejecting anything that is a common word, a title or a channel noun.
        """
        candidates: List[CandidateSpan] = []
        for m in _NAME_AFTER_CUE_RE.finditer(text):
            raw = m.group(1)
            # Trim trailing function/common words and dangling punctuation.
            kept: List[str] = []
            for tok in re.split(r"\s+", raw.strip()):
                bare = tok.lower().rstrip(".")
                if (
                    bare in _NON_NAME_WORDS
                    or bare in _COMMON_WORDS
                    or bare in _NOISE_WORDS
                    or bare in _TITLES
                    or bare in _ORG_BLOCKLIST
                ):
                    break
                kept.append(tok.rstrip("."))
                if len(kept) == 2:
                    break
            if not kept:
                continue
            span_text = " ".join(kept)
            start = m.start(1)
            end = start + len(span_text)
            if end - start < 2:
                continue
            if self._overlaps((start, end), existing):
                continue
            candidates.append(
                CandidateSpan(
                    start=start,
                    end=end,
                    text=span_text,
                    entity_type=EntityType.PERSON,
                    base_score=0.62,
                    evidence=[f"name introduced by self-reference cue: '{m.group(0).strip()}'"],
                )
            )
        return candidates

    def _find_initial_names(
        self, text: str, existing: List[Tuple[int, int]]
    ) -> List[CandidateSpan]:
        """Propose initial-style names ("R.S.") near an addressing cue."""
        candidates: List[CandidateSpan] = []
        for m in _INITIAL_NAME_RE.finditer(text):
            span = m.group(0).strip()
            if len(span) < 2:
                continue
            if self._overlaps((m.start(), m.end()), existing):
                continue
            left = _left_window(text, m.start(), 40)
            if _matches_any(
                left,
                _SELF_REFERENCE_CUES
                + [r"\bname\b", r"\bcall\b", r"\breach\b", r"\bcontact\b", r"\bmail\b"],
            ):
                candidates.append(
                    CandidateSpan(
                        start=m.start(),
                        end=m.start() + len(span),
                        text=span,
                        entity_type=EntityType.PERSON,
                        base_score=0.5,
                        evidence=["initial-style name near an addressing cue"],
                    )
                )
        return candidates

    def _find_address_clauses(
        self, text: str, existing: List[Tuple[int, int]]
    ) -> List[CandidateSpan]:
        """Detect prose addresses introduced by an address cue.

        Free-form addresses ("24, MG road... the one near the old school") are
        not reliably regex-matchable. We anchor on a cue ("my address is",
        "i live", "i stay") and take the following clause up to a sentence
        boundary, trimming trailing filler. This is intentionally conservative
        and its output is always flagged for review.
        """
        candidates: List[CandidateSpan] = []
        # Cue phrases that introduce an address clause.
        #   * "my address is ..." / "address: ..."  -> strong address cue
        #   * "i live/stay/reside ..."              -> only an address when
        #     followed by "at <place>" or a leading house number. "I live in
        #     Pune" is a location, and "I live near the old school" is a vague
        #     reference, neither of which is an address.
        strong_cues = (r"\bmy address is\b", r"\baddress\s*[:\-]\s*")
        weak_cues = (r"\bi live\b", r"\bi stay\b", r"\bi reside\b")

        for cue_re, strong in [(c, True) for c in strong_cues] + [(c, False) for c in weak_cues]:
            for m in re.finditer(cue_re, text, re.IGNORECASE):
                start = m.end()
                while start < len(text) and text[start] in " \t,:-":
                    start += 1
                if not strong and not _looks_like_address_start(text, start):
                    continue
                remainder = text[start:]
                stop = re.search(
                    r"[.!?;]|\b(?:the one|which is|but|however|so that|and then)\b",
                    remainder,
                    re.IGNORECASE,
                )
                end = start + (stop.start() if stop else len(remainder))
                end = self._trim_span(text, start, end)
                if end - start < 4:
                    continue
                if self._overlaps((start, end), existing):
                    continue
                candidates.append(
                    CandidateSpan(
                        start=start,
                        end=end,
                        text=text[start:end],
                        entity_type=EntityType.ADDRESS,
                        base_score=0.5,
                        evidence=[f"address clause introduced by '{m.group(0).strip()}'"],
                    )
                )
        return candidates

    @staticmethod
    def _trim_span(text: str, start: int, end: int) -> int:
        """Trim trailing whitespace and dangling punctuation from a span."""
        while end > start and text[end - 1] in " \t,;:-.":
            end -= 1
        return end

    # -- named entity assessment ------------------------------------------

    def _assess_named_entity(
        self,
        text: str,
        span_text: str,
        entity_type: EntityType,
        start: int,
        end: int,
        result: ContextAssessment,
    ) -> None:
        left = _clean_for_cues(_left_window(text, start, 48))
        right = _clean_for_cues(_right_window(text, end, 32))
        whole = _clean_for_cues(_window(text, start, end))

        # 1. Self-reference raises confidence substantially.
        cue = _matches_any(left, _SELF_REFERENCE_CUES)
        if cue:
            result.add(+0.25, f"self-reference cue before span: '{cue.strip()}'", "self_reference")
            result.add(+0.05, "first-person framing suggests the speaker's own identifier", "first_person")

        # 2. Fiction / illustrative framing lowers confidence.
        fiction = _matches_any(whole, _FICTION_CUES)
        if fiction:
            result.add(-0.20, f"illustrative framing nearby: '{fiction.strip()}'", "fiction")

        # 3. Third-person framing lowers confidence slightly (may not be the user).
        third = _matches_any(left + " " + right, _THIRD_PERSON_CUES)
        if third and not cue:
            result.add(-0.10, f"third-person framing nearby: '{third.strip()}'", "third_person")

        # 4. Contact-channel cue for this entity type raises confidence.
        cues = _CONTACT_CUES.get(entity_type)
        if cues:
            contact = _matches_any(whole, cues)
            if contact:
                result.add(+0.10, f"contact cue near span: '{contact.strip()}'", "contact_cue")

        # 5. Location-specific: proximity cues like "near", "on", "in". A bare
        # road name with no house number is a weak, non-identifying reference
        # ("the old school on MG Road"), so it is demoted instead of boosted.
        road_only = entity_type is EntityType.LOCATION and bool(
            _ROAD_ONLY_RE.match(span_text.strip())
        )
        if road_only:
            result.add(
                -0.40,
                "bare road name without a number is not an identifying address",
                "road_only",
            )
            result.force_review = True
        elif entity_type in (EntityType.LOCATION, EntityType.ADDRESS):
            if _matches_any(left, [r"\bnear\b", r"\bon\b", r"\bin\b", r"\bat\b", r"\blive\b", r"\baddress\b"]):
                result.add(+0.10, "locative preposition or address cue near span", "locative")

        # 6. Capitalisation / common-word guard for people.
        if entity_type is EntityType.PERSON:
            self._guard_person(span_text, result)

    def _guard_person(self, span_text: str, result: ContextAssessment) -> None:
        tokens = [t for t in re.split(r"[\s\.\-]+", span_text) if t]
        if not tokens:
            return
        lowered = [t.lower() for t in tokens]

        # Spans containing digits are not names ("Order 4587", "R2D2").
        if any(ch.isdigit() for ch in span_text):
            result.add(-0.35, "span contains digits, unlikely to be a person name", "has_digits")
            result.force_review = True
            return

        # A single bare title ("Mr.") is not a person on its own.
        if len(tokens) == 1 and lowered[0].rstrip(".") in _TITLES:
            result.add(-0.30, "span is a bare title, not a name", "title_only")
            result.force_review = True
            return

        # A single common English word capitalised mid-sentence is weak evidence.
        if len(tokens) == 1 and lowered[0] in _COMMON_WORDS:
            result.add(-0.30, "span is a common word, likely not a person", "common_word")
            result.force_review = True
            return

        # A single function word ("at", "on") is never a name.
        if len(tokens) == 1 and lowered[0] in _NON_NAME_WORDS:
            result.add(-0.40, "span is a function word, not a name", "function_word")
            result.force_review = True
            return

        # Very short single tokens (1-2 chars) are usually initials or noise.
        if len(tokens) == 1 and len(tokens[0]) <= 2:
            result.add(-0.10, "span is very short, ambiguous as a name", "short_token")
            result.force_review = True

    # -- structured entity assessment -------------------------------------

    def _assess_structured_entity(
        self,
        text: str,
        entity_type: EntityType,
        start: int,
        end: int,
        result: ContextAssessment,
    ) -> None:
        whole = _window(text, start, end, radius=40)
        cues = _CONTACT_CUES.get(entity_type)
        if cues:
            cue = _matches_any(whole, cues)
            if cue:
                result.add(+0.15, f"channel cue near {entity_type.value}: '{cue.strip()}'", "contact_cue")
            else:
                result.add(-0.10, f"no channel cue near {entity_type.value}", "no_cue")

    # -- negation (applies to all) ----------------------------------------

    def _assess_negation(
        self, text: str, start: int, end: int, result: ContextAssessment
    ) -> None:
        whole = _window(text, start, end, radius=30)
        neg = _matches_any(whole, _NEGATION_CUES)
        if neg:
            result.add(-0.15, f"negation nearby: '{neg.strip()}'", "negation")
            result.force_review = True

    # -- utilities ---------------------------------------------------------

    def _clamp(self, result: ContextAssessment) -> None:
        if result.score_delta > self.settings.context_max_boost:
            result.score_delta = self.settings.context_max_boost
        if result.score_delta < -self.settings.context_max_penalty:
            result.score_delta = -self.settings.context_max_penalty

    @staticmethod
    def _overlaps(span: Tuple[int, int], others: List[Tuple[int, int]]) -> bool:
        s, e = span
        return any(s < oe and os < e for os, oe in others)


__all__ = ["ContextualAnalyzer", "ContextAssessment", "CandidateSpan"]
