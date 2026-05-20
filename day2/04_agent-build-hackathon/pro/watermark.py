"""Provenance / watermark metadata.

Every Pro response carries a provenance block so an auditor (or the SE
reviewing months later) can answer:
  - Which agent version produced this?
  - Which model + which prompt variant?
  - Against which KB snapshot?
  - When + by whom + in which run?
  - Was it edited by a human afterwards?
"""
from __future__ import annotations

import hashlib
import platform
import sys
import time
from dataclasses import dataclass, asdict, field

from . import __version__ as PRO_VERSION


def _kb_hash(knowledge_base: dict) -> str:
    """Stable hash over (id, content) pairs so KB edits invalidate."""
    h = hashlib.sha256()
    for doc_id in sorted(knowledge_base):
        h.update(doc_id.encode())
        h.update(knowledge_base[doc_id].get("content", "").encode())
    return h.hexdigest()[:16]


@dataclass
class Provenance:
    agent_version: str
    pro_version: str
    model: str
    prompt_variant: str
    kb_hash: str
    run_id: str
    ts_utc: str
    python_version: str
    platform: str
    reviewer: str = ""           # populated after human review
    review_verdict: str = ""     # 'approved' / 'edited' / 'rejected'
    review_ts_utc: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def make_provenance(*, model: str, run_id: str, knowledge_base: dict,
                     prompt_variant: str = "pro/multi-stage",
                     agent_version: str = "pro/1.0.0") -> Provenance:
    return Provenance(
        agent_version=agent_version,
        pro_version=PRO_VERSION,
        model=model,
        prompt_variant=prompt_variant,
        kb_hash=_kb_hash(knowledge_base),
        run_id=run_id,
        ts_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        python_version=sys.version.split()[0],
        platform=f"{platform.system()} {platform.release()}",
    )


def stamp(report: dict, prov: Provenance) -> dict:
    """Mutate `report` in place + return it. Adds a top-level 'provenance' key
    AND embeds a small per-answer copy so single-question exports retain it."""
    p = prov.as_dict()
    report["provenance"] = p
    for a in report.get("answers", []) or []:
        a["provenance"] = {
            "agent_version": p["agent_version"],
            "model": p["model"],
            "run_id": p["run_id"],
            "kb_hash": p["kb_hash"],
            "ts_utc": p["ts_utc"],
        }
    return report


def record_review(report: dict, *, reviewer: str, verdict: str) -> dict:
    """After a human review, stamp the verdict into the top-level provenance."""
    p = report.get("provenance") or {}
    p["reviewer"] = reviewer
    p["review_verdict"] = verdict
    p["review_ts_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    report["provenance"] = p
    return report
