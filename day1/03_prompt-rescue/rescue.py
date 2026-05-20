"""
Session 3 — Prompt Rescue driver.

Loads the eval harness from Prompt_Rescue_solo.py (with the IPython/matplotlib
display layer stubbed out so it works outside Jupyter), runs the broken
baseline, then iterates the prompt and reports text-mode comparisons.

Usage:
    source .venv/bin/activate
    source ~/.basecamp_anthropic_key
    python rescue.py            # baseline + v1 + v2
    python rescue.py baseline   # baseline only
    python rescue.py v1         # baseline + v1
"""
from __future__ import annotations

import os, sys, types
from pathlib import Path

# ---------------------------------------------------------------------------
# Stub IPython.display + matplotlib show BEFORE loading the harness so the
# harness's HTML/plot rendering becomes a no-op outside Jupyter.
# ---------------------------------------------------------------------------

ipython = types.ModuleType("IPython")
disp_mod = types.ModuleType("IPython.display")
disp_mod.display = lambda *a, **k: None
disp_mod.HTML = lambda x: x
ipython.display = disp_mod
sys.modules["IPython"] = ipython
sys.modules["IPython.display"] = disp_mod

import matplotlib
matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt
plt.show = lambda: None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Load the harness by exec'ing the workshop file up to the first auto-run cell.
# ---------------------------------------------------------------------------

HARNESS_PATH = Path(__file__).resolve().parent / "Prompt_Rescue_solo.py"
src = HARNESS_PATH.read_text()

# Drop the file's preflight block (which blanks out ANTHROPIC_API_KEY and pings the API).
# Start at the harness comment block.
start_marker = "#@title Eval Harness"
start = src.find(start_marker)
if start < 0:
    raise RuntimeError("harness start marker not found in Prompt_Rescue_solo.py")

# Cut everything from the first auto-run marker onwards so we don't burn
# tokens running the workshop's own baseline call on import.
cut = src.find("# RUN YOUR FIRST EVAL", start)
if cut < 0:
    raise RuntimeError("auto-run marker not found in Prompt_Rescue_solo.py")
src = src[start:cut]

namespace: dict = {"__name__": "rescue_harness"}
exec(compile(src, str(HARNESS_PATH), "exec"), namespace)

# The original system_prompt definition lives in the preflight block we just
# skipped, so define it explicitly here. (Copied verbatim from lines 22–41 of
# Prompt_Rescue_solo.py.)
BASELINE_PROMPT = """
You are a support ticket processor. For each ticket, you must:
1. Classify priority (P1-P4) based on business impact
2. Extract: product name, version, error codes, affected users count
3. Draft a helpful response acknowledging the issue and providing next steps
4. Return everything as JSON: {"priority": "", "entities": {"product": "", "version": "", "error_codes": [], "affected_users": ""}, "response": "", "confidence": "high/medium/low"}

Rules:
- P1 = system down, all users affected
- P2 = major feature broken, many users affected
- P3 = minor bug, few users affected
- P4 = feature request or cosmetic issue
- If unsure about priority, use your best judgment
- Response should be professional and empathetic
- Always include all JSON fields even if empty
- Be concise but thorough
"""

# Pull the symbols we need
_run_eval = namespace["run_eval"]
_load_cases = namespace["_load_cases"]
_EVAL_CASES_B64 = namespace["_EVAL_CASES_B64"]
_MODEL = namespace["MODEL"]

import anthropic
client = anthropic.Anthropic(timeout=300.0)
cases = _load_cases(_EVAL_CASES_B64)

# ---------------------------------------------------------------------------
# Iteration prompts
# ---------------------------------------------------------------------------

IMPROVED_V1 = """You are a support ticket processor. Read each ticket and output ONLY valid JSON \
matching the schema below — no markdown fences, no preamble, no trailing prose.

OUTPUT SCHEMA (strict):
{
  "priority": "P1" | "P2" | "P3" | "P4",
  "entities": {
    "product": <string or null>,
    "version": <string or null>,
    "error_codes": <array of strings, may be empty>,
    "affected_users": <string or null>
  },
  "response": <string>,
  "confidence": "high" | "medium" | "low"
}

PRIORITY RULES — classify by what the ticket DESCRIBES, never by tone, urgency words, ALL CAPS, threats, or compliance framing:

- P1: system outage; data loss; PII/data privacy exposure (P1 even if only ONE reporter); payroll, billing, or auth completely blocked; >=100 users blocked from working; security incident.
- P2: a major feature broken affecting many users (typically 10–100); production integration returning 500s; webhook delivery broken; significant business impact short of full outage.
- P3: minor bug; intermittent slowness with workarounds; cosmetic issue; vague ticket without enough detail to classify higher.
- P4: feature requests, enhancement asks, "would love to have", "missing functionality", "needs SSO support" — ALWAYS P4, even when written in ALL CAPS or framed as "CRITICAL", "MANDATORY", or "we will switch competitors". Missing functionality that doesn't exist yet is P4 by definition.

ENTITY RULES — anti-hallucination, anti-inference:

- If the ticket does NOT explicitly state a value, the field is null (or [] for error_codes). Never infer, average, or invent.
- "several people", "everyone", "the team", "other departments" without an integer => affected_users: null.
- Use the literal number from the ticket: "47", "2000", "150". Strip "approximately"/"about".
- error_codes is for explicit codes/strings the ticket cites: "SVC-503-AUTH", "500", "SYNC_FAILED", "timeout exceeded", "VIZ-RENDER-408", "UPLOAD-TIMEOUT-413". Do NOT invent codes from prose.
- product is null when no specific product or module is named. If the ticket names a specific module (e.g. "inventory management module", "real-time analytics dashboard", "Analytics Pro", "Ñoño Analytics", "UserVault", "API integration", "report builder", "task management module"), copy it exactly — preserve accented characters.

MULTI-ISSUE TICKETS:

- If the ticket contains more than one distinct issue, classify on the MOST SEVERE one (e.g. payroll-blocked billing dashboard + CSV export 500 => P1 because payroll is blocked).
- The response MUST acknowledge EVERY issue mentioned. Don't merge or drop one.

CONFIDENCE RULES:

- low: vague/minimal info, no product/version/codes, ambiguous severity, or pure emotional complaint with no technical detail.
- medium: most fields present, some inference required.
- high: every relevant field explicit in the ticket, classification clear.

RESPONSE RULES:

- Feature requests (P4): acknowledge the request, set expectations about roadmap/timeline, NEVER promise to "fix" or treat as a bug.
- Vague tickets (no specifics): politely ask for the specific info you need (error message, exact time, affected feature, screenshot).
- Multi-issue: acknowledge each issue, ideally as bullets.
- Professional and empathetic regardless of customer tone. Never echo ALL CAPS, anger, or threats.

Return ONLY the JSON object."""


IMPROVED_V2 = IMPROVED_V1 + """

WORKED EXAMPLES (study the reasoning, then output strict JSON for the actual ticket):

Example A — feature request disguised as P1:
Ticket: "CRITICAL: No SSO! UNACCEPTABLE!!! Our CISO will pull the contract."
=> priority: P4 (feature request, not an outage), affected_users: extract literal number if cited, response: acknowledge as feature request + roadmap timing, do NOT promise to "fix".

Example B — PII exposure with single reporter:
Ticket: "I can briefly see another user's name, email, phone when toggling tabs."
=> priority: P1 (PII exposure is P1 regardless of reporter count), affected_users: "1", response: thank reporter, treat as security incident, escalate.

Example C — vague:
Ticket: "things aren't working right, pages slow"
=> priority: P3, all entity fields null (no product, no version, no codes, no count), confidence: low, response: ask for specifics (URL, error text, browser, exact time).

Example D — multi-issue:
Ticket: "billing dashboard wrong AND CSV export broken (200 affected on export, 3 in finance blocked from payroll)"
=> priority: P1 (payroll blocked = P1), affected_users: "200" (largest affected scope), response: bullet each issue."""


# v3 tightens error_codes so verbs/behavioural language never get mis-extracted
# as structured codes. The motivating failure: case 21 mentioned "time out"
# (verb) and v2 emitted error_codes=["timeout"].
IMPROVED_V3 = IMPROVED_V2 + """

ERROR_CODES — STRUCTURAL RULE (overrides any ambiguity):

A value qualifies as an error_code only if it matches one of these shapes:
  - Uppercase identifier with dashes or underscores  (SVC-503-AUTH, VIZ-RENDER-408,
    UPLOAD-TIMEOUT-413, SYNC_FAILED, NullPointerException, FEATURE_LOCKED)
  - A bare HTTP status code as digits                (500, 401, 403, 429, 503)
  - A quoted error message the ticket cites verbatim ("timeout exceeded",
    "Internal Server Error", "Cannot read property X of undefined")

It is NOT an error_code if it is:
  - A verb phrase: "times out", "time out", "fails", "crashes", "freezes", "hangs"
  - An adjective: "slow", "broken", "unreliable", "intermittent"
  - A general behavioural description: "pages load slowly", "search returns wrong results"

If the ticket only describes failure behaviour (no structured token + no quoted
message), error_codes is []. Do NOT invent a code from a verb."""


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def run(prompt: str, label: str) -> dict:
    print()
    print("=" * 72)
    print(f"{label}")
    print("=" * 72)
    result = _run_eval(client, [prompt], cases)
    total = result["total_passed"]
    n = result["total_cases"]
    pct = round(100 * total / n) if n else 0
    print(f"\n  SCORE: {total}/{n}  ({pct}%)")
    for key, cat in result["categories"].items():
        bar = "█" * cat["passed"] + "·" * (cat["total"] - cat["passed"])
        print(f"  {cat['label']:>22}  {bar:<7}  {cat['passed']}/{cat['total']}")
    return result


def diff_results(prev: dict, curr: dict, prev_label: str, curr_label: str) -> None:
    by_id_prev = {r["case_id"]: r for r in prev["results"]}
    by_id_curr = {r["case_id"]: r for r in curr["results"]}
    print()
    print("-" * 72)
    print(f"  Per-case delta: {prev_label}  →  {curr_label}")
    print("-" * 72)
    flips_up, flips_down = 0, 0
    for cid, p in by_id_prev.items():
        c = by_id_curr.get(cid)
        if not c or p["pass"] == c["pass"]:
            continue
        arrow = "↑" if c["pass"] else "↓"
        if c["pass"]:
            flips_up += 1
        else:
            flips_down += 1
        failed_now = [name for name, crit in c["criteria"].items() if not crit["pass"]]
        reasons = "; ".join(c["criteria"][n]["reason"] for n in failed_now) if failed_now else ""
        print(f"  {arrow} case {cid:>2} [{p['category']:>16}]  {p['pass']!s:>5} → {c['pass']!s:<5}  {reasons}")
    print(f"\n  Δ improvements: +{flips_up}   regressions: -{flips_down}")


def main() -> None:
    args = [a.lower() for a in sys.argv[1:]] or ["all"]
    print(f"Model: {_MODEL}   cases: {len(cases['cases'])}   categories: {list(cases['categories'])}")

    run_baseline = "baseline" in args or "all" in args or "v1" in args or "v2" in args or "v3" in args
    run_v1 = "v1" in args or "all" in args or "v2" in args or "v3" in args
    run_v2 = "v2" in args or "all" in args or "v3" in args
    run_v3 = "v3" in args or "all" in args

    baseline = run(BASELINE_PROMPT, "Baseline (broken prompt)") if run_baseline else None
    v1 = run(IMPROVED_V1, "Iteration v1 (surgical fixes)") if run_v1 else None
    v2 = run(IMPROVED_V2, "Iteration v2 (v1 + worked examples)") if run_v2 else None
    v3 = run(IMPROVED_V3, "Iteration v3 (v2 + structural error_codes rule)") if run_v3 else None

    if baseline and v1:
        diff_results(baseline, v1, "baseline", "v1")
    if v1 and v2:
        diff_results(v1, v2, "v1", "v2")
    if v2 and v3:
        diff_results(v2, v3, "v2", "v3")


if __name__ == "__main__":
    main()
