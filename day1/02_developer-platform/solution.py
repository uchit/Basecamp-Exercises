"""
Session 2 (Developer Platform) — solution file.

Implements the four TODOs from Developer_Platform.{ipynb,py}:

  1. tools schemas + run_agent          (basic agentic loop)
  2. run_agent_structured                (tool loop + final structured JSON call)
  3. run_agent_thinking(effort)          (effort-controlled adaptive thinking)
  4. run_agent_streaming                 (streaming agentic loop)

Run with the venv active and ANTHROPIC_API_KEY set:

    source .venv/bin/activate
    source ~/.basecamp_anthropic_key
    python solution.py
"""
import json
import os
import time
import sys
from typing import Any

import anthropic


# ----------------------------------------------------------------------------
# Mock data + tools (copied verbatim from Developer_Platform.py so this file
# is self-contained and runnable; in a real project we'd import them).
# ----------------------------------------------------------------------------

TICKETS = {
    "TKT-1042": {
        "id": "TKT-1042", "customer": "Acme Corp", "priority": "high",
        "product_area": "billing",
        "description": "We were charged twice for our March invoice. Invoice #INV-2024-0342 shows $4,500 but our bank shows two identical charges on March 3rd. Need immediate refund of the duplicate charge.",
        "status": "open",
    },
    "TKT-1043": {
        "id": "TKT-1043", "customer": "DataFlow Inc", "priority": "medium",
        "product_area": "api",
        "description": "Our webhook endpoint stopped receiving events after we rotated API keys yesterday. We've verified the new key works for REST calls but webhooks are still failing. Getting 401 errors in the webhook logs.",
        "status": "open",
    },
    "TKT-1044": {
        "id": "TKT-1044", "customer": "CloudScale Ltd", "priority": "low",
        "product_area": "feature_request",
        "description": "Would love to see bulk export functionality in the dashboard. Currently we have to export reports one at a time which is painful when we need quarterly summaries across 50+ projects.",
        "status": "open",
    },
    "TKT-1045": {
        "id": "TKT-1045", "customer": "SecureNet Systems", "priority": "critical",
        "product_area": "account",
        "description": "Our admin account (admin@securenet.io) is locked out after failed MFA attempts. We have 47 team members who can't access the platform because SSO is tied to this admin account. This is blocking all work.",
        "status": "open",
    },
    "TKT-1046": {
        "id": "TKT-1046", "customer": "MedTech Solutions", "priority": "high",
        "product_area": "api",
        "description": "Our production integration started returning intermittent 500 errors around 2am last night. About 15% of API calls are failing. We haven't changed anything on our end. Errors seem random - sometimes the same request works on retry. Our team in Singapore is blocked and we need this resolved ASAP.",
        "status": "open",
    },
}

KB_ARTICLES = {
    "KB-001": {"title": "Processing Duplicate Payment Refunds",
               "content": "For duplicate charges: 1) Verify the duplicate in the billing system, 2) Issue refund through the payment processor (takes 3-5 business days), 3) Send confirmation email with refund reference number. Escalate if amount exceeds $10,000."},
    "KB-002": {"title": "Webhook Authentication After Key Rotation",
               "content": "When API keys are rotated, webhook signing secrets must also be updated. Go to Settings > Webhooks > Edit endpoint, and regenerate the signing secret. The old secret is invalidated immediately on key rotation."},
    "KB-003": {"title": "Bulk Export Feature (Roadmap)",
               "content": "Bulk export is on the Q3 roadmap. Workaround: Use the REST API's /reports/export endpoint with date range parameters."},
    "KB-004": {"title": "Admin Account Lockout Recovery",
               "content": "For locked admin accounts: 1) Verify identity through the secondary email on file, 2) Reset MFA through the admin recovery flow at /admin/recover, 3) Temporary access can be granted through support-level override (requires manager approval). Critical: If SSO is blocked, enable the bypass login at /login/direct for affected users."},
    "KB-005": {"title": "API Rate Limiting Best Practices",
               "content": "Default rate limits: 100 requests/minute for standard plans, 1000/minute for enterprise. Use exponential backoff with jitter for retries."},
    "KB-006": {"title": "Invoice Discrepancy Resolution",
               "content": "For billing discrepancies: Check the billing audit log for the account, compare with payment processor records, and verify no pending transactions. Contact finance team for adjustments over $5,000."},
    "KB-007": {"title": "Intermittent 500 Errors Troubleshooting",
               "content": "For intermittent server errors: 1) Check the status page for known outages, 2) Review rate limit headers - 429s can masquerade as 500s behind load balancers, 3) Check if errors correlate with payload size or specific endpoints, 4) Enable request ID logging and contact support with specific request IDs for investigation. If >10% error rate persists for >1 hour, escalate to engineering."},
}


def get_ticket(ticket_id: str) -> str:
    ticket = TICKETS.get(ticket_id)
    return json.dumps(ticket if ticket else {"error": f"Ticket {ticket_id} not found"})


def search_kb(query: str) -> str:
    query_lower = query.lower()
    results = []
    for article_id, article in KB_ARTICLES.items():
        if any(word in article["title"].lower() or word in article["content"].lower()
               for word in query_lower.split() if len(word) > 2):
            results.append({"id": article_id, **article})
    if not results:
        results = [{"id": "KB-000", "title": "No matches found",
                    "content": "No relevant articles found. Consider escalating to Tier 2 support."}]
    return json.dumps(results[:3])


def resolve_ticket(ticket_id: str, resolution: str, status: str = "resolved") -> str:
    ticket = TICKETS.get(ticket_id)
    if not ticket:
        return json.dumps({"error": f"Ticket {ticket_id} not found"})
    ticket["status"] = status
    ticket["resolution"] = resolution
    return json.dumps({"success": True, "ticket_id": ticket_id, "new_status": status})


TOOL_FUNCTIONS = {
    "get_ticket": get_ticket,
    "search_kb": search_kb,
    "resolve_ticket": resolve_ticket,
}


def execute_tool(name: str, input_data: dict) -> str:
    func = TOOL_FUNCTIONS.get(name)
    if not func:
        return json.dumps({"error": f"Unknown tool: {name}"})
    return func(**input_data)


# ----------------------------------------------------------------------------
# TODO #1 — tool schemas (with trigger language so the model knows WHEN to call)
# ----------------------------------------------------------------------------

tools = [
    {
        "name": "get_ticket",
        "description": (
            "Retrieve a support ticket by ID. ALWAYS call this first to read the "
            "customer's description, priority, and product area before doing any "
            "diagnosis or KB search."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticket_id": {"type": "string", "description": "Ticket ID like TKT-1042"},
            },
            "required": ["ticket_id"],
        },
    },
    {
        "name": "search_kb",
        "description": (
            "Search the internal knowledge base for articles. Call after reading "
            "the ticket to find resolution procedures, escalation criteria, or "
            "known issues that match the customer's problem. Free-text query."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "Free-text query, e.g. 'duplicate charge refund' or 'webhook 401 after key rotation'"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "resolve_ticket",
        "description": (
            "Close the ticket with a final customer-facing resolution writeup. "
            "Call EXACTLY ONCE at the end, after you have read the ticket and "
            "consulted the KB. Use status='escalated' when the resolution requires "
            "an action you can't take (financial >$10k, security, eng intervention)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticket_id": {"type": "string"},
                "resolution": {"type": "string",
                               "description": "Customer-facing resolution text. Lead with the action taken or next step; cite specific KB articles, amounts, IDs."},
                "status": {"type": "string",
                           "enum": ["resolved", "escalated", "pending_customer"]},
            },
            "required": ["ticket_id", "resolution", "status"],
        },
    },
]


SYSTEM_PROMPT = """You are a Tier 1 support agent for TechFlow, a B2B SaaS platform that provides project management and team collaboration tools to mid-market companies.

## Your Role
You handle incoming support tickets by investigating issues, finding solutions in the knowledge base, and resolving tickets with clear, actionable guidance.

## Process
1. ALWAYS look up the ticket first to understand the full context
2. Search the knowledge base for relevant solutions and procedures
3. Resolve the ticket with a detailed resolution that includes specific next steps

## Guidelines
- Be thorough: always search the KB before resolving, even if the issue seems straightforward
- Be specific: include exact steps, links, and timeframes in resolutions
- Escalate when needed: if confidence is low or the issue requires privileged access, mark for escalation
- Categorize accurately: billing, technical, account, or feature_request

## Escalation Criteria
- Financial issues over $10,000
- Security-related account compromises
- Issues requiring engineering intervention
- Customers with Enterprise SLA (response within 1 hour)

## Tone
Professional, empathetic, and solution-oriented. Acknowledge the customer frustration before jumping to the solution. Use the customer name when available.
"""


RESOLUTION_SCHEMA = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {
            "diagnosis": {"type": "string", "description": "Root cause analysis of the issue"},
            "solution_steps": {"type": "array", "items": {"type": "string"},
                                "description": "Ordered steps to resolve"},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "escalation_needed": {"type": "boolean"},
            "category": {"type": "string",
                          "enum": ["billing", "technical", "account", "feature_request"]},
        },
        "required": ["diagnosis", "solution_steps", "confidence", "escalation_needed", "category"],
        "additionalProperties": False,
    },
}


def get_structured_result(response) -> dict | None:
    text_blocks = [b for b in response.content if b.type == "text" and b.text.strip()]
    if not text_blocks:
        return None
    return json.loads(text_blocks[-1].text)


# ----------------------------------------------------------------------------
# Client + helpers
# ----------------------------------------------------------------------------

MODEL = "claude-sonnet-4-6"
client = anthropic.Anthropic(timeout=600.0)


def _execute_tool_uses(content_blocks) -> list[dict]:
    """Run every tool_use block in an assistant turn and shape tool_result entries."""
    results = []
    for block in content_blocks:
        if block.type == "tool_use":
            output = execute_tool(block.name, block.input)
            results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": output,
            })
    return results


# ----------------------------------------------------------------------------
# TODO #1 — basic agentic loop
# ----------------------------------------------------------------------------

def run_agent(user_message: str):
    """Run the support ticket agent: while tool_use -> exec -> re-call."""
    messages = [{"role": "user", "content": user_message}]
    while True:
        response = client.messages.create(
            model=MODEL,
            max_tokens=32000,
            system=SYSTEM_PROMPT,
            tools=tools,
            thinking={"type": "adaptive"},
            messages=messages,
        )
        if response.stop_reason != "tool_use":
            return response
        # Pass ALL content blocks back (including thinking blocks)
        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": _execute_tool_uses(response.content)})


# ----------------------------------------------------------------------------
# TODO #2 — tool loop, then a separate final call with structured output.
# ----------------------------------------------------------------------------

def run_agent_structured(user_message: str) -> dict | None:
    """Run the tool loop, then a final structured-output call (tools off)."""
    messages = [{"role": "user", "content": user_message}]
    last_response = None
    while True:
        last_response = client.messages.create(
            model=MODEL,
            max_tokens=32000,
            system=SYSTEM_PROMPT,
            tools=tools,
            thinking={"type": "adaptive"},
            messages=messages,
        )
        if last_response.stop_reason != "tool_use":
            break
        messages.append({"role": "assistant", "content": last_response.content})
        messages.append({"role": "user", "content": _execute_tool_uses(last_response.content)})

    # Final structured call: format constrains all text, so tools MUST be off.
    messages.append({"role": "assistant", "content": last_response.content})
    messages.append({"role": "user",
                     "content": "Provide your structured resolution as JSON."})
    final = client.messages.create(
        model=MODEL,
        max_tokens=8192,
        system=SYSTEM_PROMPT,
        thinking={"type": "adaptive"},
        output_config={"format": RESOLUTION_SCHEMA},
        tool_choice={"type": "none"},
        messages=messages,
    )
    return get_structured_result(final)


# ----------------------------------------------------------------------------
# TODO #3 — effort-controlled adaptive thinking. Same shape, plus effort knob.
# ----------------------------------------------------------------------------

def run_agent_thinking(user_message: str, effort: str = "high") -> dict | None:
    """Tool loop + final structured call, both with output_config.effort set."""
    messages = [{"role": "user", "content": user_message}]
    last_response = None
    while True:
        last_response = client.messages.create(
            model=MODEL,
            max_tokens=32000,
            system=SYSTEM_PROMPT,
            tools=tools,
            thinking={"type": "adaptive"},
            output_config={"effort": effort},
            messages=messages,
        )
        if last_response.stop_reason != "tool_use":
            break
        messages.append({"role": "assistant", "content": last_response.content})
        messages.append({"role": "user", "content": _execute_tool_uses(last_response.content)})

    messages.append({"role": "assistant", "content": last_response.content})
    messages.append({"role": "user",
                     "content": "Provide your structured resolution as JSON."})
    final = client.messages.create(
        model=MODEL,
        max_tokens=8192,
        system=SYSTEM_PROMPT,
        thinking={"type": "adaptive"},
        output_config={"effort": effort, "format": RESOLUTION_SCHEMA},
        tool_choice={"type": "none"},
        messages=messages,
    )
    return get_structured_result(final)


# ----------------------------------------------------------------------------
# TODO #4 — streaming agentic loop. Surfaces thinking + tool_use as they happen.
# ----------------------------------------------------------------------------

def _consume_stream(stream, narrate=True) -> Any:
    """Drain a stream while printing thinking/tool_use signposts."""
    current_block_kind = None
    current_tool_name = None
    for event in stream:
        et = event.type
        if et == "content_block_start":
            block = event.content_block
            current_block_kind = block.type
            if narrate and block.type == "thinking":
                print("\n  [thinking] ", end="", flush=True)
            elif narrate and block.type == "tool_use":
                current_tool_name = block.name
                print(f"\n  [tool_use:{block.name}] ", end="", flush=True)
            elif narrate and block.type == "text":
                print("\n  [text] ", end="", flush=True)
        elif et == "content_block_delta":
            delta = event.delta
            dt = delta.type
            if narrate and dt == "thinking_delta":
                print(delta.thinking, end="", flush=True)
            elif narrate and dt == "text_delta":
                print(delta.text, end="", flush=True)
            # input_json_delta we deliberately skip — it's the streamed tool input
        elif et == "content_block_stop":
            current_block_kind = None
            current_tool_name = None
    return stream.get_final_message()


def run_agent_streaming(user_message: str, effort: str = "high") -> dict | None:
    """Streamed tool loop, then a streamed structured-output call."""
    messages = [{"role": "user", "content": user_message}]
    last_response = None
    while True:
        with client.messages.stream(
            model=MODEL,
            max_tokens=32000,
            system=SYSTEM_PROMPT,
            tools=tools,
            thinking={"type": "adaptive"},
            output_config={"effort": effort},
            messages=messages,
        ) as stream:
            last_response = _consume_stream(stream, narrate=True)
        if last_response.stop_reason != "tool_use":
            break
        messages.append({"role": "assistant", "content": last_response.content})
        messages.append({"role": "user", "content": _execute_tool_uses(last_response.content)})

    messages.append({"role": "assistant", "content": last_response.content})
    messages.append({"role": "user",
                     "content": "Provide your structured resolution as JSON."})
    with client.messages.stream(
        model=MODEL,
        max_tokens=8192,
        system=SYSTEM_PROMPT,
        thinking={"type": "adaptive"},
        output_config={"effort": effort, "format": RESOLUTION_SCHEMA},
        tool_choice={"type": "none"},
        messages=messages,
    ) as stream:
        final = _consume_stream(stream, narrate=False)
    return get_structured_result(final)


# ----------------------------------------------------------------------------
# Driver
# ----------------------------------------------------------------------------

def _hr(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def _final_text(response) -> str:
    return "".join(b.text for b in response.content if b.type == "text").strip()


def main(stage: str = "all") -> None:
    if stage in ("1", "all"):
        _hr("TODO #1 — run_agent on TKT-1042 (billing duplicate charge)")
        t0 = time.time()
        result = run_agent("Resolve ticket TKT-1042")
        elapsed = time.time() - t0
        print(_final_text(result))
        print(f"\n[ stop_reason={result.stop_reason}  "
              f"in={result.usage.input_tokens}  out={result.usage.output_tokens}  "
              f"{elapsed:.1f}s ]")

    if stage in ("2", "all"):
        _hr("TODO #2 — run_agent_structured on TKT-1043 (webhook auth)")
        t0 = time.time()
        result = run_agent_structured("Resolve ticket TKT-1043")
        elapsed = time.time() - t0
        print(json.dumps(result, indent=2))
        print(f"\n[ {elapsed:.1f}s ]")

    if stage in ("3", "all"):
        _hr("TODO #3 — run_agent_thinking on TKT-1046 (ambiguous 500s): high vs low effort")
        for effort in ("high", "low"):
            t0 = time.time()
            result = run_agent_thinking("Resolve ticket TKT-1046", effort=effort)
            elapsed = time.time() - t0
            print(f"\n--- effort={effort} ({elapsed:.1f}s) ---")
            print(json.dumps(result, indent=2))

    if stage in ("4", "all"):
        _hr("TODO #4 — run_agent_streaming on TKT-1045 (account lockout)")
        t0 = time.time()
        result = run_agent_streaming("Resolve ticket TKT-1045")
        elapsed = time.time() - t0
        print(f"\n\n--- final structured ({elapsed:.1f}s) ---")
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    stage = sys.argv[1] if len(sys.argv) > 1 else "all"
    main(stage)
