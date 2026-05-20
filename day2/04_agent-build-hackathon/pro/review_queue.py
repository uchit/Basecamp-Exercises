"""Human-review queue HTML — a single self-contained page listing every
flagged answer across recent runs with one-click approve / edit affordances.

Each row shows: run_id, question, confidence, flag count, link to viewer +
quick Approve / Edit buttons that POST to a local feedback endpoint. The
buttons use the Fetch API against a placeholder URL — wire to a real
endpoint by setting REVIEW_API_URL.
"""
from __future__ import annotations

import html
import sqlite3
from pathlib import Path


_CSS = """
:root { --bg:#fbfbfd;--surface:#fff;--ink:#1d1d1f;--muted:#6e6e73;
  --line:#ececee;--accent:#0071e3;--warn:#ff9f0a;--bad:#ff453a;--ok:#30b04a; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#000;--surface:#1c1c1e;--ink:#f5f5f7;--muted:#98989d;
    --line:#2c2c2e;--accent:#2997ff; } }
* { margin:0;padding:0;box-sizing:border-box; }
html,body { background:var(--bg);color:var(--ink);
  font:15px/1.5 -apple-system,BlinkMacSystemFont,"SF Pro Text",sans-serif; }
.shell { max-width:1180px;margin:0 auto;padding:40px 24px; }
h1 { font-size:32px;font-weight:600;letter-spacing:-0.02em;margin-bottom:6px; }
.sub { color:var(--muted);margin-bottom:28px; }
.kpis { display:flex;gap:14px;margin-bottom:28px;flex-wrap:wrap; }
.kpi { background:var(--surface);border:1px solid var(--line);border-radius:12px;
  padding:14px 18px;min-width:140px; }
.kpi .n { font-size:24px;font-weight:600;font-variant-numeric:tabular-nums; }
.kpi .l { font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:0.05em; }
table { width:100%;border-collapse:collapse;background:var(--surface);
  border:1px solid var(--line);border-radius:12px;overflow:hidden; }
th,td { padding:10px 14px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top; }
th { font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:0.04em;
  background:rgba(0,0,0,0.02);font-weight:600; }
tr:last-child td { border-bottom:none; }
.q-text { font-size:14px;max-width:520px; }
.confidence { padding:2px 8px;border-radius:999px;font-size:11px;font-weight:600;text-transform:uppercase; }
.confidence.high { background:rgba(48,176,74,.16);color:var(--ok); }
.confidence.medium { background:rgba(255,159,10,.16);color:var(--warn); }
.confidence.low { background:rgba(255,69,58,.16);color:var(--bad); }
.flag-pill { padding:2px 8px;border-radius:999px;font-size:11px;
  background:rgba(255,159,10,.16);color:var(--warn);font-weight:600; }
.actions button { padding:5px 12px;border-radius:8px;border:1px solid var(--line);
  background:var(--surface);color:var(--ink);font-weight:500;cursor:pointer;
  margin-right:4px;font-size:12px; }
.actions button.approve { background:var(--ok);color:#fff;border-color:var(--ok); }
.actions button.edit { background:var(--surface);color:var(--ink); }
.empty { padding:60px 20px;text-align:center;color:var(--muted); }
"""


_JS = """
const API = window.REVIEW_API_URL || null;
async function post(verdict, runId, qid) {
  if (!API) {
    alert(`[${verdict}] ${runId}/${qid} — set window.REVIEW_API_URL to wire to a backend.`);
    return;
  }
  await fetch(API, { method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ run_id: runId, question_id: qid, verdict })});
  alert(`${verdict} recorded`);
}
"""


def render_from_sqlite(db_path: Path, out_path: Path,
                        *, min_flags: int = 1) -> Path:
    """Build the queue HTML by reading the audit_db for answers with flags
    or low confidence in recent runs."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not db_path.exists():
        # Empty queue.
        out_path.write_text(_empty_html())
        return out_path

    conn = sqlite3.connect(str(db_path))
    rows = conn.execute("""
        SELECT a.run_id, a.question_id, a.category, a.confidence,
               a.answer_text, a.flags_json, a.fully_grounded,
               r.rfp_name, r.started_at
        FROM answers a
        JOIN runs r ON r.run_id = a.run_id
        WHERE (a.flags_json != '[]' OR a.confidence = 'low' OR a.fully_grounded = 0)
        ORDER BY r.started_at DESC, a.question_id
    """).fetchall()
    conn.close()

    n = len(rows)
    n_low = sum(1 for r in rows if r[3] == "low")
    n_ungrounded = sum(1 for r in rows if r[6] == 0)

    import json as _json
    body_rows = []
    for r in rows:
        run_id, qid, cat, conf, ans, flags_json, grounded, rfp, started = r
        flags = _json.loads(flags_json or "[]")
        flag_pills = "".join(f'<span class="flag-pill">{html.escape(f[:30])}</span> ' for f in flags[:3])
        body_rows.append(f"""
<tr>
  <td>{html.escape(rfp or '')}<br><small style="color:var(--muted);font-family:ui-monospace,Menlo,monospace">{html.escape(run_id[:12])}</small></td>
  <td><strong>{html.escape(qid or '')}</strong></td>
  <td class="q-text">{html.escape((ans or '')[:240])}{'…' if len(ans or '') > 240 else ''}</td>
  <td><span class="confidence {conf or 'low'}">{html.escape((conf or 'low').upper())}</span></td>
  <td>{flag_pills or '—'}</td>
  <td class="actions">
    <button class="approve" onclick="post('approve','{run_id}','{qid}')">Approve</button>
    <button class="edit" onclick="post('edit','{run_id}','{qid}')">Edit</button>
  </td>
</tr>""")

    rows_html = "\n".join(body_rows) or (
        '<tr><td colspan="6" class="empty">No flagged answers in audit log. Queue is clean.</td></tr>'
    )

    doc = f"""<!doctype html><html><head><meta charset="utf-8" />
<title>Pro · Human review queue</title>
<style>{_CSS}</style></head><body>
<div class="shell">
<h1>Review queue</h1>
<p class="sub">Answers from recent runs that carry a human-review flag, were marked low confidence, or didn't fully ground.</p>
<div class="kpis">
  <div class="kpi"><div class="n">{n}</div><div class="l">Awaiting review</div></div>
  <div class="kpi"><div class="n">{n_low}</div><div class="l">Low confidence</div></div>
  <div class="kpi"><div class="n">{n_ungrounded}</div><div class="l">Not fully grounded</div></div>
</div>
<table>
<thead><tr><th>RFP / run</th><th>Q</th><th>Answer</th><th>Conf.</th><th>Flags</th><th>Action</th></tr></thead>
<tbody>{rows_html}</tbody>
</table>
</div>
<script>{_JS}</script>
</body></html>"""
    out_path.write_text(doc)
    return out_path


def _empty_html() -> str:
    return f"""<!doctype html><html><head><meta charset="utf-8" />
<title>Pro · Human review queue</title>
<style>{_CSS}</style></head><body>
<div class="shell"><h1>Review queue</h1><p class="sub">Audit database is empty — no runs to review yet.</p></div>
</body></html>"""
