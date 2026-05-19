"""
Day 2 · Session 4 — show-off feature.

Reads rfp_response_*.json files from hackathon.py and emits a per-answer
quality report. No API calls, pure local analysis.

What it reports per RFP:
- Confidence distribution (high / medium / low / missing)
- Source coverage: % of answers citing ≥1 source; unique KB sources used;
  most-cited sources; orphaned answers (no source AND no flag)
- Flag density: total flags + per-question breakdown
- Answer-text gauges: length distribution, "I don't know" sentinels
- Reviewer take: issue count + most-flagged question_ids
- Eval roll-up: pass rate + which assertions fail most

Usage:
    python analyze.py                        # all rfp_response_*.json in CWD
    python analyze.py rfp_response_sample.json rfp_response_surprise.json
"""
from __future__ import annotations

import glob
import json
import statistics
import sys
from collections import Counter
from pathlib import Path


def load(path: Path) -> dict:
    with path.open() as f:
        return json.load(f)


def bar(passed: int, total: int, width: int = 14) -> str:
    if total <= 0:
        return "·" * width
    filled = round(width * passed / total)
    return "█" * filled + "·" * (width - filled)


def fmt_pct(n: int, total: int) -> str:
    return f"{(100 * n / total) if total else 0:>5.1f}%"


def analyze_one(report: dict) -> None:
    name = report.get("rfp_name", "(unnamed RFP)")
    answers = report.get("answers", []) or []
    review = report.get("review") or {}
    evals = report.get("evals") or {}
    n = len(answers)

    print()
    print("=" * 78)
    print(f"  {name}    [{n} answers]")
    print("=" * 78)

    if n == 0:
        print("  (no answers in this report)")
        return

    # ── Confidence distribution ──────────────────────────────────────────
    conf_counts = Counter(a.get("confidence") or "missing" for a in answers)
    print("\n  Confidence distribution")
    print("  " + "-" * 74)
    for level in ("high", "medium", "low", "missing"):
        c = conf_counts.get(level, 0)
        print(f"    {level:<8} {bar(c, n)} {c:>2}/{n}  {fmt_pct(c, n)}")

    # ── Source coverage ──────────────────────────────────────────────────
    with_sources = sum(1 for a in answers if a.get("sources"))
    source_counts: Counter = Counter()
    for a in answers:
        for s in a.get("sources", []) or []:
            source_counts[s] += 1
    orphan = sum(1 for a in answers
                 if not (a.get("sources") or a.get("flags")))

    print("\n  Source coverage")
    print("  " + "-" * 74)
    print(f"    answers citing ≥1 source:    {with_sources}/{n}  ({fmt_pct(with_sources, n)})")
    print(f"    unique KB sources used:      {len(source_counts)}")
    print(f"    orphan answers (no src/flag):{orphan}")
    if source_counts:
        print(f"\n    Sources by citation count:")
        for src, c in sorted(source_counts.items(), key=lambda kv: -kv[1]):
            print(f"      [{c}×] {src}")

    # ── Flag density ─────────────────────────────────────────────────────
    flag_counts = [(a.get("question_id", "?"), len(a.get("flags") or []))
                   for a in answers]
    total_flags = sum(c for _, c in flag_counts)
    print("\n  Flag density")
    print("  " + "-" * 74)
    print(f"    total reviewer-flags on draft:  {total_flags}")
    if total_flags:
        print(f"    Per question:")
        for qid, c in sorted(flag_counts, key=lambda kv: -kv[1]):
            if c == 0:
                continue
            print(f"      {qid:>4}: {bar(c, max(c, 1), width=c)} {c}")

    # ── Answer-text gauges ───────────────────────────────────────────────
    lengths = [len((a.get("answer") or "")) for a in answers]
    sentinels = ("i don't know", "cannot answer", "no information",
                 "not enough", "no data", "not found")
    no_answer = sum(1 for a in answers
                    if any(s in (a.get("answer") or "").lower() for s in sentinels))
    print("\n  Answer text gauges")
    print("  " + "-" * 74)
    print(f"    answer length (chars):  min={min(lengths)}  "
          f"median={statistics.median(lengths):.0f}  "
          f"mean={statistics.mean(lengths):.0f}  "
          f"max={max(lengths)}")
    print(f"    'I don't know'-style sentinels in answer text: {no_answer}/{n}")

    # ── Per-question summary table ───────────────────────────────────────
    print("\n  Per-question matrix")
    print("  " + "-" * 74)
    print(f"    {'qid':<5} {'conf':<7} {'src':>3} {'flag':>4} {'len':>5}  text snippet")
    print("    " + "-" * 70)
    for a in answers:
        qid = a.get("question_id", "?")
        conf = a.get("confidence") or "—"
        srcs = len(a.get("sources") or [])
        flgs = len(a.get("flags") or [])
        ln = len(a.get("answer") or "")
        snippet = (a.get("answer") or "").strip().replace("\n", " ")[:54]
        print(f"    {qid:<5} {conf:<7} {srcs:>3} {flgs:>4} {ln:>5}  {snippet}")

    # ── Reviewer take ────────────────────────────────────────────────────
    issues = review.get("issues") or []
    print("\n  Reviewer report")
    print("  " + "-" * 74)
    print(f"    issues flagged: {len(issues)}")
    issue_qids: Counter = Counter()
    for iss in issues:
        for qid in iss.get("question_ids", []) or []:
            issue_qids[qid] += 1
    if issue_qids:
        print(f"    Questions implicated in issues:")
        for qid, c in sorted(issue_qids.items(), key=lambda kv: -kv[1]):
            print(f"      {qid}: {c} issue(s)")
    if review.get("overall_assessment"):
        snippet = review["overall_assessment"][:200]
        print(f"    Assessment: {snippet}")

    # ── Eval roll-up ─────────────────────────────────────────────────────
    if evals:
        passed = evals.get("passed", 0)
        failed = evals.get("failed", 0)
        total = passed + failed
        print("\n  Eval roll-up")
        print("  " + "-" * 74)
        print(f"    {passed}/{total} passed ({fmt_pct(passed, total)})")
        # Top failing assertions
        fail_counter: Counter = Counter()
        for d in evals.get("details", []) or []:
            if not d.get("passed"):
                fail_counter[d.get("assertion", "?")] += 1
        if fail_counter:
            print(f"    Top failing assertions:")
            for name, c in sorted(fail_counter.items(), key=lambda kv: -kv[1]):
                print(f"      [{c}×] {name}")

    # ── Quality score (composite) ────────────────────────────────────────
    src_score = with_sources / n if n else 0
    conf_score = (conf_counts.get("high", 0) * 1.0
                  + conf_counts.get("medium", 0) * 0.6
                  + conf_counts.get("low", 0) * 0.2) / n if n else 0
    eval_pass = (evals.get("passed", 0) / (evals.get("passed", 0) + evals.get("failed", 0))
                 if evals and (evals.get("passed", 0) + evals.get("failed", 0)) else 0)
    review_clean = 1.0 if not issues else max(0, 1 - 0.15 * len(issues))
    composite = round(100 * (0.30 * src_score + 0.30 * conf_score
                              + 0.25 * eval_pass + 0.15 * review_clean), 1)

    print("\n  Composite quality score")
    print("  " + "-" * 74)
    print(f"    source coverage   (30%) {bar(int(src_score*14), 14)}  {src_score*100:>5.1f}%")
    print(f"    confidence index  (30%) {bar(int(conf_score*14), 14)}  {conf_score*100:>5.1f}%")
    print(f"    eval pass rate    (25%) {bar(int(eval_pass*14), 14)}  {eval_pass*100:>5.1f}%")
    print(f"    reviewer-clean    (15%) {bar(int(review_clean*14), 14)}  {review_clean*100:>5.1f}%")
    print(f"    OVERALL                                 {composite:>5.1f}/100")


def cross_compare(reports: list[tuple[str, dict]]) -> None:
    if len(reports) < 2:
        return
    print()
    print("=" * 78)
    print("  Cross-RFP comparison")
    print("=" * 78)
    header = f"  {'metric':<28} " + " ".join(f"{name[:18]:>20}" for name, _ in reports)
    print(header)
    print("  " + "-" * (len(header) - 2))

    def metric(report: dict, fn):
        return fn(report)

    rows = [
        ("answers", lambda r: len(r.get("answers", []))),
        ("high confidence (count)",
         lambda r: sum(1 for a in r.get("answers", []) if a.get("confidence") == "high")),
        ("low confidence (count)",
         lambda r: sum(1 for a in r.get("answers", []) if a.get("confidence") == "low")),
        ("answers with sources",
         lambda r: sum(1 for a in r.get("answers", []) if a.get("sources"))),
        ("total flags",
         lambda r: sum(len(a.get("flags") or []) for a in r.get("answers", []))),
        ("reviewer issues",
         lambda r: len((r.get("review") or {}).get("issues") or [])),
        ("eval pass rate",
         lambda r: f"{(r.get('evals') or {}).get('passed', 0)}/"
                   f"{((r.get('evals') or {}).get('passed', 0) + (r.get('evals') or {}).get('failed', 0))}"),
    ]
    for name, fn in rows:
        cells = [str(metric(r, fn)) for _, r in reports]
        print(f"  {name:<28} " + " ".join(f"{c:>20}" for c in cells))


def main() -> None:
    args = sys.argv[1:]
    if args:
        paths = [Path(p) for p in args]
    else:
        paths = sorted(Path(".").glob("rfp_response_*.json"))

    if not paths:
        print("No rfp_response_*.json files found. "
              "Run hackathon.py first or pass file paths as arguments.")
        sys.exit(1)

    reports = []
    for p in paths:
        if not p.exists():
            print(f"  ! {p}: missing, skipping")
            continue
        rep = load(p)
        reports.append((p.stem.replace("rfp_response_", ""), rep))
        analyze_one(rep)

    cross_compare(reports)


if __name__ == "__main__":
    main()
