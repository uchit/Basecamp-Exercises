"""Apple-grade HTML viewer.

Single self-contained .html file per RunReport. SF Pro typography, dark mode
via prefers-color-scheme, soft cards, hairline borders. Every question is a
card with an expandable audit trail (retrieval hits, evidence quotes, the
critique verdict, citation verification status).

No build, no JS framework — vanilla everything. Drop the .html in any
browser, it works.
"""
from __future__ import annotations

import html
import json
from pathlib import Path


_CSS = """
:root {
  --bg: #fbfbfd; --surface: #fff; --surface-2: #f5f5f7;
  --ink: #1d1d1f; --ink-muted: #6e6e73; --ink-faint: #86868b;
  --line: #d2d2d7; --line-soft: #ececee;
  --accent: #0071e3;
  --done: #30b04a; --warn: #ff9f0a; --bad: #ff453a; --info: #5e5ce6;
  --shadow: 0 1px 3px rgba(0,0,0,.04), 0 8px 24px rgba(0,0,0,.05);
  --shadow-lg: 0 1px 3px rgba(0,0,0,.06), 0 22px 48px rgba(0,0,0,.08);
  --radius: 16px; --radius-sm: 10px;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #000; --surface: #1c1c1e; --surface-2: #2c2c2e;
    --ink: #f5f5f7; --ink-muted: #98989d; --ink-faint: #6e6e73;
    --line: #38383a; --line-soft: #2c2c2e;
    --accent: #2997ff;
    --shadow: 0 1px 3px rgba(0,0,0,.5), 0 18px 32px rgba(0,0,0,.45);
    --shadow-lg: 0 1px 3px rgba(0,0,0,.5), 0 28px 48px rgba(0,0,0,.55);
  }
}
* { margin: 0; padding: 0; box-sizing: border-box; }
html, body {
  background: var(--bg); color: var(--ink);
  font: 16px/1.5 "SF Pro Text", -apple-system, BlinkMacSystemFont, "Helvetica Neue", Helvetica, sans-serif;
  -webkit-font-smoothing: antialiased; -moz-osx-font-smoothing: grayscale;
}
.shell { max-width: 1080px; margin: 0 auto; padding: 0 28px; }

/* Hero */
.hero { padding: 80px 0 40px; text-align: center; }
.hero .pill {
  display: inline-block; padding: 4px 12px;
  background: var(--surface); border: 1px solid var(--line-soft); border-radius: 999px;
  color: var(--accent); font-size: 12px; font-weight: 600;
  letter-spacing: 0.06em; text-transform: uppercase; margin-bottom: 18px;
}
.hero h1 {
  font-family: "SF Pro Display", -apple-system, sans-serif;
  font-size: clamp(40px, 6vw, 64px); font-weight: 600;
  letter-spacing: -0.025em; line-height: 1.05;
}
.hero .sub { color: var(--ink-muted); font-size: 20px; margin-top: 14px; }

/* Composite score panel */
.score-panel {
  margin-top: 40px; padding: 32px;
  background: var(--surface); border: 1px solid var(--line-soft);
  border-radius: var(--radius); box-shadow: var(--shadow);
}
.score-display {
  display: flex; align-items: baseline; justify-content: center;
  gap: 12px; margin-bottom: 28px;
}
.score-num {
  font-family: "SF Pro Display", -apple-system, sans-serif;
  font-size: 88px; font-weight: 600; letter-spacing: -0.04em;
  color: var(--done); font-variant-numeric: tabular-nums; line-height: 1;
}
.score-num.warn { color: var(--warn); }
.score-num.bad  { color: var(--bad); }
.score-suffix { font-size: 22px; color: var(--ink-muted); }
.score-bars { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; }
.bar-cell {
  padding: 14px; background: var(--surface-2); border-radius: var(--radius-sm);
}
.bar-cell .lbl {
  font-size: 11px; color: var(--ink-muted); font-weight: 600;
  text-transform: uppercase; letter-spacing: 0.05em;
}
.bar-cell .val {
  font-size: 26px; font-weight: 600; color: var(--ink);
  margin-top: 6px; font-variant-numeric: tabular-nums; letter-spacing: -0.02em;
}
.bar-cell .weight { font-size: 11px; color: var(--ink-faint); margin-top: 4px; }
.bar-cell .bar {
  margin-top: 10px; height: 4px; background: var(--line-soft); border-radius: 999px;
}
.bar-cell .bar > span {
  display: block; height: 100%; background: var(--accent); border-radius: 999px;
}

/* Stats row */
.kpis {
  display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px;
  margin-top: 24px;
}
@media (max-width: 720px) { .kpis { grid-template-columns: repeat(2, 1fr); } }
.kpi {
  padding: 16px; background: var(--surface); border: 1px solid var(--line-soft);
  border-radius: var(--radius-sm);
}
.kpi .n { font-size: 24px; font-weight: 600; color: var(--ink); font-variant-numeric: tabular-nums; }
.kpi .l { font-size: 12px; color: var(--ink-muted); text-transform: uppercase; letter-spacing: 0.05em; margin-top: 2px; }

/* Heatmap */
.heatmap { margin-top: 32px; }
.heatmap h2 { font-size: 14px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em; color: var(--ink-muted); margin-bottom: 12px; }
.heatmap-row { display: flex; gap: 6px; }
.heat-cell {
  flex: 1; aspect-ratio: 1 / 0.4; border-radius: 6px; min-height: 32px;
  display: flex; align-items: center; justify-content: center;
  font-size: 10px; font-weight: 600; color: rgba(255,255,255,.92);
  letter-spacing: -0.005em;
}
.heat-cell.h { background: var(--done); }
.heat-cell.m { background: var(--warn); color: #1d1d1f; }
.heat-cell.l { background: var(--ink-faint); }

/* Review block */
.review-block {
  margin-top: 36px; padding: 22px 24px;
  background: var(--surface); border: 1px solid var(--line-soft);
  border-radius: var(--radius); box-shadow: var(--shadow);
}
.review-block h2 {
  font-size: 14px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em;
  color: var(--ink-muted); margin-bottom: 12px;
}
.review-issue { padding: 10px 12px; border-radius: 8px; margin-bottom: 8px; background: var(--surface-2); }
.review-issue .head {
  font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 4px;
}
.review-issue.blocker .head { color: var(--bad); }
.review-issue.warning .head { color: var(--warn); }
.review-issue.info    .head { color: var(--ink-muted); }
.review-empty { color: var(--ink-muted); font-size: 14px; }

/* Answers */
.answers { margin-top: 36px; }
.answer {
  background: var(--surface); border: 1px solid var(--line-soft);
  border-radius: var(--radius); box-shadow: var(--shadow);
  margin-bottom: 22px; overflow: hidden;
}
.answer-head {
  padding: 22px 24px; cursor: pointer; user-select: none;
}
.answer-meta {
  display: flex; align-items: center; gap: 10px; margin-bottom: 8px;
  font-size: 12px; color: var(--ink-muted); text-transform: uppercase;
  letter-spacing: 0.05em; font-weight: 600;
}
.answer-meta .qid { background: var(--surface-2); padding: 2px 8px; border-radius: 999px; }
.answer-meta .cat { color: var(--ink-faint); }
.answer-meta .chev { margin-left: auto; transition: transform 0.25s ease; color: var(--ink-faint); }
.answer.open .answer-meta .chev { transform: rotate(90deg); }
.answer-q {
  font-family: "SF Pro Display", -apple-system, sans-serif;
  font-size: 19px; font-weight: 600; letter-spacing: -0.012em;
  color: var(--ink); margin-bottom: 12px;
}
.answer-a {
  color: var(--ink); font-size: 16px; line-height: 1.55;
}
.answer-badges {
  display: flex; flex-wrap: wrap; gap: 6px; margin-top: 14px;
}
.badge {
  font-size: 11px; font-weight: 600; padding: 4px 10px; border-radius: 999px;
  text-transform: uppercase; letter-spacing: 0.04em;
}
.badge.high   { background: rgba(48,176,74,.16); color: var(--done); }
.badge.medium { background: rgba(255,159,10,.16); color: var(--warn); }
.badge.low    { background: rgba(142,142,147,.18); color: var(--ink-muted); }
.badge.grounded { background: rgba(48,176,74,.12); color: var(--done); }
.badge.ungrounded { background: rgba(255,69,58,.16); color: var(--bad); }
.badge.src { background: var(--surface-2); color: var(--ink-muted); }
.badge.flag { background: rgba(255,159,10,.16); color: var(--warn); }

/* Audit drawer */
.audit {
  border-top: 1px solid var(--line-soft);
  padding: 0 24px;
  max-height: 0; overflow: hidden;
  transition: max-height 0.4s ease, padding 0.4s ease;
}
.answer.open .audit { max-height: 5000px; padding: 18px 24px 22px; }
.audit h4 {
  font-size: 11px; text-transform: uppercase; font-weight: 600;
  color: var(--ink-muted); letter-spacing: 0.06em; margin: 14px 0 8px;
}
.audit h4:first-child { margin-top: 0; }
.evidence-q {
  font-style: italic; color: var(--ink-muted); padding: 8px 12px;
  border-left: 3px solid var(--accent); background: var(--surface-2);
  border-radius: 0 6px 6px 0; margin-bottom: 6px; font-size: 14px;
}
.retrieval {
  font-size: 13px; padding: 10px 12px; background: var(--surface-2);
  border-radius: 8px; margin-bottom: 6px;
}
.retrieval .meta {
  display: flex; gap: 10px; font-size: 11px; color: var(--ink-faint);
  text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 4px;
  font-weight: 600;
}
.retrieval .meta .src { color: var(--ink); }
.retrieval .content { color: var(--ink-muted); }
.crit-grid {
  display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px; font-size: 13px;
}
.crit-item { padding: 8px 10px; border-radius: 6px; background: var(--surface-2); display: flex; align-items: center; gap: 8px; }
.crit-item .check.pass { color: var(--done); }
.crit-item .check.fail { color: var(--bad); }
.crit-notes { font-size: 13px; color: var(--ink-muted); padding: 10px 12px; background: var(--surface-2); border-radius: 8px; margin-top: 8px; }
.verif-grid { display: grid; gap: 6px; font-size: 13px; }
.verif-row { display: flex; gap: 8px; padding: 6px 10px; background: var(--surface-2); border-radius: 6px; }
.verif-row .k { color: var(--ink-muted); min-width: 140px; }

/* Cost */
.cost {
  margin-top: 36px; padding: 22px 24px;
  background: var(--surface); border: 1px solid var(--line-soft);
  border-radius: var(--radius); box-shadow: var(--shadow);
}
.cost h2 { font-size: 14px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em; color: var(--ink-muted); margin-bottom: 12px; }
.cost-total { font-size: 30px; font-weight: 600; letter-spacing: -0.02em; color: var(--ink); margin-bottom: 4px; font-variant-numeric: tabular-nums; }
.cost-sub { color: var(--ink-muted); font-size: 13px; margin-bottom: 16px; }
.cost-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.cost-table th, .cost-table td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--line-soft); }
.cost-table th { text-transform: uppercase; font-size: 11px; color: var(--ink-muted); letter-spacing: 0.04em; }
.cost-table td.num { text-align: right; font-variant-numeric: tabular-nums; }

footer {
  text-align: center; padding: 48px 0 64px; color: var(--ink-faint); font-size: 12px;
  border-top: 1px solid var(--line-soft); margin-top: 56px;
}
"""


def _esc(s) -> str:
    return html.escape(str(s)) if s is not None else ""


def _score_class(score: float) -> str:
    if score >= 90: return ""        # green (default)
    if score >= 70: return "warn"
    return "bad"


def _conf_letter(conf: str) -> str:
    return {"high": "H", "medium": "M", "low": "L"}.get(conf, "·")


def render(report: dict, out_path: Path) -> Path:
    answers = report.get("answers", []) or []
    review = report.get("review") or {}
    comp = report.get("composite") or {}
    cost = report.get("cost") or {}
    score = float(comp.get("score", 0))
    score_cls = _score_class(score)

    # KPIs
    n = len(answers)
    high = sum(1 for a in answers if a.get("confidence") == "high")
    grounded = sum(1 for a in answers if (a.get("verification") or {}).get("fully_grounded"))
    flags = sum(len(a.get("flags") or []) for a in answers)

    # Heatmap (confidence × answers)
    heat_cells = "".join(
        f'<div class="heat-cell {a.get("confidence", "low")[:1]}" title="{_esc(a.get("question_id"))} · {_esc(a.get("confidence"))}">{_esc(a.get("question_id"))}</div>'
        for a in answers
    )

    # Review block
    issues = review.get("issues") or []
    if issues:
        review_html = "".join(
            f'<div class="review-issue {_esc(i.get("severity", "info"))}">'
            f'<div class="head">[{_esc(i.get("severity", "info"))}] {_esc(i.get("kind"))} · {", ".join(_esc(q) for q in i.get("question_ids") or [])}</div>'
            f'<div>{_esc(i.get("summary"))}</div>'
            f'<div style="margin-top:6px;font-size:13px;color:var(--ink-muted)"><b>Fix:</b> {_esc(i.get("recommended_fix"))}</div>'
            f'</div>'
            for i in issues
        )
    else:
        review_html = '<div class="review-empty">No cross-answer issues detected — batch is consistent.</div>'

    if review.get("overall_assessment"):
        review_html += f'<div style="margin-top:14px;font-size:14px;color:var(--ink-muted);font-style:italic">{_esc(review["overall_assessment"])}</div>'

    # Answers
    answer_html_parts = []
    for a in answers:
        conf = a.get("confidence", "low")
        verif = a.get("verification") or {}
        grounded_badge = ('<span class="badge grounded">Grounded</span>'
                          if verif.get("fully_grounded")
                          else f'<span class="badge ungrounded">{len(verif.get("ungrounded_claims") or [])} ungrounded</span>')
        src_badges = "".join(
            f'<span class="badge src">{_esc(s)}</span>'
            for s in (a.get("sources") or [])
        )
        flag_badges = "".join(
            f'<span class="badge flag" title="{_esc(f)}">⚑ flag</span>' for f in (a.get("flags") or [])
        )

        # Evidence quotes
        ev_html = "".join(
            f'<div class="evidence-q">“{_esc(q)}”</div>'
            for q in (a.get("evidence_quotes") or [])
        )

        # Retrieval audit
        retrieved = a.get("retrieved") or []
        retr_html = "".join(
            f'<div class="retrieval">'
            f'<div class="meta"><span class="src">{_esc(r.get("source"))}</span>'
            f'<span>rerank {r.get("rerank_score") or "—"}</span>'
            f'<span>bm25 {r.get("bm25_score")}</span></div>'
            f'<div class="content">{_esc((r.get("content") or "")[:240])}{"…" if len(r.get("content") or "") > 240 else ""}</div>'
            f'</div>'
            for r in retrieved
        )

        # Critique
        crit = a.get("critique") or {}
        if crit:
            crit_items = "".join(
                f'<div class="crit-item"><span class="check {"pass" if crit.get(k) else "fail"}">{"✓" if crit.get(k) else "✗"}</span>{_esc(k)}</div>'
                for k in ("grounded", "cited_correctly", "confidence_calibrated",
                          "tone_professional", "addresses_question")
            )
            crit_html = f'<div class="crit-grid">{crit_items}</div>'
            if crit.get("should_revise"):
                crit_html += f'<div class="crit-notes"><b>Revision notes (applied):</b> {_esc(crit.get("revision_notes"))}</div>'
        else:
            crit_html = '<div class="crit-notes">No critique performed.</div>'

        # Verification details
        verif_html = (
            f'<div class="verif-grid">'
            f'<div class="verif-row"><span class="k">Fully grounded</span><span>{"yes" if verif.get("fully_grounded") else "no"}</span></div>'
            f'<div class="verif-row"><span class="k">Grounded claims ({len(verif.get("grounded_claims") or [])})</span><span>{", ".join(_esc(c) for c in (verif.get("grounded_claims") or [])[:8]) or "—"}</span></div>'
            f'<div class="verif-row"><span class="k">Ungrounded claims ({len(verif.get("ungrounded_claims") or [])})</span><span>{", ".join(_esc(c) for c in (verif.get("ungrounded_claims") or [])) or "—"}</span></div>'
            f'<div class="verif-row"><span class="k">Citations resolved</span><span>{len(verif.get("cited_sources_resolved") or [])}</span></div>'
            f'</div>'
        )

        flags_html = ""
        if a.get("flags"):
            flags_html = '<h4>Flags for human review</h4>' + "".join(
                f'<div class="crit-notes">{_esc(f)}</div>' for f in a["flags"]
            )

        answer_html_parts.append(f"""
<article class="answer" onclick="this.classList.toggle('open')">
  <div class="answer-head">
    <div class="answer-meta">
      <span class="qid">{_esc(a.get('question_id'))}</span>
      <span class="cat">{_esc(a.get('category'))}</span>
      <span class="chev">›</span>
    </div>
    <div class="answer-q">{_esc(a.get('question'))}</div>
    <div class="answer-a">{_esc(a.get('answer'))}</div>
    <div class="answer-badges">
      <span class="badge {conf}">{conf.title()} confidence</span>
      {grounded_badge}
      {src_badges}
      {flag_badges}
    </div>
  </div>
  <div class="audit" onclick="event.stopPropagation()">
    {f'<h4>Evidence quotes (verbatim from sources)</h4>{ev_html}' if ev_html else ''}
    <h4>Retrieval — top {len(retrieved)} candidates (rerank + BM25)</h4>
    {retr_html or '<div class="crit-notes">No retrieval (KB-miss).</div>'}
    <h4>Critique verdict</h4>
    {crit_html}
    <h4>Citation verification</h4>
    {verif_html}
    {flags_html}
  </div>
</article>""")

    # Cost
    cost_rows = "".join(
        f'<tr><td>{_esc(stage)}</td>'
        f'<td class="num">{s["calls"]}</td>'
        f'<td class="num">{s["input_tokens"]:,}</td>'
        f'<td class="num">{s["output_tokens"]:,}</td>'
        f'<td class="num">${s["cost"]:.4f}</td>'
        f'<td class="num">{s["elapsed_ms"]:.0f}</td></tr>'
        for stage, s in (cost.get("by_stage") or {}).items()
    )

    doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>Pro · {_esc(report.get('rfp_name'))} · {score:.0f}/100</title>
<style>{_CSS}</style>
</head>
<body>

<header class="hero">
  <div class="shell">
    <span class="pill">Hackathon Pro · RFP response</span>
    <h1>{_esc(report.get('rfp_name'))}</h1>
    <div class="sub">{n} questions · grounded {grounded}/{n} · ${cost.get('total_cost', 0):.4f} · {report.get('elapsed_s', 0):.1f}s</div>

    <div class="score-panel">
      <div class="score-display">
        <div class="score-num {score_cls}">{score:.1f}</div>
        <div class="score-suffix">/ 100</div>
      </div>
      <div class="score-bars">
        <div class="bar-cell"><div class="lbl">Source coverage</div><div class="val">{comp.get('source_coverage', 0):.1f}%</div><div class="weight">weight 30%</div><div class="bar"><span style="width:{comp.get('source_coverage', 0)}%"></span></div></div>
        <div class="bar-cell"><div class="lbl">Confidence index</div><div class="val">{comp.get('confidence_index', 0):.1f}%</div><div class="weight">weight 30%</div><div class="bar"><span style="width:{comp.get('confidence_index', 0)}%"></span></div></div>
        <div class="bar-cell"><div class="lbl">Grounding rate</div><div class="val">{comp.get('grounding_rate', 0):.1f}%</div><div class="weight">weight 25%</div><div class="bar"><span style="width:{comp.get('grounding_rate', 0)}%"></span></div></div>
        <div class="bar-cell"><div class="lbl">Reviewer clean</div><div class="val">{comp.get('reviewer_clean', 0):.1f}%</div><div class="weight">weight 15%</div><div class="bar"><span style="width:{comp.get('reviewer_clean', 0)}%"></span></div></div>
      </div>

      <div class="kpis">
        <div class="kpi"><div class="n">{n}</div><div class="l">Questions</div></div>
        <div class="kpi"><div class="n">{high}/{n}</div><div class="l">High confidence</div></div>
        <div class="kpi"><div class="n">{grounded}/{n}</div><div class="l">Fully grounded</div></div>
        <div class="kpi"><div class="n">{flags}</div><div class="l">Human-review flags</div></div>
      </div>

      <div class="heatmap">
        <h2>Confidence heatmap (one cell per answer)</h2>
        <div class="heatmap-row">{heat_cells}</div>
      </div>
    </div>
  </div>
</header>

<main class="shell">
  <section class="review-block">
    <h2>Cross-answer review</h2>
    {review_html}
  </section>

  <section class="answers">
    {''.join(answer_html_parts)}
  </section>

  <section class="cost">
    <h2>Cost &amp; telemetry</h2>
    <div class="cost-total">${cost.get('total_cost', 0):.4f}</div>
    <div class="cost-sub">{cost.get('total_calls', 0)} API calls · {cost.get('total_input_tokens', 0):,} input tokens · {cost.get('total_output_tokens', 0):,} output tokens · {cost.get('wall_clock_s', 0):.1f}s wall clock</div>
    <table class="cost-table">
      <thead><tr><th>Stage</th><th>Calls</th><th>In tok</th><th>Out tok</th><th>Cost</th><th>ms</th></tr></thead>
      <tbody>{cost_rows}</tbody>
    </table>
  </section>
</main>

<footer>
  Pro · generated by day2/04_agent-build-hackathon/pro · open in any browser, no build step
</footer>

</body>
</html>"""

    out_path.write_text(doc)
    return out_path
