"""A/B comparator: baseline vs Pro on the same RFP.

Runs both pipelines, diffs the composite scores per dimension, generates a
side-by-side HTML report.
"""
from __future__ import annotations

import html
import json
from pathlib import Path


_COMP_CSS = """
:root { --bg:#fbfbfd;--surface:#fff;--surface-2:#f5f5f7;--ink:#1d1d1f;
  --ink-muted:#6e6e73;--ink-faint:#86868b;--line-soft:#ececee;
  --accent:#0071e3;--done:#30b04a;--warn:#ff9f0a;--bad:#ff453a;
  --shadow:0 1px 3px rgba(0,0,0,.04),0 12px 28px rgba(0,0,0,.06);
  --radius:18px; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#000;--surface:#1c1c1e;--surface-2:#2c2c2e;--ink:#f5f5f7;
    --ink-muted:#98989d;--ink-faint:#6e6e73;--line-soft:#2c2c2e;
    --accent:#2997ff; --shadow:0 1px 3px rgba(0,0,0,.5),0 18px 32px rgba(0,0,0,.45); }
}
* { margin:0;padding:0;box-sizing:border-box; }
html,body { background:var(--bg);color:var(--ink); font: 16px/1.5 "SF Pro Text",-apple-system,BlinkMacSystemFont,"Helvetica Neue",sans-serif; -webkit-font-smoothing:antialiased; }
.shell { max-width:1180px;margin:0 auto;padding:0 28px; }
.hero { padding:72px 0 24px;text-align:center; }
.hero h1 { font-family:"SF Pro Display",-apple-system,sans-serif; font-size:clamp(36px,5vw,56px); font-weight:600;letter-spacing:-0.025em;line-height:1.05; }
.hero .sub { color:var(--ink-muted);font-size:20px;margin-top:14px; }
.cards { display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-top:48px; }
@media (max-width:760px){ .cards { grid-template-columns:1fr; } }
.card { background:var(--surface);border:1px solid var(--line-soft);border-radius:var(--radius);padding:32px;box-shadow:var(--shadow);text-align:center; }
.card .label { font-size:12px;text-transform:uppercase;letter-spacing:0.08em;color:var(--ink-muted);font-weight:600;margin-bottom:8px; }
.card.pro .label { color:var(--accent); }
.card .name { font-size:18px;font-weight:600;letter-spacing:-0.01em;color:var(--ink);margin-bottom:14px; }
.card .score {
  font-family:"SF Pro Display",-apple-system,sans-serif;
  font-size:88px;font-weight:600;letter-spacing:-0.04em;line-height:1; color:var(--done);
  font-variant-numeric:tabular-nums;
}
.card .score.bad { color:var(--bad); }
.card .score.warn { color:var(--warn); }
.card .of { font-size:18px;color:var(--ink-muted);margin-top:6px; }
.card .breakdown { margin-top:24px;font-size:13px;color:var(--ink-muted); }
.delta-table { width:100%;border-collapse:collapse;margin-top:48px; background:var(--surface); border:1px solid var(--line-soft);border-radius:var(--radius);overflow:hidden;box-shadow:var(--shadow); }
.delta-table th,.delta-table td { padding:14px 18px;border-bottom:1px solid var(--line-soft); }
.delta-table th { text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:0.06em;color:var(--ink-muted);font-weight:600;background:var(--surface-2); }
.delta-table td { font-size:14px; }
.delta-table td.num { text-align:right;font-variant-numeric:tabular-nums; }
.delta { font-weight:600; }
.delta.up { color:var(--done); }
.delta.down { color:var(--bad); }
.delta.flat { color:var(--ink-muted); }
footer { padding:48px 0 64px;text-align:center;color:var(--ink-faint);font-size:12px;border-top:1px solid var(--line-soft);margin-top:64px; }
"""


def _esc(s) -> str:
    return html.escape(str(s)) if s is not None else ""


def _score_cls(s: float) -> str:
    if s >= 90: return ""
    if s >= 70: return "warn"
    return "bad"


def _delta_cell(a: float, b: float, suffix: str = "") -> str:
    diff = b - a
    cls = "up" if diff > 0.5 else ("down" if diff < -0.5 else "flat")
    sign = "+" if diff > 0 else ""
    return f'<td class="num delta {cls}">{sign}{diff:.1f}{suffix}</td>'


def render(baseline_report: dict, pro_report: dict, out_path: Path) -> Path:
    bc = baseline_report.get("composite", {}) or {}
    pc = pro_report.get("composite", {}) or {}

    bs = float(bc.get("score", 0))
    ps = float(pc.get("score", 0))

    rfp_name = pro_report.get("rfp_name", "RFP")

    rows = [
        ("Composite score",        bs,   ps,   ""),
        ("Source coverage",        bc.get("source_coverage", 0),    pc.get("source_coverage", 0),    "%"),
        ("Confidence index",       bc.get("confidence_index", 0),   pc.get("confidence_index", 0),   "%"),
        ("Grounding rate",         bc.get("grounding_rate", 0),     pc.get("grounding_rate", 0),     "%"),
        ("Reviewer clean",         bc.get("reviewer_clean", 0),     pc.get("reviewer_clean", 0),     "%"),
    ]
    kpi_rows = [
        ("Total cost ($)",         baseline_report.get("cost", {}).get("total_cost", 0),
                                   pro_report.get("cost", {}).get("total_cost", 0), ""),
        ("Total API calls",        baseline_report.get("cost", {}).get("total_calls", 0),
                                   pro_report.get("cost", {}).get("total_calls", 0), ""),
        ("Wall clock (s)",         baseline_report.get("cost", {}).get("wall_clock_s",
                                       baseline_report.get("elapsed_s", 0)),
                                   pro_report.get("cost", {}).get("wall_clock_s",
                                       pro_report.get("elapsed_s", 0)), ""),
        ("Reviewer issues",        len((baseline_report.get("review") or {}).get("issues") or []),
                                   len((pro_report.get("review") or {}).get("issues") or []), ""),
    ]

    rows_html = "\n".join(
        f"<tr><td>{name}</td>"
        f"<td class='num'>{a:.1f}{suffix}</td>"
        f"<td class='num'>{b:.1f}{suffix}</td>"
        f"{_delta_cell(a, b, suffix)}</tr>"
        for name, a, b, suffix in rows
    )
    kpi_html = "\n".join(
        f"<tr><td>{name}</td>"
        f"<td class='num'>{a}</td>"
        f"<td class='num'>{b}</td>"
        f"<td class='num delta flat'>—</td></tr>"
        for name, a, b, _ in kpi_rows
    )

    doc = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8" /><meta name="viewport" content="width=device-width,initial-scale=1" />
<title>A/B · Baseline vs Pro · {_esc(rfp_name)}</title>
<style>{_COMP_CSS}</style>
</head><body>

<header class="hero"><div class="shell">
  <h1>Baseline vs Pro</h1>
  <div class="sub">{_esc(rfp_name)} · same questions, same KB, same model</div>

  <div class="cards">
    <div class="card">
      <div class="label">Baseline (hackathon.py)</div>
      <div class="name">keyword retrieval · text JSON · single review</div>
      <div class="score {_score_cls(bs)}">{bs:.1f}</div>
      <div class="of">/ 100</div>
    </div>
    <div class="card pro">
      <div class="label">Pro (day2/04/pro)</div>
      <div class="name">BM25 + rerank · structured output · critique + verify</div>
      <div class="score {_score_cls(ps)}">{ps:.1f}</div>
      <div class="of">/ 100</div>
    </div>
  </div>
</div></header>

<main class="shell">
  <table class="delta-table">
    <thead><tr><th>Quality metric</th><th class="num" style="text-align:right">Baseline</th><th class="num" style="text-align:right">Pro</th><th class="num" style="text-align:right">Δ</th></tr></thead>
    <tbody>{rows_html}</tbody>
  </table>

  <table class="delta-table" style="margin-top:32px">
    <thead><tr><th>Operational metric</th><th class="num" style="text-align:right">Baseline</th><th class="num" style="text-align:right">Pro</th><th class="num" style="text-align:right">Δ</th></tr></thead>
    <tbody>{kpi_html}</tbody>
  </table>
</main>

<footer>Side-by-side comparison · open the per-RFP viewer for full drill-down</footer>
</body></html>"""

    out_path.write_text(doc)
    return out_path
