"""Evaluation harness for CipherForge.

Computes span-level precision / recall / F1 against the synthetic gold set.

Matching rule
-------------
A prediction matches a gold span when the entity types are equal AND the spans
overlap (partial credit is standard in NER evaluation). Exact-boundary matches
are also reported so the reader can see how often the engine gets the boundary
exactly right.

Important honesty note
----------------------
These are measured numbers on a *synthetic* dataset written by the same person
who wrote the recognisers. They are indicative, not a claim of real-world
performance. See ``docs/LIMITATIONS.md``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple

FIXTURES = Path(__file__).resolve().parent / "fixtures"
DEFAULT_DATASET = FIXTURES / "synthetic_dataset.json"


@dataclass
class Metrics:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    exact_tp: int = 0

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
            "exact_match_rate": round(self.exact_tp / self.tp, 4) if self.tp else 0.0,
        }


@dataclass
class EvalReport:
    overall: Metrics
    per_type: Dict[str, Metrics] = field(default_factory=dict)
    per_category: Dict[str, Metrics] = field(default_factory=dict)
    failures: List[dict] = field(default_factory=list)
    false_positives: List[dict] = field(default_factory=list)
    uncertain_flags: List[dict] = field(default_factory=list)


def load_dataset(path: Path | None = None) -> dict:
    return json.loads((path or DEFAULT_DATASET).read_text(encoding="utf-8"))


def _overlaps(a: Tuple[int, int], b: Tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def evaluate(analyzer, dataset: dict | None = None) -> EvalReport:
    """Run the analyzer over the dataset and compute metrics.

    ``analyzer`` must expose ``analyze(text, **kwargs) -> AnalyzeResponse``.
    """
    dataset = dataset or load_dataset()
    overall = Metrics()
    per_type: Dict[str, Metrics] = {}
    per_category: Dict[str, Metrics] = {}
    failures: List[dict] = []
    false_positives: List[dict] = []
    uncertain_flags: List[dict] = []

    for example in dataset["examples"]:
        text = example["text"]
        gold = [(g["start"], g["end"], g["entity_type"], g["text"]) for g in example["entities"]]
        result = analyzer.analyze(text)
        preds = [(p.start, p.end, p.entity_type.value, p.text, p.needs_review) for p in result.entities]

        matched_gold = set()
        matched_pred = set()
        for pi, (ps, pe, ptype, ptext, _review) in enumerate(preds):
            for gi, (gs, ge, gtype, gtext) in enumerate(gold):
                if gi in matched_gold:
                    continue
                if ptype == gtype and _overlaps((ps, pe), (gs, ge)):
                    matched_gold.add(gi)
                    matched_pred.add(pi)
                    overall.tp += 1
                    _bump(per_type, ptype).tp += 1
                    if (ps, pe) == (gs, ge):
                        overall.exact_tp += 1
                        _bump(per_type, ptype).exact_tp += 1
                    for cat in example["categories"]:
                        _bump(per_category, cat).tp += 1
                    if _review:
                        uncertain_flags.append(
                            {"id": example["id"], "text": ptext, "type": ptype}
                        )
                    break

        for pi, (ps, pe, ptype, ptext, _r) in enumerate(preds):
            if pi in matched_pred:
                continue
            overall.fp += 1
            _bump(per_type, ptype).fp += 1
            for cat in example["categories"]:
                _bump(per_category, cat).fp += 1
            false_positives.append(
                {"id": example["id"], "type": ptype, "text": ptext,
                 "context": text[max(0, ps - 15):pe + 15]}
            )

        for gi, (gs, ge, gtype, gtext) in enumerate(gold):
            if gi in matched_gold:
                continue
            overall.fn += 1
            _bump(per_type, gtype).fn += 1
            for cat in example["categories"]:
                _bump(per_category, cat).fn += 1
            failures.append(
                {"id": example["id"], "type": gtype, "text": gtext,
                 "context": text[max(0, gs - 15):ge + 15]}
            )

    return EvalReport(
        overall=overall,
        per_type=per_type,
        per_category=per_category,
        failures=failures,
        false_positives=false_positives,
        uncertain_flags=uncertain_flags,
    )


def _bump(d: Dict[str, Metrics], key: str) -> Metrics:
    if key not in d:
        d[key] = Metrics()
    return d[key]


def format_report(report: EvalReport) -> str:
    lines = ["=" * 68, "CipherForge evaluation (synthetic dataset)", "=" * 68]
    o = report.overall.as_dict()
    lines.append(
        f"OVERALL  P={o['precision']:.3f}  R={o['recall']:.3f}  F1={o['f1']:.3f}  "
        f"(tp={o['tp']} fp={o['fp']} fn={o['fn']} exact={o['exact_match_rate']:.3f})"
    )
    lines.append("")
    lines.append(f"{'entity type':<16}{'P':>7}{'R':>7}{'F1':>7}{'tp':>6}{'fp':>6}{'fn':>6}")
    for etype, m in sorted(report.per_type.items()):
        d = m.as_dict()
        lines.append(
            f"{etype:<16}{d['precision']:>7.3f}{d['recall']:>7.3f}{d['f1']:>7.3f}"
            f"{d['tp']:>6}{d['fp']:>6}{d['fn']:>6}"
        )
    lines.append("")
    lines.append("Per category:")
    for cat, m in sorted(report.per_category.items()):
        d = m.as_dict()
        lines.append(
            f"  {cat:<24} P={d['precision']:.3f} R={d['recall']:.3f} F1={d['f1']:.3f}"
        )
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover
    from app.detection.analyzer import PiiAnalyzer

    rep = evaluate(PiiAnalyzer())
    print(format_report(rep))
    print(f"\nmissed spans ({len(rep.failures)}):")
    for f in rep.failures:
        print(f"  [{f['id']}] {f['type']:<14} {f['text']!r}  ...{f['context']!r}")
    print(f"\nfalse positives ({len(rep.false_positives)}):")
    for f in rep.false_positives:
        print(f"  [{f['id']}] {f['type']:<14} {f['text']!r}  ...{f['context']!r}")
