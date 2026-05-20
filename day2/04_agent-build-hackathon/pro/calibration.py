"""Calibrated confidence.

The model emits a coarse label (high / medium / low). Calibration converts
that label into a probability of correctness, calibrated against historical
accuracy on similar questions.

Inputs:
  - audit_db.answers + feedback rows accumulated over time
  - the new draft's stated confidence + category

Output:
  - a calibrated probability in [0, 1]
  - a calibration bucket count so the answer carries "trust me +/- N"

Bootstrap: when too few historical rows exist (cold start), fall back to
priors (high=0.90, medium=0.70, low=0.40) — those mirror typical observed
rates for forced-tool-output agents and decay as real data comes in.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from . import audit_db


# Cold-start priors when history is too thin to be statistically meaningful.
_PRIOR = {"high": 0.90, "medium": 0.70, "low": 0.40}
# Number of historical samples below which we lean on the prior.
_BAYES_K = 10


@dataclass
class CalibrationBucket:
    confidence: str
    category: str | None
    n_samples: int
    n_correct: int
    raw_accuracy: float       # n_correct / n_samples (0 if n_samples = 0)
    calibrated: float         # Bayesian-smoothed accuracy
    is_bootstrapped: bool     # True when we're leaning on the prior


@dataclass
class CalibrationResult:
    bucket: CalibrationBucket
    confidence_label: str
    advice: str

    def as_dict(self) -> dict:
        return {
            "bucket": {
                "confidence": self.bucket.confidence,
                "category": self.bucket.category,
                "n_samples": self.bucket.n_samples,
                "n_correct": self.bucket.n_correct,
                "raw_accuracy": round(self.bucket.raw_accuracy, 3),
                "calibrated": round(self.bucket.calibrated, 3),
                "is_bootstrapped": self.bucket.is_bootstrapped,
            },
            "confidence_label": self.confidence_label,
            "advice": self.advice,
        }


def _compute_bucket(confidence: str, category: str | None,
                     db_path: Path | None) -> CalibrationBucket:
    """Pull (confidence, category) samples from the audit DB and compute the
    raw + Bayesian-smoothed correctness rate.

    Correctness signal: feedback verdict in ('approve', 'promote') counts
    as correct; 'edit'/'reject' counts as incorrect.
    """
    with audit_db.connect(db_path) as conn:
        if category:
            rows = conn.execute("""
                SELECT a.question_id, f.verdict
                FROM answers a
                LEFT JOIN feedback f
                  ON f.run_id = a.run_id AND f.question_id = a.question_id
                WHERE a.confidence = ? AND a.category = ? AND f.verdict IS NOT NULL
            """, (confidence, category)).fetchall()
        else:
            rows = conn.execute("""
                SELECT a.question_id, f.verdict
                FROM answers a
                LEFT JOIN feedback f
                  ON f.run_id = a.run_id AND f.question_id = a.question_id
                WHERE a.confidence = ? AND f.verdict IS NOT NULL
            """, (confidence,)).fetchall()

    n = len(rows)
    n_correct = sum(1 for _, v in rows if v in ("approve", "promote"))
    raw = (n_correct / n) if n else 0.0

    # Bayesian smoothing toward the prior. When n=0, calibrated = prior.
    # When n >> _BAYES_K, calibrated ≈ raw.
    prior = _PRIOR.get(confidence, 0.5)
    calibrated = (prior * _BAYES_K + n_correct) / (_BAYES_K + n)
    is_bootstrapped = n < _BAYES_K

    return CalibrationBucket(
        confidence=confidence, category=category,
        n_samples=n, n_correct=n_correct,
        raw_accuracy=raw, calibrated=calibrated,
        is_bootstrapped=is_bootstrapped,
    )


def _humanize(prob: float) -> str:
    """Bucket the calibrated probability into a four-tier label."""
    if prob >= 0.85: return "very high"
    if prob >= 0.65: return "high"
    if prob >= 0.40: return "medium"
    return "low"


def _advice(bucket: CalibrationBucket) -> str:
    if bucket.is_bootstrapped:
        return (f"Calibrated probability is anchored to the prior — only "
                f"{bucket.n_samples} historical samples in this bucket. "
                f"Treat as directional, refine as feedback compounds.")
    if bucket.calibrated >= 0.85:
        return "Historical accuracy in this bucket is strong; ship."
    if bucket.calibrated >= 0.65:
        return "Generally reliable, but spot-check the cited sources."
    if bucket.calibrated >= 0.40:
        return ("Coin-flip territory — recommend human review before "
                "sending to a customer.")
    return ("Historically inaccurate in this bucket. Do not ship without "
            "explicit reviewer sign-off.")


def calibrate(confidence: str, category: str | None = None,
               db_path: Path | None = None) -> CalibrationResult:
    """Look up the empirical accuracy of (confidence, category) and return a
    calibrated probability + an advisory string."""
    bucket = _compute_bucket(confidence, category, db_path)
    return CalibrationResult(
        bucket=bucket,
        confidence_label=_humanize(bucket.calibrated),
        advice=_advice(bucket),
    )


def calibrate_all(db_path: Path | None = None) -> dict[str, CalibrationBucket]:
    """Convenience: compute buckets for every confidence label (un-categorized)."""
    return {
        c: _compute_bucket(c, None, db_path)
        for c in ("high", "medium", "low")
    }
