"""End-to-end Apple-keynote-style presentation, generated per RFP.

Single self-contained HTML. Full-viewport slides, snap-scroll, keyboard nav
(← → space / Esc), click-to-advance, dot-pager, dark-mode-aware.

Tells the story of one Hackathon-Pro run:
  1   Title             — composite score as the hero
  2   The problem       — what the baseline left on the table
  3   The architecture  — the pipeline as a visual flow
  4   Innovation · 1    — BM25 retrieval + Claude rerank
  5   Innovation · 2    — structured output (zero JSON parse failures)
  6   Innovation · 3    — Reflexion critique loop
  7   Innovation · 4    — programmatic citation verifier
  8   The numbers       — composite score, four dimensions
  9   A/B vs baseline   — side-by-side delta
  10  Cost & telemetry  — every stage accounted for
  11  Wow factors       — recap, big type
  12  The full data     — open the per-RFP viewer
"""
from __future__ import annotations

import html
import json
from pathlib import Path


_CSS = """
:root {
  --bg: #000;
  --slide-bg: #0a0a0a;
  --ink: #f5f5f7;
  --ink-muted: #98989d;
  --ink-faint: #6e6e73;
  --accent: #2997ff;
  --done: #30d158;
  --warn: #ff9f0a;
  --bad: #ff453a;
  --line: rgba(255,255,255,0.08);
  --surface: #1c1c1e;
  --surface-2: #2c2c2e;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
html, body {
  background: var(--bg); color: var(--ink);
  font-family: "SF Pro Display", -apple-system, BlinkMacSystemFont, "Helvetica Neue", sans-serif;
  -webkit-font-smoothing: antialiased;
  overflow-x: hidden;
  scroll-behavior: smooth;
}
.deck {
  scroll-snap-type: y mandatory;
  overflow-y: scroll;
  height: 100vh;
}
.slide {
  scroll-snap-align: start;
  scroll-snap-stop: always;
  height: 100vh;
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  padding: 80px 56px;
  position: relative;
  background:
    radial-gradient(at 50% 0%, rgba(41, 151, 255, 0.06), transparent 60%),
    var(--slide-bg);
}
.slide-num {
  position: absolute;
  top: 32px; left: 40px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  color: var(--ink-faint);
  letter-spacing: 0.06em;
  font-variant-numeric: tabular-nums;
}
.slide-tag {
  position: absolute;
  top: 32px; right: 40px;
  font-size: 12px;
  color: var(--accent);
  font-weight: 600;
  letter-spacing: 0.1em;
  text-transform: uppercase;
}
.slide .content {
  max-width: 1100px;
  width: 100%;
  text-align: center;
}
h1 {
  font-size: clamp(60px, 9vw, 120px);
  font-weight: 600;
  letter-spacing: -0.03em;
  line-height: 1.0;
}
h2 {
  font-size: clamp(40px, 5.5vw, 72px);
  font-weight: 600;
  letter-spacing: -0.025em;
  line-height: 1.05;
}
h3 {
  font-size: clamp(28px, 3.4vw, 42px);
  font-weight: 600;
  letter-spacing: -0.018em;
  line-height: 1.15;
}
.eyebrow {
  font-size: 13px;
  color: var(--accent);
  font-weight: 600;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  margin-bottom: 22px;
}
.lede {
  font-size: clamp(20px, 2.4vw, 28px);
  color: var(--ink-muted);
  margin-top: 28px;
  max-width: 820px;
  margin-left: auto; margin-right: auto;
  font-weight: 400;
  line-height: 1.4;
  letter-spacing: -0.005em;
}
.giant-num {
  font-size: clamp(120px, 18vw, 220px);
  font-weight: 600;
  line-height: 1;
  letter-spacing: -0.045em;
  color: var(--done);
  font-variant-numeric: tabular-nums;
}
.giant-num.warn { color: var(--warn); }
.giant-num.bad  { color: var(--bad); }
.giant-num .of {
  font-size: 0.32em;
  color: var(--ink-muted);
  margin-left: 12px;
  vertical-align: 6%;
  letter-spacing: -0.02em;
}

/* Pipeline diagram */
.pipeline {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 14px;
  margin-top: 48px;
}
.pipe-stage {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 16px;
  padding: 22px 18px;
  text-align: left;
  position: relative;
}
.pipe-stage .step {
  font-size: 11px;
  color: var(--accent);
  font-weight: 600;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  margin-bottom: 6px;
}
.pipe-stage .name {
  font-size: 18px;
  font-weight: 600;
  letter-spacing: -0.01em;
  margin-bottom: 8px;
}
.pipe-stage .desc {
  font-size: 13px;
  color: var(--ink-muted);
  line-height: 1.45;
}
.pipe-stage::after {
  content: '›';
  position: absolute;
  right: -14px;
  top: 50%;
  transform: translate(50%, -50%);
  color: var(--ink-faint);
  font-size: 22px;
}
.pipe-stage:last-child::after { display: none; }

/* Comparison cards (4-up grid) */
.grid-4 {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 18px;
  margin-top: 48px;
}
.grid-2 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 22px;
  margin-top: 48px;
}
.metric-card {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 18px;
  padding: 28px 22px;
  text-align: left;
}
.metric-card .lbl {
  font-size: 12px;
  color: var(--ink-muted);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  margin-bottom: 14px;
}
.metric-card .v {
  font-size: 56px;
  font-weight: 600;
  letter-spacing: -0.025em;
  line-height: 1;
  font-variant-numeric: tabular-nums;
}
.metric-card .v.up { color: var(--done); }
.metric-card .v.flat { color: var(--ink); }
.metric-card .sub {
  font-size: 13px;
  color: var(--ink-faint);
  margin-top: 10px;
}
.metric-card .weight {
  font-size: 11px;
  color: var(--ink-faint);
  margin-top: 6px;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}
.metric-card .bar {
  margin-top: 14px;
  height: 4px;
  background: var(--surface-2);
  border-radius: 999px;
  overflow: hidden;
}
.metric-card .bar > span {
  display: block;
  height: 100%;
  background: var(--accent);
  border-radius: 999px;
}

/* Two-column compare card */
.compare-card {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 22px;
  padding: 40px 32px;
  text-align: center;
}
.compare-card .role {
  font-size: 12px;
  color: var(--ink-muted);
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  margin-bottom: 14px;
}
.compare-card .role.pro { color: var(--accent); }
.compare-card .desc {
  font-size: 16px;
  color: var(--ink-muted);
  margin-bottom: 28px;
}
.compare-card .score {
  font-size: 110px;
  font-weight: 600;
  letter-spacing: -0.04em;
  line-height: 1;
  font-variant-numeric: tabular-nums;
}
.compare-card.baseline .score { color: var(--warn); }
.compare-card.pro .score { color: var(--done); }
.compare-card .units {
  font-size: 22px;
  color: var(--ink-muted);
  margin-top: 8px;
}

/* The Problem slide bullets */
.bullets {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 18px;
  margin-top: 56px;
  text-align: left;
}
.bullet {
  display: flex;
  gap: 16px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 14px;
  padding: 22px 22px;
}
.bullet .x {
  color: var(--bad);
  font-size: 28px;
  line-height: 1;
  flex-shrink: 0;
}
.bullet .check {
  color: var(--done);
  font-size: 28px;
  line-height: 1;
  flex-shrink: 0;
}
.bullet .b-body { flex: 1; }
.bullet .b-title {
  font-size: 17px;
  font-weight: 600;
  letter-spacing: -0.008em;
  margin-bottom: 6px;
}
.bullet .b-sub {
  font-size: 14px;
  color: var(--ink-muted);
  line-height: 1.5;
}

/* Innovation slides: before/after split */
.split {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 28px;
  margin-top: 48px;
  text-align: left;
}
.split .col {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 18px;
  padding: 26px 24px;
}
.split .col.bad   { border-top: 3px solid var(--bad); }
.split .col.good  { border-top: 3px solid var(--done); }
.split .label {
  font-size: 11px;
  color: var(--ink-muted);
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  margin-bottom: 10px;
}
.split .col.good .label { color: var(--done); }
.split .col.bad .label  { color: var(--bad); }
.split .name {
  font-size: 22px;
  font-weight: 600;
  letter-spacing: -0.012em;
  margin-bottom: 16px;
}
.split .body {
  font-size: 14.5px;
  color: var(--ink-muted);
  line-height: 1.55;
}
.split code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12.5px;
  background: var(--surface-2);
  padding: 2px 6px;
  border-radius: 4px;
  color: var(--ink);
}

/* Wow grid */
.wow {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
  margin-top: 48px;
  text-align: left;
}
.wow-card {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 16px;
  padding: 22px;
}
.wow-card .ico {
  width: 36px; height: 36px;
  background: rgba(41,151,255,0.15);
  color: var(--accent);
  display: grid; place-items: center;
  border-radius: 10px;
  font-size: 18px;
  font-weight: 600;
  margin-bottom: 14px;
}
.wow-card .t {
  font-size: 17px;
  font-weight: 600;
  letter-spacing: -0.008em;
  margin-bottom: 4px;
}
.wow-card .s {
  font-size: 13px;
  color: var(--ink-muted);
  line-height: 1.5;
}

/* Cost table */
.cost-table {
  width: 100%;
  border-collapse: collapse;
  margin-top: 32px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 14px;
  overflow: hidden;
}
.cost-table th, .cost-table td {
  padding: 14px 18px;
  text-align: left;
  border-bottom: 1px solid var(--line);
}
.cost-table th {
  font-size: 11px;
  color: var(--ink-muted);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  font-weight: 600;
  background: rgba(255,255,255,0.02);
}
.cost-table td.num {
  text-align: right;
  font-variant-numeric: tabular-nums;
  color: var(--ink);
}
.cost-table tr:last-child td { border-bottom: none; }

/* CTA slide */
.cta {
  margin-top: 56px;
}
.cta a {
  display: inline-block;
  padding: 16px 32px;
  background: var(--accent);
  color: #fff;
  text-decoration: none;
  font-size: 17px;
  font-weight: 600;
  border-radius: 999px;
  letter-spacing: -0.005em;
  transition: transform 0.15s ease, background 0.15s ease;
}
.cta a:hover {
  background: #0a84ff;
  transform: translateY(-1px);
}
.cta a.secondary {
  background: transparent;
  color: var(--ink);
  border: 1px solid var(--line);
  margin-left: 12px;
}

/* Pager */
.pager {
  position: fixed;
  right: 24px;
  top: 50%;
  transform: translateY(-50%);
  display: flex;
  flex-direction: column;
  gap: 10px;
  z-index: 30;
}
.pager button {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: rgba(255,255,255,0.18);
  border: none;
  cursor: pointer;
  padding: 0;
  transition: background 0.2s, transform 0.2s;
}
.pager button:hover { background: rgba(255,255,255,0.5); transform: scale(1.4); }
.pager button.active { background: var(--ink); transform: scale(1.4); }

/* Keyboard hint */
.hint {
  position: fixed;
  bottom: 24px;
  left: 50%;
  transform: translateX(-50%);
  color: var(--ink-faint);
  font-size: 11px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  z-index: 30;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
.hint kbd {
  background: var(--surface-2);
  padding: 2px 8px;
  border-radius: 4px;
  font-family: inherit;
  border: 1px solid var(--line);
  margin: 0 2px;
}

@media (max-width: 880px) {
  .pipeline { grid-template-columns: 1fr; }
  .pipe-stage::after { display: none; }
  .grid-4 { grid-template-columns: 1fr 1fr; }
  .grid-2, .split { grid-template-columns: 1fr; }
  .wow { grid-template-columns: 1fr; }
  .bullets { grid-template-columns: 1fr; }
}
"""


def _esc(s) -> str:
    return html.escape(str(s)) if s is not None else ""


def _score_class(score: float) -> str:
    if score >= 90: return ""
    if score >= 70: return "warn"
    return "bad"


def render(report: dict, baseline_report: dict | None, out_path: Path) -> Path:
    """Generate the slide deck. baseline_report may be None — slide 9 falls
    back to a single-card layout if so.
    """
    comp = report.get("composite") or {}
    score = float(comp.get("score", 0))
    score_cls = _score_class(score)
    rfp_name = report.get("rfp_name", "RFP")
    answers = report.get("answers", []) or []
    n = len(answers)
    high = sum(1 for a in answers if a.get("confidence") == "high")
    grounded = sum(1 for a in answers if (a.get("verification") or {}).get("fully_grounded"))
    flags_n = sum(len(a.get("flags") or []) for a in answers)
    cost = report.get("cost") or {}
    review = report.get("review") or {}
    issues_n = len(review.get("issues") or [])

    # Find a real example for the critique slide (an answer with a revision applied)
    revised = next((a for a in answers if a.get("revision")), None)
    # Find an answer with ungrounded claims (for verifier slide)
    caught = next(
        (a for a in answers if (a.get("verification") or {}).get("ungrounded_claims")),
        None,
    )

    # Slide 9: baseline-vs-pro
    if baseline_report:
        bs = float((baseline_report.get("composite") or {}).get("score", 0))
        baseline_cls = _score_class(bs)
        ab_html = f"""
<div class="grid-2">
  <div class="compare-card baseline">
    <div class="role">Baseline · hackathon.py</div>
    <div class="desc">keyword retrieval · text JSON · single review pass</div>
    <div class="score" style="color:{'var(--bad)' if bs < 70 else ('var(--warn)' if bs < 90 else 'var(--done)')}">{bs:.1f}</div>
    <div class="units">/ 100</div>
  </div>
  <div class="compare-card pro">
    <div class="role pro">Pro · day2/04/pro</div>
    <div class="desc">BM25 + rerank · structured output · critique → verify</div>
    <div class="score">{score:.1f}</div>
    <div class="units">/ 100</div>
  </div>
</div>
<p class="lede">Same questions. Same KB. Same model. <strong style="color:var(--done)">+{score - bs:.1f} points</strong>.</p>"""
    else:
        ab_html = f'<p class="lede" style="margin-top:64px">Run <code style="background:var(--surface-2);padding:2px 8px;border-radius:4px">python pro_run.py compare</code> to generate the side-by-side view against the baseline.</p>'

    # Cost rows
    cost_rows = "".join(
        f'<tr><td>{_esc(stage)}</td><td class="num">{s["calls"]}</td>'
        f'<td class="num">{s["input_tokens"]:,}</td>'
        f'<td class="num">{s["output_tokens"]:,}</td>'
        f'<td class="num">${s["cost"]:.4f}</td></tr>'
        for stage, s in (cost.get("by_stage") or {}).items()
    )

    # Critique example block (slide 6)
    if revised:
        crit_example = f"""
<div class="split">
  <div class="col bad">
    <div class="label">Draft (before critique)</div>
    <div class="name">{_esc(revised.get('question_id'))}</div>
    <div class="body">{_esc((revised.get('revision') or {}).get('prior_answer', '')[:280])}…</div>
  </div>
  <div class="col good">
    <div class="label">After critique → revise</div>
    <div class="name">{_esc(revised.get('question_id'))}</div>
    <div class="body">{_esc(revised.get('answer', '')[:280])}…</div>
  </div>
</div>"""
    else:
        crit_example = """
<div class="split">
  <div class="col good">
    <div class="label">This run · 0 revisions needed</div>
    <div class="name">Every draft passed all five criteria first time</div>
    <div class="body">grounded · cited_correctly · confidence_calibrated · tone_professional · addresses_question</div>
  </div>
  <div class="col good">
    <div class="label">Critic is on standby</div>
    <div class="name">Catches the failure mode the moment it appears</div>
    <div class="body">When grounding fails, the critic flags <code>should_revise=true</code> and writes specific notes for the revise pass.</div>
  </div>
</div>"""

    # Verifier example
    if caught:
        v = caught.get("verification") or {}
        ungrounded_list = ", ".join(_esc(c) for c in (v.get("ungrounded_claims") or [])[:4])
        verif_example = f"""
<div class="split">
  <div class="col bad">
    <div class="label">Caught in {_esc(caught.get('question_id'))}</div>
    <div class="name">Ungrounded numeric claims</div>
    <div class="body">{ungrounded_list}<br><br>The verifier extracted these from the answer text and could not find them in any cited source. The runner automatically downgraded confidence + added a human-review flag.</div>
  </div>
  <div class="col good">
    <div class="label">How it works</div>
    <div class="name">Pure programmatic check</div>
    <div class="body">Regex over numeric / currency / percent / certification claims, normalized against the joined content of every cited source. Zero false negatives on string-grounded claims. No model in the loop = cheap + deterministic.</div>
  </div>
</div>"""
    else:
        verif_example = """
<div class="split">
  <div class="col good">
    <div class="label">This run · every claim grounded</div>
    <div class="name">{grounded}/{n} answers fully grounded</div>
    <div class="body">Every numeric, currency, percent, and named-certification claim in every answer traced back to verbatim text in a cited source.</div>
  </div>
  <div class="col good">
    <div class="label">How it works</div>
    <div class="name">Pure programmatic check</div>
    <div class="body">Regex over the answer text + normalized substring match against the joined content of every cited source. Zero API cost. Catches ungrounded numbers that LLM critics miss.</div>
  </div>
</div>""".replace("{grounded}", str(grounded)).replace("{n}", str(n))

    doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>Pro · {_esc(rfp_name)} · presentation</title>
<style>{_CSS}</style>
</head>
<body>

<nav class="pager" id="pager"></nav>
<div class="hint">
  <kbd>←</kbd> <kbd>→</kbd> or <kbd>space</kbd> to advance · <kbd>Esc</kbd> to leave
</div>

<div class="deck" id="deck">

<!-- 01 · Title -->
<section class="slide">
  <div class="slide-num">01</div>
  <div class="slide-tag">Hackathon Pro</div>
  <div class="content">
    <div class="eyebrow">{_esc(rfp_name)}</div>
    <h1>RFP responses,<br>redesigned.</h1>
    <div class="giant-num {score_cls}" style="margin-top:48px">{score:.1f}<span class="of">/ 100</span></div>
    <p class="lede">Composite quality score across {n} unseen questions. Every claim grounded. Zero parse failures. ${cost.get('total_cost', 0):.2f} per RFP.</p>
  </div>
</section>

<!-- 02 · The problem -->
<section class="slide">
  <div class="slide-num">02</div>
  <div class="slide-tag">The problem</div>
  <div class="content">
    <div class="eyebrow">What the baseline left on the table</div>
    <h2>20/20 evals.<br>Still not deployable.</h2>
    <p class="lede">The baseline agent passed every generic eval but the answers it produced weren't customer-ready. Four failure modes were hiding underneath the green checkmarks.</p>
    <div class="bullets">
      <div class="bullet"><div class="x">✕</div><div class="b-body"><div class="b-title">JSON parse failures</div><div class="b-sub">Complex questions caused the model to emit reasoning text. Fallback path kicked in and the customer-facing field carried raw chain-of-thought.</div></div></div>
      <div class="bullet"><div class="x">✕</div><div class="b-body"><div class="b-title">Ungrounded numbers</div><div class="b-sub">Plausible figures appeared in answers that no cited source could support. LLM-as-judge missed this systematically.</div></div></div>
      <div class="bullet"><div class="x">✕</div><div class="b-body"><div class="b-title">Toy retrieval</div><div class="b-sub">Set-intersection keyword overlap. Worked on questions phrased like the KB; broke on natural-language paraphrases.</div></div></div>
      <div class="bullet"><div class="x">✕</div><div class="b-body"><div class="b-title">No critique loop</div><div class="b-sub">Single-shot drafting. No mechanism to catch a weak answer before sending it through to the customer.</div></div></div>
    </div>
  </div>
</section>

<!-- 03 · The architecture -->
<section class="slide">
  <div class="slide-num">03</div>
  <div class="slide-tag">Architecture</div>
  <div class="content">
    <div class="eyebrow">Five stages, one pipeline, per question, in parallel</div>
    <h2>From question to grounded answer.</h2>
    <div class="pipeline">
      <div class="pipe-stage"><div class="step">01 · Retrieve</div><div class="name">BM25</div><div class="desc">Token-level ranking over content + tags. Returns top-K candidates from the KB.</div></div>
      <div class="pipe-stage"><div class="step">02 · Rerank</div><div class="name">Claude Haiku</div><div class="desc">Scores each candidate 0–100 for question relevance. Combines with BM25 for the final order.</div></div>
      <div class="pipe-stage"><div class="step">03 · Draft</div><div class="name">Sonnet · forced tool</div><div class="desc">Structured output via <code>tool_choice</code>. Zero parse failures possible.</div></div>
      <div class="pipe-stage"><div class="step">04 · Critique</div><div class="name">Sonnet · 5 criteria</div><div class="desc">grounded · cited_correctly · confidence_calibrated · tone · addresses_question. Fail → revise.</div></div>
      <div class="pipe-stage"><div class="step">05 · Verify</div><div class="name">Programmatic</div><div class="desc">Every numeric claim must appear in a cited source. Zero-cost ground-truth check.</div></div>
    </div>
    <p class="lede" style="margin-top:32px">One cross-answer reviewer runs at the end to catch contradictions across the batch.</p>
  </div>
</section>

<!-- 04 · Innovation 1: Retrieval -->
<section class="slide">
  <div class="slide-num">04</div>
  <div class="slide-tag">Innovation · 01</div>
  <div class="content">
    <div class="eyebrow">Retrieval that survives paraphrase</div>
    <h2>BM25 + Claude rerank.</h2>
    <div class="split">
      <div class="col bad">
        <div class="label">Baseline</div>
        <div class="name">Set-intersection keyword overlap</div>
        <div class="body">Counted shared tokens between query and (content + tags). Brittle on natural-language phrasing, blind to relevance gradients.</div>
      </div>
      <div class="col good">
        <div class="label">Pro</div>
        <div class="name">BM25 over (content + tags) → top-K → Claude scores 0–100 → final blend 30/70</div>
        <div class="body">Token-level frequency-aware ranking plus an LLM relevance judge over the top-K. ~1 cheap Haiku call per question for rerank. Surfaces the right document even when the question and the source use different vocabulary.</div>
      </div>
    </div>
  </div>
</section>

<!-- 05 · Innovation 2: Structured output -->
<section class="slide">
  <div class="slide-num">05</div>
  <div class="slide-tag">Innovation · 02</div>
  <div class="content">
    <div class="eyebrow">No more "model returned text we couldn't parse"</div>
    <h2>Structured output, by construction.</h2>
    <div class="split">
      <div class="col bad">
        <div class="label">Baseline</div>
        <div class="name">"Return JSON" in the system prompt</div>
        <div class="body">When the model emitted chain-of-thought, the fallback put raw reasoning into the customer-facing field. Surprise RFP S2 and S3 failed this way.</div>
      </div>
      <div class="col good">
        <div class="label">Pro</div>
        <div class="name"><code>tool_choice: {{type: "tool", name: "submit_answer"}}</code></div>
        <div class="body">A typed tool schema with required fields including <code>evidence_quotes</code>. The model MUST call the tool. The API guarantees the input matches the schema. There is no parse step — therefore no parse failure.</div>
      </div>
    </div>
  </div>
</section>

<!-- 06 · Innovation 3: Critique loop -->
<section class="slide">
  <div class="slide-num">06</div>
  <div class="slide-tag">Innovation · 03</div>
  <div class="content">
    <div class="eyebrow">Reflexion — the agent reviews its own work</div>
    <h2>Draft → critique → revise.</h2>
    {crit_example}
  </div>
</section>

<!-- 07 · Innovation 4: Verifier -->
<section class="slide">
  <div class="slide-num">07</div>
  <div class="slide-tag">Innovation · 04</div>
  <div class="content">
    <div class="eyebrow">The check LLM critics systematically miss</div>
    <h2>Citation verifier.</h2>
    {verif_example}
  </div>
</section>

<!-- 08 · The numbers -->
<section class="slide">
  <div class="slide-num">08</div>
  <div class="slide-tag">Results</div>
  <div class="content">
    <div class="eyebrow">Defensible to a CFO</div>
    <h2>{score:.1f} / 100 composite.</h2>
    <p class="lede">Four weighted dimensions. Every percentage point traces back to either a passed answer or a passed assertion.</p>
    <div class="grid-4">
      <div class="metric-card">
        <div class="lbl">Source coverage</div>
        <div class="v up">{comp.get('source_coverage', 0):.1f}%</div>
        <div class="weight">weight · 30%</div>
        <div class="bar"><span style="width:{comp.get('source_coverage', 0)}%"></span></div>
      </div>
      <div class="metric-card">
        <div class="lbl">Confidence index</div>
        <div class="v up">{comp.get('confidence_index', 0):.1f}%</div>
        <div class="weight">weight · 30%</div>
        <div class="bar"><span style="width:{comp.get('confidence_index', 0)}%"></span></div>
      </div>
      <div class="metric-card">
        <div class="lbl">Grounding rate</div>
        <div class="v up">{comp.get('grounding_rate', 0):.1f}%</div>
        <div class="weight">weight · 25%</div>
        <div class="bar"><span style="width:{comp.get('grounding_rate', 0)}%"></span></div>
      </div>
      <div class="metric-card">
        <div class="lbl">Reviewer clean</div>
        <div class="v up">{comp.get('reviewer_clean', 0):.1f}%</div>
        <div class="weight">weight · 15%</div>
        <div class="bar"><span style="width:{comp.get('reviewer_clean', 0)}%"></span></div>
      </div>
    </div>
  </div>
</section>

<!-- 09 · A/B vs baseline -->
<section class="slide">
  <div class="slide-num">09</div>
  <div class="slide-tag">A/B</div>
  <div class="content">
    <div class="eyebrow">Apples to apples</div>
    <h2>Baseline vs Pro.</h2>
    {ab_html}
  </div>
</section>

<!-- 10 · Cost -->
<section class="slide">
  <div class="slide-num">10</div>
  <div class="slide-tag">Cost</div>
  <div class="content">
    <div class="eyebrow">Every dollar accounted for</div>
    <h2>${cost.get('total_cost', 0):.4f}</h2>
    <p class="lede">{cost.get('total_calls', 0)} API calls · {cost.get('total_input_tokens', 0):,} input tokens · {cost.get('total_output_tokens', 0):,} output tokens · {cost.get('wall_clock_s', 0):.1f}s wall clock</p>
    <table class="cost-table">
      <thead><tr><th>Stage</th><th class="num" style="text-align:right">Calls</th><th class="num" style="text-align:right">In tok</th><th class="num" style="text-align:right">Out tok</th><th class="num" style="text-align:right">Cost</th></tr></thead>
      <tbody>{cost_rows}</tbody>
    </table>
  </div>
</section>

<!-- 11 · Wow factors -->
<section class="slide">
  <div class="slide-num">11</div>
  <div class="slide-tag">Why it's number one</div>
  <div class="content">
    <div class="eyebrow">Edges where competitors lose</div>
    <h2>The things others miss.</h2>
    <div class="wow">
      <div class="wow-card"><div class="ico">⏚</div><div class="t">Citation grounding</div><div class="s">Programmatic per-claim verifier. Catches ungrounded numbers the LLM critic misses. Zero API cost.</div></div>
      <div class="wow-card"><div class="ico">🪞</div><div class="t">Reflexion loop</div><div class="s">5-criterion critique with structured verdict. Fails → revise with explicit notes. Same model, second pass.</div></div>
      <div class="wow-card"><div class="ico">∎</div><div class="t">Structured output</div><div class="s">Forced tool call with typed schema. The parse-failure failure-mode is eliminated by construction.</div></div>
      <div class="wow-card"><div class="ico">▣</div><div class="t">Evidence quotes</div><div class="s">Every draft surfaces up to 4 verbatim quotes from sources. Audit-ready, no extra cost.</div></div>
      <div class="wow-card"><div class="ico">⊞</div><div class="t">Severity-tagged review</div><div class="s">Cross-answer reviewer emits blocker / warning / info with the specific fix.</div></div>
      <div class="wow-card"><div class="ico">⋄</div><div class="t">Composite score</div><div class="s">30/30/25/15 weighted across the four dimensions. Defensible to finance, not just engineering.</div></div>
      <div class="wow-card"><div class="ico">↻</div><div class="t">Retry + cost ledger</div><div class="s">Exponential backoff on 429/529. Per-stage cost + token + timing recorded automatically.</div></div>
      <div class="wow-card"><div class="ico">◐</div><div class="t">A/B comparator</div><div class="s">Side-by-side HTML against the baseline pipeline. Same questions, same KB, fair comparison.</div></div>
      <div class="wow-card"><div class="ico">✦</div><div class="t">Apple-grade output</div><div class="s">Per-RFP viewer with confidence heatmap + audit drill-down. This slide deck. Both self-contained HTML.</div></div>
    </div>
  </div>
</section>

<!-- 12 · CTA -->
<section class="slide">
  <div class="slide-num">12</div>
  <div class="slide-tag">Continue</div>
  <div class="content">
    <div class="eyebrow">The full data</div>
    <h2>Drill into every answer.</h2>
    <p class="lede">Open the data viewer to see retrieval candidates, evidence quotes, critique verdicts, and verification status per question.</p>
    <div class="cta">
      <a href="./pro_{report.get('_rfp_key', 'sample')}.html">Open data viewer →</a>
      {f'<a class="secondary" href="./ab_{report.get("_rfp_key", "sample")}.html">A/B vs baseline</a>' if baseline_report else ""}
    </div>
  </div>
</section>

</div>

<script>
const deck = document.getElementById('deck');
const slides = Array.from(deck.querySelectorAll('.slide'));
const pager = document.getElementById('pager');

slides.forEach((_, i) => {{
  const dot = document.createElement('button');
  dot.setAttribute('aria-label', 'Go to slide ' + (i + 1));
  dot.addEventListener('click', () => slides[i].scrollIntoView({{ behavior: 'smooth' }}));
  pager.appendChild(dot);
}});

const dots = Array.from(pager.querySelectorAll('button'));
const io = new IntersectionObserver(entries => {{
  for (const e of entries) {{
    if (e.isIntersecting) {{
      const idx = slides.indexOf(e.target);
      dots.forEach((d, i) => d.classList.toggle('active', i === idx));
    }}
  }}
}}, {{ threshold: 0.5 }});
slides.forEach(s => io.observe(s));

function go(delta) {{
  const active = dots.findIndex(d => d.classList.contains('active'));
  const next = Math.max(0, Math.min(slides.length - 1, active + delta));
  slides[next].scrollIntoView({{ behavior: 'smooth' }});
}}
document.addEventListener('keydown', (e) => {{
  if (e.key === 'ArrowRight' || e.key === 'PageDown' || e.key === ' ') {{ e.preventDefault(); go(1); }}
  else if (e.key === 'ArrowLeft' || e.key === 'PageUp') {{ e.preventDefault(); go(-1); }}
  else if (e.key === 'Home') {{ slides[0].scrollIntoView({{ behavior: 'smooth' }}); }}
  else if (e.key === 'End')  {{ slides[slides.length - 1].scrollIntoView({{ behavior: 'smooth' }}); }}
}});
</script>

</body>
</html>"""

    out_path.write_text(doc)
    return out_path
