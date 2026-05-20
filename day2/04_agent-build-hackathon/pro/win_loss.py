"""Win/loss tracking — correlate RFP outcomes to answer quality.

The strongest signal Pro can compound is whether the deals actually close.
This module owns a small ledger in the audit DB that maps run_id to outcome
(won / lost / withdrawn / pending), tracks days-to-decision, and aggregates
the composite quality score by outcome.

Calculated on demand: composite_by_outcome(), avg_days_to_close_by_outcome(),
win_rate_by_score_band(). Use as the input to "does quality predict wins?"
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from . import audit_db


VALID_OUTCOMES = {"won", "lost", "withdrawn", "pending"}


_SCHEMA = """
CREATE TABLE IF NOT EXISTS rfp_outcomes (
  run_id          TEXT PRIMARY KEY,
  outcome         TEXT NOT NULL CHECK(outcome IN ('won','lost','withdrawn','pending')),
  decision_date   TEXT,
  contract_value  REAL,
  competitor_chosen TEXT,
  reason          TEXT,
  recorded_at     TEXT NOT NULL,
  FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
);
"""


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)


@dataclass
class Outcome:
    run_id: str
    outcome: str
    decision_date: str | None = None
    contract_value: float | None = None
    competitor_chosen: str | None = None
    reason: str = ""


def record_outcome(o: Outcome, db_path: Path | None = None) -> None:
    if o.outcome not in VALID_OUTCOMES:
        raise ValueError(f"outcome must be one of {sorted(VALID_OUTCOMES)}")
    with audit_db.connect(db_path) as conn:
        _ensure_schema(conn)
        conn.execute("""
            INSERT OR REPLACE INTO rfp_outcomes
              (run_id, outcome, decision_date, contract_value, competitor_chosen, reason, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (o.run_id, o.outcome, o.decision_date, o.contract_value,
              o.competitor_chosen, o.reason, audit_db.now_iso()))


def composite_by_outcome(db_path: Path | None = None) -> dict[str, dict]:
    """For each outcome, mean+min+max composite quality score + sample size."""
    with audit_db.connect(db_path) as conn:
        _ensure_schema(conn)
        rows = conn.execute("""
            SELECT o.outcome, r.composite_score
            FROM rfp_outcomes o
            JOIN runs r ON r.run_id = o.run_id
            WHERE r.composite_score IS NOT NULL
        """).fetchall()
    buckets: dict[str, list[float]] = {}
    for outcome, score in rows:
        buckets.setdefault(outcome, []).append(score)
    out = {}
    for k, v in buckets.items():
        out[k] = {
            "n": len(v),
            "mean": round(sum(v) / len(v), 2),
            "min": round(min(v), 2),
            "max": round(max(v), 2),
        }
    return out


def win_rate_by_score_band(db_path: Path | None = None,
                             bands: list[tuple[float, float]] | None = None
                             ) -> list[dict]:
    """Group runs by composite-score band, compute win rate in each band."""
    bands = bands or [(0, 60), (60, 75), (75, 90), (90, 101)]
    with audit_db.connect(db_path) as conn:
        _ensure_schema(conn)
        rows = conn.execute("""
            SELECT o.outcome, r.composite_score
            FROM rfp_outcomes o
            JOIN runs r ON r.run_id = o.run_id
            WHERE r.composite_score IS NOT NULL AND o.outcome IN ('won','lost')
        """).fetchall()
    out = []
    for lo, hi in bands:
        in_band = [o for o, s in rows if lo <= s < hi]
        won = sum(1 for o in in_band if o == "won")
        n = len(in_band)
        out.append({
            "band": f"{lo:.0f}-{hi - 1:.0f}",
            "n": n,
            "won": won,
            "win_rate": round(won / n, 3) if n else None,
        })
    return out


def list_outcomes(db_path: Path | None = None, *, limit: int = 100) -> list[dict]:
    with audit_db.connect(db_path) as conn:
        _ensure_schema(conn)
        rows = conn.execute("""
            SELECT run_id, outcome, decision_date, contract_value,
                   competitor_chosen, reason, recorded_at
            FROM rfp_outcomes ORDER BY recorded_at DESC LIMIT ?
        """, (limit,)).fetchall()
    return [
        {"run_id": r[0], "outcome": r[1], "decision_date": r[2],
         "contract_value": r[3], "competitor_chosen": r[4],
         "reason": r[5], "recorded_at": r[6]}
        for r in rows
    ]
