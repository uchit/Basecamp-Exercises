"""Composite scorer + schema-aware graders.

Weights (defensible to a CFO):
  30%  source coverage      — % of answers citing ≥1 source
  30%  confidence index     — weighted average: high=1.0, medium=0.6, low=0.2
  25%  citation grounding   — % of answers with fully_grounded=true (from verifier)
  15%  reviewer-clean       — 1.0 if zero blockers, scaled down by issue count
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Composite:
    source_coverage: float        # 0..1
    confidence_index: float       # 0..1
    grounding_rate: float         # 0..1
    reviewer_clean: float         # 0..1
    score: float                  # 0..100

    def as_dict(self) -> dict:
        return {
            "source_coverage": round(self.source_coverage * 100, 1),
            "confidence_index": round(self.confidence_index * 100, 1),
            "grounding_rate": round(self.grounding_rate * 100, 1),
            "reviewer_clean": round(self.reviewer_clean * 100, 1),
            "score": round(self.score, 1),
        }


_CONF_WEIGHTS = {"high": 1.0, "medium": 0.6, "low": 0.2}


def composite(answers: list[dict], review: dict) -> Composite:
    n = len(answers) or 1

    with_sources = sum(1 for a in answers if a.get("sources"))
    source_coverage = with_sources / n

    confidence_index = (
        sum(_CONF_WEIGHTS.get(a.get("confidence", "low"), 0.0) for a in answers) / n
    )

    grounded = sum(1 for a in answers
                   if (a.get("verification") or {}).get("fully_grounded"))
    grounding_rate = grounded / n

    issues = (review or {}).get("issues") or []
    blockers = [i for i in issues if i.get("severity") == "blocker"]
    warnings_ = [i for i in issues if i.get("severity") == "warning"]
    if blockers:
        reviewer_clean = 0.0
    elif warnings_:
        # each warning shaves 10 points off (cap floor at 0.5 for warnings only)
        reviewer_clean = max(0.5, 1.0 - 0.1 * len(warnings_))
    else:
        reviewer_clean = 1.0

    score = 100.0 * (
        0.30 * source_coverage
        + 0.30 * confidence_index
        + 0.25 * grounding_rate
        + 0.15 * reviewer_clean
    )

    return Composite(
        source_coverage=source_coverage,
        confidence_index=confidence_index,
        grounding_rate=grounding_rate,
        reviewer_clean=reviewer_clean,
        score=score,
    )
