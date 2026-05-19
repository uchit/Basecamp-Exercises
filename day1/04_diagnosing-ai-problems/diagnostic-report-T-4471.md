# Diagnostic Report — Meridian Multi-Agent Support System

**For:** Priya R., Meridian
**Re:** Northwind escalations (T-4471 + rate-limit incident)
**Method:** the four-step Diagnostic Loop from `diagnostic-framework.md`

---

## 1. Symptom

Restated unedited from Priya's email:

- **Incident A (T-4471).** Customer asked about an SSO failure + a $1,200 billing overcharge in one ticket. SSO got fixed; on the refund the agent told him to chase his account manager. Should have either processed the refund or escalated to a human.
- **Incident B (rate-limit ticket).** Customer asked why they were rate-limited. Agent read their plan config back ("10K req/min, check if you're over") and stopped. Real cause: customer-side retry-loop on one endpoint, visible in Meridian's metrics. `fetch_customer_v2_databricks` exists but "isn't getting called most times."
- **Caching.** "Trying to get caching working and not seeing any savings."
- **Mitigation already attempted.** "Added more examples to the coordinator prompt and more guidance on which tool to reach for when." Wants assurance these don't come back.

## 2. Hypotheses (formed before opening artifacts)

| # | Hypothesis | Signal in the email |
|---|---|---|
| H1 | **Single-dispatch coordinator** — one ticket, one specialist, no fan-out on multi-issue inputs | SSO got handled, billing got dropped. Classic "pick one category" routing. |
| H2 | **Tool descriptions too opaque to drive selection** | Priya named `fetch_customer_v2_databricks` specifically — engineering-speak name, no functional trigger. Adding "more guidance in the prompt" didn't help because the bug isn't in the prompt, it's in the tool descriptions. |
| H3 | **Subagents over-claim resolution and have no escalation path** | Agent told the customer "go to your account manager" — narrating an action instead of taking one. Usually means (a) no tool, (b) no instruction to escalate when stuck. |

Bonus: **H4 — cache placement is around dynamic content** (timestamps / request IDs), so every request invalidates.

## 3. Evidence (line-cited)

### H1 — Single-dispatch coordinator · CONFIRMED

1. **`system-prompt-coordinator.txt`, line 23:**
   > "If a ticket could fit two categories, pick the one the customer seems most blocked by."

   PROCESS step 5 reinforces it: "DELEGATE. Call spawn_specialist with the right category." Singular.

2. **`coordinator-tools.json`, `spawn_specialist` schema:**
   ```json
   "category": { "type": "string", "enum": ["billing", "technical", "account"] }
   ```
   The tool takes one `category`. No way to fan out without multiple calls — and the prompt never asks for multiple.

3. **`trace-T-4471-coordinator.json`:**
   - One `spawn_specialist({category: "account"})` call (toolu_05).
   - Coordinator's reasoning at toolu_05 acknowledged the dual-issue ticket but picked one: *"SSO failure with 40 people locked out is a security concern. Routing to the account specialist — they have the SSO tooling."*
   - Billing issue identified earlier in the trace and dropped silently.

### H2 — Opaque / duplicate tool descriptions · CONFIRMED

`coordinator-tools.json` is a tool graveyard. Five overlapping customer-lookup tools, none with trigger language:

| Tool | Description verbatim |
|---|---|
| `get_customer` | "Get customer information" |
| `fetch_customer_details` | "Fetches customer details" |
| `fetch_customer_v2_databricks` | "Queries the Databricks warehouse. v2 endpoint, use this not the old one." |
| `lookup_customer_info` | "Look up customer info by ID or email" |
| `customer_data_retrieval` | "Retrieves customer data." |

None say WHEN to call them. The one Priya called out describes the implementation ("Databricks v2 endpoint"), not the trigger (*"use this when a customer reports rate limiting, usage anomalies, or unexplained API behavior"*).

Plus five pure-noise tools: `helper`, `process`, `validate`, `get_data`, `tool_3_v2`. The model reads past these every turn — context budget waste, selection noise.

Coordinator system prompt mitigation (lines 25–28):
> "You have many tools available. Think carefully about which one to use. When you need customer information, choose the appropriate lookup tool."

Generic encouragement, not selection criteria.

**Trace evidence on T-4471:**
- toolu_02 `get_customer({id: "northwind-traders"})` → 404
- toolu_03 `lookup_customer_info({identifier: "devops@northwind-traders.example"})` → 200
- toolu_04 `fetch_customer_details({customer_id: "cust_8fK2mQ"})` → 200

Three different lookup tools to identify one customer. The model is spinning through tools because their descriptions don't differentiate.

**For Incident B:** with `fetch_customer_v2_databricks` described as "Queries the Databricks warehouse", a model handed a rate-limit ticket has no signal to associate that tool with the problem. It falls back to plan-config knowledge. The tool isn't broken; its description is invisible to selection.

### H3 — Sub-agent over-claims, no escalation path · CONFIRMED

Three stacked failures:

1. **No `escalate_to_human` in subagent toolsets.** `subagent-account-tools.json` has 8 tools — `get_ticket`, `lookup_customer_by_email`, `check_sso_config`, `check_audit_log`, `get_workspace_account_summary`, `list_workspace_users`, `modify_permissions`, `reset_2fa`. No escalate. Same for billing and technical. Only the coordinator has it. So when a specialist hits out-of-scope, it has no path except to narrate.

2. **`system-prompt-subagent-account.txt` doesn't require verification.** Lines 17–20 say *"Write up what you found. For SSO issues, include the exact error state and the fix steps."* No instruction like *"every action you report must be backed by a tool call."*

3. **Hallucinated action in `trace-T-4471-subagent-account.json` (line 132):**
   > "Billing: The $1,200 overcharge is outside what I can action directly. Advised them to escalate to their account manager with the Feb 26 downgrade confirmation email…"

   No tool was called to "advise them." The "advised them" action is invented — the specialist can't communicate with the customer; only the coordinator can. The subagent narrated an action it had no mechanism to perform, and the coordinator passed that narration to the customer in `write_response`:
   > "On the refund — I wasn't able to look into the March charge from here. You'll want to reach out to your account manager…"

   Marked `"resolution_status": "resolved"`. Not escalated. The coordinator's 5 examples are all happy-path `write_response`; `escalate_to_human` never appears. The model learned: responses get written, not escalated.

### H4 — Cache placement · CONFIRMED

`trace-T-4471-coordinator.json` usage:
```json
"usage": {
  "input_tokens": 24600,
  "cache_creation_input_tokens": 0,
  "cache_read_input_tokens": 0,
  "output_tokens": 2841
}
```

Both cache metrics are zero. Why: the coordinator template starts with per-request variables (`system-prompt-coordinator.txt` lines 1–2):
```
Current timestamp: {{ current_timestamp }}
Request ID: {{ request_id }}
```

These sit BEFORE the giant static block (process + classification + 5 examples). `cache_control: ephemeral` sits at the END of the whole block. Every request has a unique prefix → cached block's prefix hash differs every time → no hit. Static→variable→cache_control is backwards.

## 4. Recommendations (scoped: file, line, change, why)

### Fix 1 · Multi-route dispatch (closes H1)

**`system-prompt-coordinator.txt`**

- Delete line 23:
  ```diff
  - If a ticket could fit two categories, pick the one the customer seems most blocked by.
  + If a ticket contains issues across more than one category, call spawn_specialist
  + once per category. Wait for every specialist to return before composing the
  + response. The customer-facing response MUST address every issue raised.
  ```

- Make PROCESS step 5 plural:
  ```diff
  - 5. DELEGATE. Call spawn_specialist with the right category.
  + 5. DELEGATE. For each category the ticket touches, call spawn_specialist once.
  +    Multiple categories ⇒ multiple parallel specialist calls. Wait for all results.
  ```

- Add EXAMPLE 6 showing a multi-issue ticket dispatched to 2 specialists in parallel and a single response addressing both.

### Fix 2 · Tool catalogue surgery (closes H2)

**`coordinator-tools.json`**

- Delete the noise floor: `helper`, `process`, `validate`, `tool_3_v2`, `get_data`, `customer_data_retrieval`.

- Collapse 5 customer-lookup tools into one canonical `lookup_customer`:
  ```json
  {
    "name": "lookup_customer",
    "description": "Look up a customer record. ALWAYS call this once per ticket, right after get_ticket, to resolve the submitter to a customer_id. Accepts either a customer_id or an email. Returns: customer_id, name, tier, sso_provider, primary_contact, billing_contact, seats_used, seats_limit, mrr. This is the only customer lookup you need — do not call any other lookup tool.",
    "input_schema": { "type": "object", "properties": { "identifier": {"type": "string"} }, "required": ["identifier"] }
  }
  ```

- Rename `fetch_customer_v2_databricks` to `get_customer_metrics` with trigger language:
  ```json
  {
    "name": "get_customer_metrics",
    "description": "USE THIS WHEN: a customer reports rate limiting, usage anomalies, intermittent errors, slow performance, retry storms, unexplained 429s/500s, or any complaint that requires looking at their actual traffic. Returns request volume, per-endpoint error rate, retry counts, and current vs. plan limit headroom over the last N days (default 7). DO NOT answer rate-limit questions from plan config alone — call this first.",
    "input_schema": {
      "type": "object",
      "properties": { "customer_id": {"type": "string"}, "lookback_days": {"type": "integer", "default": 7} },
      "required": ["customer_id"]
    }
  }
  ```

**`system-prompt-coordinator.txt`** — replace lines 25–28 (TOOL SELECTION GUIDANCE) with an explicit catalog: each remaining tool, one line on the trigger, one on the return.

### Fix 3 · Verify-before-resolve + escalation path (closes H3)

**Each `system-prompt-subagent-{billing,technical,account}.txt`** — add a VERIFICATION RULE before "WHAT TO RETURN":

```
=== VERIFICATION RULE ===

Every action you report must be backed by a tool call in your trace.

- If you say you issued a refund: issue_refund must appear in your tool calls.
- If you say you reset 2FA: reset_2fa must appear.
- If you say you "advised" or "told" or "asked" the customer anything: that's an
  action you cannot take — only the coordinator can write to the customer.

If the action you need to take is outside your tools, do NOT narrate it as
done. Call escalate_to_human with reason="agent_stuck" and a one-paragraph
summary of (a) what you did verify, (b) what's still blocking, (c) what action
is needed.
```

**Each subagent's `*-tools.json`** — add `escalate_to_human` (scoped identically to coordinator's version).

**`system-prompt-coordinator.txt`** — add EXAMPLE 6 (or a new EXAMPLE 7) showing a ticket that legitimately requires human handling → coordinator calls `escalate_to_human`, not `write_response`. Without an example, the model never learns this branch exists.

### Fix 4 · Cache placement (closes H4)

**`system-prompt-coordinator.txt`** — delete lines 1–2 (the per-request variables). Pass `current_timestamp` and `request_id` either in the first user message or as inputs the model reads via `get_ticket`. Keep `cache_control: ephemeral` at the end of what is now a 100% static system block.

**Each subagent system prompt** — apply the same pattern: any per-request variables come AFTER the static block (or are pulled via tools), then `cache_control: ephemeral`.

**Expected outcome:** with ~3K tickets/day and 5-minute ephemeral TTL, the static prefix (≈23K tokens for the coordinator) caches once every ~5 minutes and is read on every subsequent request. `cache_read_input_tokens` becomes the dominant share of `usage.input_tokens`. Hit ratio approaches ≥90%. Input-token cost on the cached portion drops ~10×.

## 5. Verification plan (so these don't come back)

| Fix | Verification |
|---|---|
| 1 (multi-route) | Replay T-4471. Expect: trace shows 2× `spawn_specialist` (account + billing). Response addresses both issues. Resolution status: resolved or escalated — never silently deferred. |
| 2 (tools) | Replay Incident B (rate-limit ticket). Expect: `get_customer_metrics` appears in trace before `write_response`. |
| 3 (verify/escalate) | Add a synthetic ticket requiring out-of-scope action (e.g. refund $25K, above the $10K threshold). Expect: subagent calls `escalate_to_human` with reason="agent_stuck", NO `issue_refund`, NO narrated action. |
| 4 (cache) | Send two identical coordinator requests within 5 minutes. Expect: second's `cache_read_input_tokens` ≈ size of static system block, ≥ 90% of total input. |

Bake these into a CI fixture suite. Run on every prompt change.

---

## Stretch 03 — 60-second infographic for Priya's stakeholders

Single page, three vertical lanes, large type. No technical vocabulary.

```
┌──────────────────────────────┬──────────────────────────────┬──────────────────────────────┐
│  WHAT WE PROMISED            │  WHAT HAPPENED               │  HOW WE'RE FIXING IT         │
├──────────────────────────────┼──────────────────────────────┼──────────────────────────────┤
│   "One ticket → one          │   When a customer raised     │   The AI is now allowed —    │
│    answer that handles       │   TWO issues in one          │   and required — to call     │
│    everything they asked"    │   message, our AI quietly    │   multiple specialists for   │
│                              │   answered the first and     │   one ticket. Every issue    │
│                              │   ignored the second.        │   raised gets an answer.     │
├──────────────────────────────┼──────────────────────────────┼──────────────────────────────┤
│   "AI checks the actual      │   On a 'why am I being      │   We renamed the metrics     │
│    data before answering"    │   rate-limited' question,    │   tool from an engineering   │
│                              │   the AI guessed from the    │   codename to plain          │
│                              │   plan settings instead of   │   instructions: "use this    │
│                              │   looking at the real        │   when a customer asks       │
│                              │   traffic logs.              │   about rate limits."        │
├──────────────────────────────┼──────────────────────────────┼──────────────────────────────┤
│   "AI either resolves or     │   In one case the AI         │   Specialists now have a     │
│    hands off to a human"     │   said 'go contact your      │   button to call a human.    │
│                              │   account manager' —         │   And they have to PROVE     │
│                              │   neither resolving nor      │   any action they claim:     │
│                              │   handing off, just          │   if they say they did       │
│                              │   deflecting.                │   something, the system      │
│                              │                              │   logs the tool call.        │
└──────────────────────────────┴──────────────────────────────┴──────────────────────────────┘

  The fixes are live in <date>. We're adding automated checks so any AI that drifts
  back to these behaviors is caught before it reaches a customer.
```

## Stretch 04 — Observability dashboard

The four metrics that would have surfaced both incidents days earlier:

| Metric | Definition | Trigger |
|---|---|---|
| **Orphaned-issue rate** | % of tickets where the ticket text mentions ≥2 issue-categories (lexical / classifier) but the coordinator made only 1 `spawn_specialist` call | > 5% / day → alert |
| **Specialist-hallucinated-action rate** | % of specialist writeups reporting an action without a matching tool call earlier in the trace | > 1% / day → page on-call |
| **Metrics-tool reach rate on rate-limit tickets** | % of tickets containing "rate limit"/"429"/"throttle" where `get_customer_metrics` was called | < 95% → alert |
| **Cache hit ratio** | `cache_read_input_tokens / (cache_read + cache_creation + uncached_input)` | < 30% → cost alert; > 90% is target |

Plus two that should already exist but probably don't:

- **Escalation rate** — `escalate_to_human` calls / resolved tickets. Healthy multi-tenant support sits 5–15%. Zero is silent failure.
- **First-touch accuracy** — sampled human review of "resolved" tickets that came back within 7 days. Would have caught T-4471.

**Single-screen layout:**

```
┌─────────────────────────────────────────────────────────────────────┐
│  Today: 2,847 tickets · 91% resolved · avg cost $0.04 · cache 87%   │  ← big numbers
├──────────────────┬──────────────────┬──────────────────┬────────────┤
│ Orphaned-issue   │ Hallucinated-    │ Metrics-tool     │ Escalation │
│ rate             │ action rate      │ reach on rate-   │ rate       │
│   2.1% ↓         │   0.3% ↓         │   limit tickets  │            │
│                  │                  │   96% ↑          │   11% →    │  ← KPIs w/ trend
├──────────────────┴──────────────────┴──────────────────┴────────────┤
│  Cache hit ratio over 24h  ─────────────────────────────────────────│  ← time-series
│  Cost per ticket over 24h  ─────────────────────────────────────────│
├─────────────────────────────────────────────────────────────────────┤
│  Top 5 tickets needing human review (sampled):                      │  ← actionable list
│    T-4882  account · 2 issues raised · 1 specialist · resolved      │
│    T-4901  billing · refund claimed in writeup, no tool call        │
└─────────────────────────────────────────────────────────────────────┘
```

Every metric is a leading indicator — if any moves the wrong way, you know which fix decayed.
