"""Snapshot regression harness.

Goal: every shipped answer + its grade gets snapshotted. A future run that
regresses on the same question (lower confidence, lost grounding, dropped
sources, new flags) fails the regression test at PR time.

Snapshot file: pro/regression_snapshots.json — one entry per (rfp_key,
question_id). Each entry records the "good baseline" version of the answer.
Run `regression.update_snapshot(report)` to refresh after intentional
improvements; run `regression.check(report)` in CI to detect drift.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


_DEFAULT_PATH = Path(__file__).resolve().parent / "regression_snapshots.json"


def _load(path: Path | None = None) -> dict:
    p = path or _DEFAULT_PATH
    if not p.exists():
        return {}
    return json.loads(p.read_text())


def _save(data: dict, path: Path | None = None) -> Path:
    p = path or _DEFAULT_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, sort_keys=True))
    return p


@dataclass
class Regression:
    question_id: str
    kind: str        # "confidence_dropped" / "lost_source" / "grounding_dropped" / "new_flags"
    before: str
    after: str

    def as_dict(self) -> dict:
        return {"question_id": self.question_id, "kind": self.kind,
                "before": self.before, "after": self.after}


def update_snapshot(report: dict, *, rfp_key: str,
                     path: Path | None = None) -> Path:
    """Overwrite the baseline snapshot for one RFP. Call this manually after
    you've reviewed a run and confirmed it's the new baseline."""
    snaps = _load(path)
    snap = {}
    for a in report.get("answers") or []:
        verif = a.get("verification") or {}
        snap[a["question_id"]] = {
            "confidence": a.get("confidence"),
            "sources_sorted": sorted(a.get("sources") or []),
            "fully_grounded": bool(verif.get("fully_grounded")),
            "flag_count": len(a.get("flags") or []),
        }
    snaps[rfp_key] = snap
    return _save(snaps, path)


def check(report: dict, *, rfp_key: str,
           path: Path | None = None) -> list[Regression]:
    """Compare report against the saved baseline. Returns regressions only.

    Improvements (higher confidence, more sources, gained grounding, fewer
    flags) are silent — only drift matters for the gate.
    """
    snaps = _load(path)
    baseline = snaps.get(rfp_key)
    if not baseline:
        return []  # nothing to compare against
    regressions: list[Regression] = []
    _RANK = {"high": 2, "medium": 1, "low": 0}
    for a in report.get("answers") or []:
        qid = a["question_id"]
        base = baseline.get(qid)
        if not base:
            continue  # new question — not a regression
        verif = a.get("verification") or {}
        cur_conf = a.get("confidence")
        if _RANK.get(cur_conf, 0) < _RANK.get(base["confidence"], 0):
            regressions.append(Regression(qid, "confidence_dropped",
                                            before=base["confidence"], after=cur_conf or ""))
        cur_sources = sorted(a.get("sources") or [])
        lost = set(base["sources_sorted"]) - set(cur_sources)
        if lost:
            regressions.append(Regression(qid, "lost_source",
                                            before=", ".join(sorted(lost)),
                                            after=", ".join(cur_sources)))
        if base["fully_grounded"] and not verif.get("fully_grounded"):
            regressions.append(Regression(qid, "grounding_dropped",
                                            before="True", after="False"))
        cur_flags = len(a.get("flags") or [])
        if cur_flags > base["flag_count"]:
            regressions.append(Regression(qid, "new_flags",
                                            before=str(base["flag_count"]),
                                            after=str(cur_flags)))
    return regressions
