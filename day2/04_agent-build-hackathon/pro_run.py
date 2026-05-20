"""Hackathon Pro CLI.

Usage:
    source .venv/bin/activate && source ~/.basecamp_anthropic_key

    python pro_run.py sample          # run Pro on the sample RFP
    python pro_run.py surprise        # run Pro on the surprise RFP
    python pro_run.py all             # both
    python pro_run.py compare sample  # baseline + Pro on sample, A/B HTML
    python pro_run.py compare all     # baseline + Pro on both, A/B HTML × 2

Outputs (in ./runs/):
    pro_<rfp>.json    full structured report
    pro_<rfp>.html    Apple-grade per-RFP viewer
    ab_<rfp>.html     side-by-side baseline vs Pro

Then `open runs/pro_sample.html` or similar.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNS = HERE / "runs"
RUNS.mkdir(parents=True, exist_ok=True)

# Import the baseline fixtures and the baseline pipeline (for compare mode).
sys.path.insert(0, str(HERE))
import hackathon  # type: ignore
from pro import runner, viewer, comparator, presentation

SAMPLE_RFP = hackathon.SAMPLE_RFP
SURPRISE_RFP = hackathon.SURPRISE_RFP

RFPS = {
    "sample":   ("Helios sample RFP", SAMPLE_RFP),
    "surprise": ("SURPRISE RFP (unseen)", SURPRISE_RFP),
}


def run_pro(rfp_key: str, *, baseline_report: dict | None = None) -> dict:
    name, questions = RFPS[rfp_key]
    report = runner.run(name, questions, out_dir=RUNS)
    report_dict = report.as_dict()
    report_dict["_rfp_key"] = rfp_key  # used by the presentation CTA links

    viewer_path = RUNS / f"pro_{rfp_key}.html"
    viewer.render(report_dict, viewer_path)
    print(f"  wrote {viewer_path}")

    pres_path = RUNS / f"keynote_{rfp_key}.html"
    presentation.render(report_dict, baseline_report, pres_path)
    print(f"  wrote {pres_path}")

    return report_dict


def run_baseline(rfp_key: str) -> dict:
    """Drive the baseline hackathon.py pipeline on the same RFP for A/B."""
    name, questions = RFPS[rfp_key]
    print(f"\n--- BASELINE on {name} ---")
    answers = hackathon.process_rfp(questions)
    review = hackathon.review_answers(answers)
    evals = hackathon.run_evals(answers, sample_specific=(rfp_key == "sample"))
    # Coerce the baseline report into the same envelope so comparator can read both.
    n = len(answers) or 1
    with_sources = sum(1 for a in answers if a.get("sources"))
    high = sum(1 for a in answers if a.get("confidence") == "high")
    medium = sum(1 for a in answers if a.get("confidence") == "medium")
    low = sum(1 for a in answers if a.get("confidence") == "low")
    confidence_index = (high * 1.0 + medium * 0.6 + low * 0.2) / n
    issues = review.get("issues") or []
    reviewer_clean = 0.0 if any(i for i in issues) else 1.0
    # baseline has no grounding verifier — count it as a loss
    grounding_rate = 0.0
    composite = 100 * (0.30 * (with_sources / n) + 0.30 * confidence_index
                       + 0.25 * grounding_rate + 0.15 * reviewer_clean)
    baseline_report = {
        "rfp_name": name,
        "total_questions": n,
        "answers": answers,
        "review": review,
        "evals": evals,
        "composite": {
            "source_coverage": round(with_sources / n * 100, 1),
            "confidence_index": round(confidence_index * 100, 1),
            "grounding_rate": 0.0,
            "reviewer_clean": round(reviewer_clean * 100, 1),
            "score": round(composite, 1),
        },
        "cost": {"total_cost": 0, "total_calls": 0,
                  "total_input_tokens": 0, "total_output_tokens": 0,
                  "wall_clock_s": 0, "by_stage": {}},
        "elapsed_s": 0,
    }
    out = RUNS / f"baseline_{rfp_key}.json"
    out.write_text(json.dumps(baseline_report, indent=2))
    print(f"  baseline composite: {baseline_report['composite']['score']:.1f}/100 (wrote {out})")
    return baseline_report


def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help", "help"):
        print(__doc__)
        return

    cmd = args[0].lower()

    if cmd in ("sample", "surprise"):
        run_pro(cmd)
        return

    if cmd == "all":
        for k in ("sample", "surprise"):
            run_pro(k)
        return

    if cmd == "compare":
        target = args[1].lower() if len(args) > 1 else "all"
        keys = ["sample", "surprise"] if target == "all" else [target]
        for k in keys:
            baseline = run_baseline(k)
            pro = run_pro(k, baseline_report=baseline)
            ab_path = RUNS / f"ab_{k}.html"
            comparator.render(baseline, pro, ab_path)
            print(f"  wrote {ab_path}")
        return

    print(f"unknown command: {cmd}")
    print(__doc__)
    sys.exit(2)


if __name__ == "__main__":
    main()
