"""Hybrid detection orchestrator.

Combines the four detection layers into a single pipeline:

    Layer A  structured patterns        (patterns.py  -> Presidio)
    Layer B  NLP named entities         (nlp.py       -> spaCy)
    Layer C  contextual adjustment      (contextual.py)
    Layer D  confidence + overlaps      (confidence.py)

The orchestrator is deliberately thin: each layer owns its own logic so it can
be tested and replaced independently.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from app.config import get_settings
from app.detection.confidence import finalize_entity, resolve_overlaps, score_to_level
from app.detection.contextual import ContextualAnalyzer
from app.detection.nlp import analyze_with_presidio, run_spacy_ner
from app.models.schemas import (
    AnalyzeResponse,
    DetectedEntity,
    DetectionMethod,
    EntitySummary,
    EntityType,
)

# Entity types spaCy/Presidio may emit that we map or drop.
_PRESIDIO_TO_CIPHERFORGE = {
    "EMAIL_ADDRESS": EntityType.EMAIL_ADDRESS,
    "PHONE_NUMBER": EntityType.PHONE_NUMBER,
    "URL": EntityType.URL,
    "IP_ADDRESS": EntityType.IP_ADDRESS,
    "POSTAL_CODE": EntityType.POSTAL_CODE,
    "ADDRESS": EntityType.ADDRESS,
    "IN_AADHAAR": EntityType.IN_AADHAAR,
    "IN_PAN": EntityType.IN_PAN,
    "CREDIT_CARD": EntityType.CREDIT_CARD,
    "PERSON": EntityType.PERSON,
    "LOCATION": EntityType.LOCATION,
    "ORGANIZATION": EntityType.ORGANIZATION,
}


@dataclass
class _Candidate:
    entity_type: EntityType
    start: int
    end: int
    text: str
    score: float
    method: DetectionMethod
    recognizer: str
    evidence: List[str]
    force_review: bool = False


class PiiAnalyzer:
    """The CipherForge context-aware PII analyzer."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.contextual = ContextualAnalyzer()

    # -- public API --------------------------------------------------------

    def analyze(
        self,
        text: str,
        language: str = "en",
        entities: Optional[Sequence[EntityType]] = None,
        score_threshold: Optional[float] = None,
        include_uncertain: bool = True,
    ) -> AnalyzeResponse:
        threshold = (
            score_threshold
            if score_threshold is not None
            else self.settings.default_score_threshold
        )
        allowed = set(entities) if entities else None

        candidates = self._collect_candidates(text, language, allowed)
        detected = self._score_and_finalize(candidates, text, threshold, include_uncertain)

        if allowed is not None:
            detected = [d for d in detected if d.entity_type in allowed]

        detected = resolve_overlaps(detected)

        return AnalyzeResponse(
            text_length=len(text),
            entities=detected,
            summary=_summarize(detected),
            uncertain_count=sum(1 for d in detected if d.needs_review),
            engine_version=self.settings.engine_version,
        )

    def analyze_entities(self, text: str, **kwargs) -> List[DetectedEntity]:
        """Convenience wrapper returning only the entity list."""
        return self.analyze(text, **kwargs).entities

    # -- layer collection --------------------------------------------------

    def _collect_candidates(
        self,
        text: str,
        language: str,
        allowed: Optional[set],
    ) -> List[_Candidate]:
        candidates: List[_Candidate] = []
        candidates.extend(self._layer_a(text, language))
        candidates.extend(self._layer_b(text))
        candidates.extend(self._layer_c_extra(text, candidates))
        return candidates

    def _layer_a(self, text: str, language: str) -> List[_Candidate]:
        """Layer A: structured pattern matches via Presidio recognisers."""
        results = analyze_with_presidio(text, language=language, score_threshold=0.0)
        out: List[_Candidate] = []
        for r in results:
            mapped = _PRESIDIO_TO_CIPHERFORGE.get(r.entity_type)
            if mapped is None:
                continue
            name = "Presidio"
            meta = getattr(r, "recognition_metadata", None) or {}
            if meta.get("recognizer_name"):
                name = meta["recognizer_name"]
            out.append(
                _Candidate(
                    entity_type=mapped,
                    start=r.start,
                    end=r.end,
                    text=text[r.start:r.end],
                    score=r.score,
                    method=DetectionMethod.PATTERN,
                    recognizer=name,
                    evidence=[],
                )
            )
        return out

    def _layer_b(self, text: str) -> List[_Candidate]:
        """Layer B: spaCy NER."""
        out: List[_Candidate] = []
        for r in run_spacy_ner(text):
            mapped = _PRESIDIO_TO_CIPHERFORGE.get(r.entity_type)
            if mapped is None:
                continue
            out.append(
                _Candidate(
                    entity_type=mapped,
                    start=r.start,
                    end=r.end,
                    text=text[r.start:r.end],
                    score=r.score,
                    method=DetectionMethod.NLP,
                    recognizer="SpacyNer",
                    evidence=[],
                )
            )
        return out

    def _layer_c_extra(self, text: str, existing: List[_Candidate]) -> List[_Candidate]:
        """Layer C: propose name candidates the NLP layer missed."""
        covered = [(c.start, c.end) for c in existing]
        out: List[_Candidate] = []
        for cand in self.contextual.find_cue_based_candidates(text, covered):
            out.append(
                _Candidate(
                    entity_type=cand.entity_type,
                    start=cand.start,
                    end=cand.end,
                    text=cand.text,
                    score=cand.base_score,
                    method=DetectionMethod.CONTEXT,
                    recognizer="ContextualCandidate",
                    evidence=list(cand.evidence),
                )
            )
        return out

    # -- scoring -----------------------------------------------------------

    def _score_and_finalize(
        self,
        candidates: List[_Candidate],
        text: str,
        threshold: float,
        include_uncertain: bool,
    ) -> List[DetectedEntity]:
        # Merge duplicate spans first (e.g. Presidio + spaCy agreeing on a name).
        merged = _merge_duplicates(candidates)

        finalized: List[DetectedEntity] = []
        for c in merged:
            # Layer C: allow the contextual layer to re-label or drop a
            # candidate the NLP layer proposed (spaCy confuses categories).
            new_type, type_evidence = self.contextual.suggest_type(
                text, c.text, c.entity_type, c.start, c.end
            )
            if new_type is None:
                continue
            c.entity_type = new_type

            assessment = self.contextual.assess(
                text, c.text, c.entity_type, c.start, c.end
            )
            adjusted = c.score + assessment.score_delta
            evidence = list(c.evidence) + type_evidence + assessment.evidence
            method = c.method
            if assessment.score_delta != 0:
                method = (
                    DetectionMethod.COMBINED
                    if c.method is not DetectionMethod.CONTEXT
                    else DetectionMethod.CONTEXT
                )

            entity = finalize_entity(
                entity_type=c.entity_type.value,
                start=c.start,
                end=c.end,
                text=c.text,
                score=adjusted,
                detection_method=method.value,
                recognizer=c.recognizer,
                evidence=evidence,
                force_review=c.force_review or assessment.force_review,
            )

            # The threshold is a hard floor: a span the contextual layer pushed
            # below it is treated as "not sensitive", not merely uncertain.
            if entity.score < threshold:
                continue
            # When asked to exclude uncertain detections, drop the low bucket.
            if not include_uncertain and entity.confidence_level.value == "uncertain":
                continue
            finalized.append(entity)

        return finalized


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _merge_duplicates(candidates: List[_Candidate]) -> List[_Candidate]:
    """Merge candidates sharing an identical span, keeping the best score.

    When two layers agree on the exact same span we combine their evidence and
    take the higher score (agreement is itself evidence).
    """
    by_span: Dict[tuple, _Candidate] = {}
    for c in candidates:
        key = (c.start, c.end, c.entity_type)
        existing = by_span.get(key)
        if existing is None:
            by_span[key] = c
            continue
        # Same span + type from two layers: keep higher score, merge evidence.
        if c.score > existing.score:
            existing.score = c.score
            existing.recognizer = f"{existing.recognizer}+{c.recognizer}"
        existing.evidence.extend(e for e in c.evidence if e not in existing.evidence)
        if c.method is not existing.method:
            existing.method = DetectionMethod.COMBINED
    return list(by_span.values())


def _summarize(entities: List[DetectedEntity]) -> List[EntitySummary]:
    counts: Dict[str, List[float]] = {}
    for e in entities:
        key = e.entity_type.value
        counts.setdefault(key, []).append(e.score)
    return [
        EntitySummary(
            entity_type=EntityType(key),
            count=len(scores),
            max_score=round(max(scores), 4),
        )
        for key, scores in sorted(counts.items())
    ]


__all__ = ["PiiAnalyzer"]
