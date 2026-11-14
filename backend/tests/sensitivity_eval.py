"""Sensitivity-classification evaluation harness.

This is deliberately separate from ``tests/evaluation.py`` (which measures
*entity detection*). Here we measure the second, distinct question: given the
entities, did the engine decide PROTECT / KEEP / REVIEW correctly?

Reported metrics
----------------
* Entity detection P/R/F1 (span-level, same rule as the main harness).
* Sensitivity classification P/R/F1 (3-way: PROTECT / KEEP / REVIEW).
* False-positive anonymisation rate -- KEEP spans that were wrongly anonymised.
* False-negative protection rate -- PROTECT spans that were wrongly left visible.
* REVIEW decision frequency -- how often the engine declines to decide.

Honesty note
------------
Synthetic data, written alongside the rules. Indicative, not a claim of
real-world accuracy.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

FIXTURES = Path(__file__).resolve().parent / "fixtures"
DEFAULT_SENSITIVITY_DATASET = FIXTURES / "sensitivity_dataset.json"

_CLASSES = ("PROTECT", "KEEP", "REVIEW")


def load_sensitivity_dataset(path: Optional[Path] = None) -> dict:
    return json.loads((path or DEFAULT_SENSITIVITY_DATASET).read_text(encoding="utf-8"))


@dataclass
class Prf:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if (self.tp + self.fp) else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if (self.tp + self.fn) else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    def as_dict(self) -> dict:
        return {
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
        }


@dataclass
class SensitivityReport:
    detection: Prf = field(default_factory=Prf)
    per_class: Dict[str, Prf] = field(default_factory=dict)
    keep_spans: int = 0
    keep_anonymized: int = 0
    protect_spans: int = 0
    protect_leaked: int = 0
    detected_entities: int = 0
    review_entities: int = 0
    # Full per-example rows, so the Workbench can show what went wrong and why.
    rows: List[dict] = field(default_factory=list)

    @property
    def micro(self) -> Prf:
        m = Prf()
        for p in self.per_class.values():
            m.tp += p.tp
            m.fp += p.fp
            m.fn += p.fn
        return m

    @property
    def macro_f1(self) -> float:
        f1s = [self.per_class.get(c, Prf()).f1 for c in _CLASSES]
        return sum(f1s) / len(f1s)

    @property
    def false_positive_anonymisation_rate(self) -> float:
        return self.keep_anonymized / self.keep_spans if self.keep_spans else 0.0

    @property
    def false_negative_protection_rate(self) -> float:
        return self.protect_leaked / self.protect_spans if self.protect_spans else 0.0

    @property
    def review_frequency(self) -> float:
        return self.review_entities / self.detected_entities if self.detected_entities else 0.0

    def as_dict(self) -> dict:
        return {
            "detection": self.detection.as_dict(),
            "sensitivity_micro": self.micro.as_dict(),
            "sensitivity_per_class": {
                c: self.per_class.get(c, Prf()).as_dict() for c in _CLASSES
            },
            "sensitivity_macro_f1": round(self.macro_f1, 4),
            "false_positive_anonymisation_rate": round(
                self.false_positive_anonymisation_rate, 4
            ),
            "false_negative_protection_rate": round(
                self.false_negative_protection_rate, 4
            ),
            "review_frequency": round(self.review_frequency, 4),
            "counts": {
                "keep_spans": self.keep_spans,
                "keep_anonymized": self.keep_anonymized,
                "protect_spans": self.protect_spans,
                "protect_leaked": self.protect_leaked,
                "detected_entities": self.detected_entities,
                "review_entities": self.review_entities,
            },
        }


def _overlaps(a: Tuple[int, int], b: Tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def evaluate_sensitivity(analyzer, anonymizer, dataset: Optional[dict] = None) -> SensitivityReport:
    """Run detection + sensitivity + anonymisation over the regression set."""
    dataset = dataset or load_sensitivity_dataset()
    report = SensitivityReport()
    for c in _CLASSES:
        report.per_class[c] = Prf()

    for example in dataset["examples"]:
        text = example["text"]
        gold = example["entities"]
        analysis = analyzer.analyze(text)
        preds = analysis.entities
        result = anonymizer.anonymize(text, preds)

        # Map each replacement (by span) to its action, for FP/FN rates.
        action_by_span = {(r.start, r.end): r.action for r in result.replacements}

        matched_gold: set = set()
        matched_pred: set = set()

        # -- detection (span + type overlap) ------------------------------
        for pi, p in enumerate(preds):
            for gi, g in enumerate(gold):
                if gi in matched_gold:
                    continue
                if p.entity_type.value == g["entity_type"] and _overlaps(
                    (p.start, p.end), (g["start"], g["end"])
                ):
                    matched_gold.add(gi)
                    matched_pred.add(pi)
                    report.detection.tp += 1
                    break
        for pi in range(len(preds)):
            if pi not in matched_pred:
                report.detection.fp += 1
        for gi in range(len(gold)):
            if gi not in matched_gold:
                report.detection.fn += 1

        # -- sensitivity classification on detected gold spans -------------
        gold_decision: Dict[int, str] = {}
        for gi, g in enumerate(gold):
            gold_decision[gi] = g["expected_sensitivity"]

        predicted_decision: Dict[int, str] = {}
        for p in preds:
            for gi, g in enumerate(gold):
                # Sensitivity is a property of the *span*, so match by overlap
                # regardless of the entity label. Label correctness is measured
                # separately by the detection metrics above.
                if gi in predicted_decision:
                    continue
                if _overlaps((p.start, p.end), (g["start"], g["end"])):
                    predicted_decision[gi] = p.sensitivity.value

        for gi, g in enumerate(gold):
            expected = gold_decision[gi]
            if expected == "PROTECT":
                report.protect_spans += 1
            elif expected == "KEEP":
                report.keep_spans += 1

            # FP anonymisation: a KEEP span whose text was actually replaced.
            replaced = any(
                r.replaced_original and _overlaps((r.start, r.end), (g["start"], g["end"]))
                for r in result.replacements
            )
            if expected == "KEEP" and replaced:
                report.keep_anonymized += 1
            if expected == "PROTECT" and not replaced:
                report.protect_leaked += 1

            predicted = predicted_decision.get(gi)
            if predicted is None:
                # Not detected -> counts as a miss for the sensitivity metric.
                report.per_class[expected].fn += 1
                continue
            if predicted == expected:
                report.per_class[expected].tp += 1
            else:
                report.per_class[expected].fn += 1
                report.per_class[predicted].fp += 1

        # -- review frequency + example row -------------------------------
        for p in preds:
            report.detected_entities += 1
            if p.sensitivity.value == "REVIEW":
                report.review_entities += 1

        report.rows.append(
            {
                "id": example["id"],
                "text": text,
                "sanitized_text": result.sanitized_text,
                "gold": [
                    {
                        "text": g["text"],
                        "entity_type": g["entity_type"],
                        "expected_sensitivity": g["expected_sensitivity"],
                        "note": g.get("note", ""),
                    }
                    for g in gold
                ],
                "predicted": [
                    {
                        "text": p.text,
                        "entity_type": p.entity_type.value,
                        "sensitivity": p.sensitivity.value,
                        "sensitivity_reason": p.sensitivity_reason,
                        "sensitivity_rationale": p.sensitivity_rationale,
                        "score": p.score,
                        "needs_review": p.needs_review,
                        "action": action_by_span.get((p.start, p.end), "unknown"),
                    }
                    for p in preds
                ],
            }
        )

    return report


def format_report(report: SensitivityReport) -> str:
    lines = ["=" * 68, "CipherForge sensitivity classification (synthetic set)", "=" * 68]
    d = report.detection.as_dict()
    lines.append(
        f"ENTITY DETECTION      P={d['precision']:.3f} R={d['recall']:.3f} "
        f"F1={d['f1']:.3f} (tp={d['tp']} fp={d['fp']} fn={d['fn']})"
    )
    m = report.micro.as_dict()
    lines.append(
        f"SENSITIVITY (micro)   P={m['precision']:.3f} R={m['recall']:.3f} "
        f"F1={m['f1']:.3f}  macro-F1={report.macro_f1:.3f}"
    )
    lines.append("")
    lines.append(f"{'class':<10}{'P':>7}{'R':>7}{'F1':>7}{'tp':>6}{'fp':>6}{'fn':>6}")
    for c in _CLASSES:
        pc = report.per_class.get(c, Prf()).as_dict()
        lines.append(
            f"{c:<10}{pc['precision']:>7.3f}{pc['recall']:>7.3f}{pc['f1']:>7.3f}"
            f"{pc['tp']:>6}{pc['fp']:>6}{pc['fn']:>6}"
        )
    lines.append("")
    lines.append(
        f"FP anonymisation rate  = {report.false_positive_anonymisation_rate:.3f} "
        f"({report.keep_anonymized}/{report.keep_spans} KEEP spans anonymised)"
    )
    lines.append(
        f"FN protection rate     = {report.false_negative_protection_rate:.3f} "
        f"({report.protect_leaked}/{report.protect_spans} PROTECT spans left visible)"
    )
    lines.append(
        f"REVIEW frequency       = {report.review_frequency:.3f} "
        f"({report.review_entities}/{report.detected_entities} detected entities)"
    )
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover
    from app.anonymization.engine import AnonymizationEngine
    from app.detection.analyzer import PiiAnalyzer

    rep = evaluate_sensitivity(PiiAnalyzer(), AnonymizationEngine())
    print(format_report(rep))
    print("\nper-example:")
    for row in rep.rows:
        print(f"  [{row['id']}] {row['text']!r}")
        print(f"      -> {row['sanitized_text']!r}")
        for g in row["gold"]:
            print(f"      gold  {g['expected_sensitivity']:<7} {g['entity_type']:<14} {g['text']!r}")
        for p in row["predicted"]:
            print(
                f"      pred  {p['sensitivity']:<7} {p['entity_type']:<14} {p['text']!r} "
                f"({p['sensitivity_reason']}, action={p['action']})"
            )
