"""SQLite audit log — every run persisted to disk.

Tables:
  runs           one row per RFP run (id, name, started_at, finished_at,
                 composite_score, total_cost, total_calls)
  answers        one row per drafted answer (run_id, question_id, category,
                 confidence, sources, fully_grounded, answer_text, flags_json)
  cost_entries   one row per LLM call (run_id, stage, model, in_tok, out_tok,
                 cost, elapsed_ms)
  feedback       one row per human edit/comment (run_id, question_id, edit,
                 verdict, reviewer, timestamp)

Searchable history for compliance + a real-world A/B harness later
(`SELECT composite_score FROM runs WHERE prompt_variant='v3'`).
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_DB = Path(__file__).resolve().parent.parent / "audit.db"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id          TEXT PRIMARY KEY,
  rfp_name        TEXT NOT NULL,
  prompt_variant  TEXT,
  started_at      TEXT NOT NULL,
  finished_at     TEXT,
  composite_score REAL,
  total_cost      REAL,
  total_calls     INTEGER,
  agent_version   TEXT,
  kb_hash         TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_at);

CREATE TABLE IF NOT EXISTS answers (
  run_id          TEXT NOT NULL,
  question_id     TEXT NOT NULL,
  category        TEXT,
  confidence      TEXT,
  sources         TEXT,                -- JSON array
  fully_grounded  INTEGER,
  answer_text     TEXT,
  flags_json      TEXT,                -- JSON array
  PRIMARY KEY (run_id, question_id),
  FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS cost_entries (
  run_id      TEXT NOT NULL,
  stage       TEXT NOT NULL,
  model       TEXT NOT NULL,
  input_tokens  INTEGER,
  output_tokens INTEGER,
  cost        REAL,
  elapsed_ms  REAL,
  ts          TEXT,
  FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_cost_run ON cost_entries(run_id);

CREATE TABLE IF NOT EXISTS feedback (
  run_id        TEXT NOT NULL,
  question_id   TEXT,
  ts            TEXT NOT NULL,
  reviewer      TEXT,
  verdict       TEXT,                  -- 'approve' / 'reject' / 'edit'
  edit_text     TEXT,
  notes         TEXT,
  FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
);
"""


@contextmanager
def connect(path: Path | None = None):
    p = path or DEFAULT_DB
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def insert_run(conn, *, run_id: str, rfp_name: str,
               prompt_variant: str | None = None,
               agent_version: str = "pro/1.0.0",
               kb_hash: str | None = None) -> None:
    conn.execute(
        "INSERT INTO runs (run_id, rfp_name, prompt_variant, started_at, "
        "agent_version, kb_hash) VALUES (?, ?, ?, ?, ?, ?)",
        (run_id, rfp_name, prompt_variant, now_iso(), agent_version, kb_hash),
    )


def finish_run(conn, *, run_id: str, composite_score: float,
                total_cost: float, total_calls: int) -> None:
    conn.execute(
        "UPDATE runs SET finished_at=?, composite_score=?, total_cost=?, "
        "total_calls=? WHERE run_id=?",
        (now_iso(), composite_score, total_cost, total_calls, run_id),
    )


def insert_answer(conn, *, run_id: str, answer: dict) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO answers (run_id, question_id, category, "
        "confidence, sources, fully_grounded, answer_text, flags_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            run_id,
            answer.get("question_id", "?"),
            answer.get("category"),
            answer.get("confidence"),
            json.dumps(answer.get("sources") or []),
            1 if (answer.get("verification") or {}).get("fully_grounded") else 0,
            answer.get("answer"),
            json.dumps(answer.get("flags") or []),
        ),
    )


def insert_cost_entries(conn, *, run_id: str, entries: list[dict]) -> None:
    if not entries:
        return
    conn.executemany(
        "INSERT INTO cost_entries (run_id, stage, model, input_tokens, "
        "output_tokens, cost, elapsed_ms, ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [(run_id, e["stage"], e["model"], e["input_tokens"], e["output_tokens"],
          e["cost"], e["elapsed_ms"], now_iso()) for e in entries],
    )


def insert_feedback(conn, *, run_id: str, question_id: str | None,
                     reviewer: str, verdict: str,
                     edit_text: str = "", notes: str = "") -> None:
    conn.execute(
        "INSERT INTO feedback (run_id, question_id, ts, reviewer, verdict, "
        "edit_text, notes) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (run_id, question_id, now_iso(), reviewer, verdict, edit_text, notes),
    )


def list_runs(conn, *, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        "SELECT run_id, rfp_name, started_at, finished_at, composite_score, "
        "total_cost, total_calls FROM runs ORDER BY started_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(zip(
        ("run_id", "rfp_name", "started_at", "finished_at",
         "composite_score", "total_cost", "total_calls"),
        r,
    )) for r in rows]


def get_run(conn, run_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
    if row is None:
        return None
    cols = [d[0] for d in conn.execute("PRAGMA table_info(runs)").fetchall()]
    return dict(zip(cols, row))
