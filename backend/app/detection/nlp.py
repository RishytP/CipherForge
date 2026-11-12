"""Layer B: NLP-based entity recognition.

Wraps spaCy and Presidio. spaCy provides statistical NER for less structured
entities (people, organisations, places). Presidio orchestrates the recognisers
and supplies context-aware scoring for pattern recognisers.

Important: installing an NLP library is not the same as understanding context.
spaCy's NER is a statistical model; it will mislabel capitalised words, titles
and abbreviations. Layer C (``contextual.py``) is what actually decides whether
an NER hit is likely to identify a real subject, so NLP scores here are kept
deliberately modest and are always passed through contextual adjustment.
"""

from __future__ import annotations

from functools import lru_cache
from typing import List, Optional

from presidio_analyzer import AnalyzerEngine, RecognizerResult
from presidio_analyzer.nlp_engine import NlpEngineProvider, SpacyNlpEngine

from app.config import get_settings
from app.detection.patterns import build_recognizers
from app.models.schemas import EntityType

# spaCy entity label -> CipherForge entity type
_SPACY_LABEL_MAP = {
    "PERSON": EntityType.PERSON,
    "ORG": EntityType.ORGANIZATION,
    "GPE": EntityType.LOCATION,
    "LOC": EntityType.LOCATION,
    "FAC": EntityType.LOCATION,
    "NORP": EntityType.OTHER,
}


@lru_cache
def get_nlp_engine() -> SpacyNlpEngine:
    """Build (once) the spaCy NLP engine used by Presidio."""
    settings = get_settings()
    provider = NlpEngineProvider(
        nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [
                {"lang_code": "en", "model_name": settings.spacy_model},
            ],
        }
    )
    return provider.create_engine()


@lru_cache
def get_analyzer() -> AnalyzerEngine:
    """Build (once) a curated Presidio analyzer engine.

    We intentionally do NOT use Presidio's default recogniser registry: it
    produced false positives such as ``UK_NHS`` for Indian mobile numbers and
    its loader conflicts with our recogniser constructors. Instead we build a
    registry containing only our curated Layer A recognisers.
    """
    settings = get_settings()
    languages = list(settings.supported_languages)

    from presidio_analyzer.recognizer_registry import RecognizerRegistry

    recognizers = build_recognizers()

    registry = RecognizerRegistry(
        recognizers=recognizers,
        supported_languages=languages,
    )

    engine = AnalyzerEngine(
        registry=registry,
        nlp_engine=get_nlp_engine(),
        supported_languages=languages,
    )
    return engine


def analyze_with_presidio(
    text: str,
    language: str = "en",
    entities: Optional[List[str]] = None,
    score_threshold: float = 0.0,
) -> List[RecognizerResult]:
    """Run the Presidio engine (Layer A + URL) over ``text``."""
    analyzer = get_analyzer()
    kwargs = dict(
        text=text,
        language=language,
        score_threshold=score_threshold,
        return_decision_process=True,
    )
    if entities:
        kwargs["entities"] = entities
    return analyzer.analyze(**kwargs)


def run_spacy_ner(text: str) -> List[RecognizerResult]:
    """Run raw spaCy NER and map labels to CipherForge entity types.

    Scores are fixed per label (see ``config.nlp_*_base_score``) because spaCy's
    ``en_core_web_sm`` does not expose calibrated per-entity probabilities. The
    real discrimination happens in the contextual layer.
    """
    settings = get_settings()
    nlp_engine = get_nlp_engine()
    doc = nlp_engine.nlp[settings.supported_languages[0]](text)

    results: List[RecognizerResult] = []
    for ent in doc.ents:
        mapped = _SPACY_LABEL_MAP.get(ent.label_)
        if mapped is None:
            continue
        if mapped is EntityType.PERSON:
            base = settings.nlp_person_base_score
        elif mapped is EntityType.ORGANIZATION:
            base = settings.nlp_org_base_score
        elif mapped is EntityType.LOCATION:
            base = settings.nlp_location_base_score
        else:
            base = 0.4
        results.append(
            RecognizerResult(
                entity_type=mapped.value,
                start=ent.start_char,
                end=ent.end_char,
                score=base,
                analysis_explanation=None,
                recognition_metadata={
                    RecognizerResult.RECOGNIZER_NAME_KEY: "SpacyNer",
                    RecognizerResult.RECOGNIZER_IDENTIFIER_KEY: "spacy_ner",
                    "spacy_label": ent.label_,
                },
            )
        )
    return results


def spacy_token_tags(text: str):
    """Return the spaCy ``Doc`` for a text (used by the contextual layer)."""
    settings = get_settings()
    nlp_engine = get_nlp_engine()
    return nlp_engine.nlp[settings.supported_languages[0]](text)
