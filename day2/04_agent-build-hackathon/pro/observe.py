"""Structured JSON logging.

`print` is fine for a workshop demo; structured logs are required for any
deployable agent. Every event carries: timestamp_iso, run_id, stage, level,
plus arbitrary fields. Written to stdout as one JSON-per-line so downstream
log shippers (Vector, Fluent Bit, Datadog, etc.) can ingest unchanged.
"""
from __future__ import annotations

import json
import sys
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any


# A single run_id per process unless explicitly overridden. The audit_db
# uses the same id to correlate logs and persisted rows.
_RUN_ID = uuid.uuid4().hex[:12]


def set_run_id(rid: str) -> None:
    global _RUN_ID
    _RUN_ID = rid


def get_run_id() -> str:
    return _RUN_ID


def _emit(level: str, event: str, **fields) -> None:
    line = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
        "run_id": _RUN_ID,
        "level": level,
        "event": event,
        **fields,
    }
    sys.stdout.write(json.dumps(line, default=str) + "\n")
    sys.stdout.flush()


def info(event: str, **fields) -> None:  _emit("info", event, **fields)
def warn(event: str, **fields) -> None:  _emit("warn", event, **fields)
def error(event: str, **fields) -> None: _emit("error", event, **fields)
def debug(event: str, **fields) -> None: _emit("debug", event, **fields)


@contextmanager
def stage(name: str, **fields):
    """Context manager that logs entry + exit (with duration_ms) for a stage."""
    t0 = time.perf_counter()
    info(f"stage.start", stage=name, **fields)
    try:
        yield
        info(f"stage.ok", stage=name,
             duration_ms=round((time.perf_counter() - t0) * 1000, 1),
             **fields)
    except Exception as exc:
        error(f"stage.error", stage=name,
              duration_ms=round((time.perf_counter() - t0) * 1000, 1),
              error_class=type(exc).__name__, error_msg=str(exc)[:200],
              **fields)
        raise
