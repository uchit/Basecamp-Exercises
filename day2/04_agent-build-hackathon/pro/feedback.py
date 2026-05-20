"""SE feedback capture — turns reviewer actions into durable signal.

Writes to the audit_db.feedback table. Five verdicts:
  approve     answer ships as-is
  edit        answer ships with reviewer's edit_text replacing the original
  reject      answer pulled; question re-routed
  promote     answer is exemplary — promote to the few-shot pool
  needs_kb    answer was unanswerable due to a KB gap; ticket the content team

These rows compound into a fine-tuning corpus + a regression-test source.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import audit_db


VALID_VERDICTS = {"approve", "edit", "reject", "promote", "needs_kb"}


@dataclass
class FeedbackEntry:
    run_id: str
    question_id: str | None
    reviewer: str
    verdict: str
    edit_text: str = ""
    notes: str = ""

    def is_valid(self) -> bool:
        return (self.verdict in VALID_VERDICTS
                and bool(self.reviewer)
                and bool(self.run_id))


def record(entry: FeedbackEntry, db_path: Path | None = None) -> None:
    """Persist one feedback row. Raises ValueError on invalid input."""
    if not entry.is_valid():
        raise ValueError(
            f"invalid feedback: verdict must be one of {sorted(VALID_VERDICTS)}; "
            f"run_id + reviewer required."
        )
    with audit_db.connect(db_path) as conn:
        audit_db.insert_feedback(
            conn, run_id=entry.run_id, question_id=entry.question_id,
            reviewer=entry.reviewer, verdict=entry.verdict,
            edit_text=entry.edit_text, notes=entry.notes,
        )


def list_for_run(run_id: str, db_path: Path | None = None) -> list[dict]:
    """All feedback rows for one run, newest first."""
    with audit_db.connect(db_path) as conn:
        rows = conn.execute("""
            SELECT question_id, ts, reviewer, verdict, edit_text, notes
            FROM feedback WHERE run_id = ?
            ORDER BY ts DESC
        """, (run_id,)).fetchall()
    return [
        {"question_id": r[0], "ts": r[1], "reviewer": r[2],
         "verdict": r[3], "edit_text": r[4], "notes": r[5]}
        for r in rows
    ]


def aggregate(db_path: Path | None = None) -> dict:
    """Verdict counts across the whole feedback table — useful for dashboards."""
    with audit_db.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT verdict, COUNT(*) FROM feedback GROUP BY verdict"
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
    return {
        "total": total,
        "by_verdict": {r[0]: r[1] for r in rows},
    }


def fine_tuning_corpus(db_path: Path | None = None) -> list[dict]:
    """Yield reviewer-edited answers as (input, output) training pairs.

    Filter: verdict='edit' OR 'promote', edit_text non-empty.
    Joins answers table to retrieve the original draft.
    """
    with audit_db.connect(db_path) as conn:
        rows = conn.execute("""
            SELECT f.question_id, a.answer_text, f.edit_text, f.verdict, f.notes
            FROM feedback f
            JOIN answers a ON a.run_id = f.run_id AND a.question_id = f.question_id
            WHERE f.verdict IN ('edit', 'promote') AND length(f.edit_text) > 0
        """).fetchall()
    return [
        {"question_id": r[0],
          "original_draft": r[1] or "",
          "approved_answer": r[2] or r[1] or "",
          "verdict": r[3],
          "notes": r[4] or ""}
        for r in rows
    ]
