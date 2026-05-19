"""
Day 2 · Session 4 — Agent Engineering Challenge.

Builds a full Helios Security RFP-response pipeline on top of the Level-0
single-question agent from the notebook:

  parse  →  retrieve (search_kb)  →  draft (cited JSON)  →  review (cross-answer
  consistency)  →  export  →  evals

Design goal: generic enough to take a SURPRISE RFP (questions the agent hasn't
seen) without hardcoding to the 5 sample questions.

Usage:
    source .venv/bin/activate && source ~/.basecamp_anthropic_key
    python hackathon.py            # full pipeline + review + evals
    python hackathon.py draft      # just draft answers
    python hackathon.py surprise   # run against a surprise RFP fixture
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import sys
import time
from typing import Optional

import anthropic

MODEL = "claude-sonnet-4-6"
client = anthropic.Anthropic(timeout=300.0)


# ----------------------------------------------------------------------------
# Mock knowledge base (verbatim from the notebook — would normally be a vector
# store or BM25 index in production).
# ----------------------------------------------------------------------------

KNOWLEDGE_BASE = {
    "threat_detection": {
        "source": "Helios Platform Architecture Doc v4.2",
        "content": (
            "Helios Sentinel uses a multi-layered detection engine combining "
            "signature-based matching, behavioral analysis, and ML-driven anomaly detection. "
            "Data sources include endpoint telemetry (process events, file system changes, "
            "network connections), cloud workload logs (AWS CloudTrail, Azure Activity Log, "
            "GCP Audit Log), network flow data (NetFlow v9/IPFIX), and email gateway events. "
            "Average detection-to-alert latency is 2.3 seconds for signature matches and "
            "18 seconds for behavioral detections. Our SIEM correlation engine processes "
            "up to 50,000 events per second per tenant."
        ),
        "tags": ["technical", "detection", "latency", "architecture"],
    },
    "compliance_certs": {
        "source": "Helios Compliance & Certifications Register 2025",
        "content": (
            "Current certifications: SOC 2 Type II (audited December 2024 by Deloitte), "
            "ISO 27001:2022 (certified March 2024 by BSI), FedRAMP Moderate (authorized "
            "June 2024, sponsored by DHS), HIPAA (BAA available, last assessment October 2024), "
            "PCI DSS v4.0 Level 1 Service Provider (validated September 2024 by Coalfire). "
            "StateRAMP authorized (January 2025). All certifications maintained on continuous "
            "monitoring basis with quarterly internal audits."
        ),
        "tags": ["compliance", "certifications", "audit", "soc2", "fedramp"],
    },
    "pricing_model": {
        "source": "Helios Commercial Pricing Sheet Q1 2025",
        "content": (
            "Endpoint Protection Platform (EPP+EDR bundle): "
            "500 endpoints: $18/seat/month ($108,000/year). "
            "1,000 endpoints: $15/seat/month ($180,000/year) — 17% volume discount. "
            "5,000 endpoints: $11/seat/month ($660,000/year) — 39% volume discount. "
            "Minimum contract term: 12 months. Multi-year discounts: 2-year = additional 5%, "
            "3-year = additional 10%. SIEM add-on: +$6/seat/month. "
            "MDR add-on: +$12/seat/month. All pricing excludes professional services."
        ),
        "tags": ["pricing", "commercial", "discount", "contract"],
    },
    "financial_services_customers": {
        "source": "Helios Customer Success — Vertical Report 2024",
        "content": (
            "Helios currently serves 47 customers in financial services, including "
            "12 banks, 8 insurance carriers, 15 asset management firms, and 12 fintech companies. "
            "Reference accounts (approved for external use): "
            "1) Meridian National Bank — 3,200 endpoints, EPP+EDR+SIEM, deployed since 2022. "
            "2) Crestview Capital Partners — 850 endpoints, EPP+MDR, deployed since 2023. "
            "3) Apex Insurance Group — 5,100 endpoints, full platform, deployed since 2021. "
            "Average NPS in financial services vertical: 72."
        ),
        "tags": ["company-info", "customers", "financial-services", "references"],
    },
    "data_residency_eu": {
        "source": "Helios Data Sovereignty & Privacy Whitepaper v3.1",
        "content": (
            "Helios supports full EU data residency through dedicated infrastructure in "
            "Frankfurt (AWS eu-central-1) and Dublin (AWS eu-west-1). Customer data never "
            "leaves the selected region. Encryption at rest: AES-256-GCM with customer-managed "
            "keys (AWS KMS or BYOK). Encryption in transit: TLS 1.3 for all API and agent "
            "communications, with certificate pinning for endpoint agents. "
            "GDPR Data Processing Agreement (DPA) included in all EU contracts. "
            "Annual third-party penetration testing by NCC Group. "
            "Data retention: configurable per tenant, default 90 days for raw telemetry, "
            "13 months for aggregated alerts."
        ),
        "tags": ["technical", "compliance", "data-residency", "eu", "encryption", "gdpr"],
    },
    "past_rfp_detection_answer": {
        "source": "Acme Corp RFP Response — March 2024",
        "content": (
            "Q: Describe your real-time threat detection capabilities. "
            "A: Helios Sentinel provides sub-3-second detection for known threat patterns "
            "and under 20 seconds for behavioral anomalies. Our detection engine ingests "
            "endpoint telemetry, network flows, cloud audit logs, and email events. "
            "The SIEM correlation engine handles 50K EPS per tenant. "
            "We maintain a 99.7% true positive rate on our top 100 detection rules, "
            "validated quarterly against MITRE ATT&CK framework."
        ),
        "tags": ["technical", "detection", "past-rfp"],
    },
    "past_rfp_compliance_answer": {
        "source": "NovaTech RFP Response — July 2024",
        "content": (
            "Q: What compliance certifications do you hold? "
            "A: Helios holds SOC 2 Type II, ISO 27001, FedRAMP Moderate, PCI DSS v4.0, "
            "and HIPAA compliance. All certifications are actively maintained with "
            "continuous monitoring. We provide audit reports upon request under NDA. "
            "Our security team of 14 full-time engineers manages compliance programs."
        ),
        "tags": ["compliance", "certifications", "past-rfp"],
    },
}


def search_kb_impl(query: str, category: Optional[str] = None) -> list[dict]:
    qterms = set(query.lower().split())
    results = []
    for entry_id, entry in KNOWLEDGE_BASE.items():
        text = (entry["content"] + " " + " ".join(entry["tags"])).lower()
        overlap = len(qterms & set(text.split()))
        if category and category.lower() in [t.lower() for t in entry["tags"]]:
            overlap += 5
        if overlap > 0:
            results.append({"id": entry_id, "source": entry["source"],
                            "content": entry["content"], "relevance_score": overlap})
    results.sort(key=lambda x: x["relevance_score"], reverse=True)
    return results[:3]


SEARCH_KB_TOOL = {
    "name": "search_kb",
    "description": (
        "Search the Helios Security knowledge base for information relevant to "
        "answering an RFP question. USE THIS for every question — never answer "
        "from general knowledge. Returns up to 3 documents with source attribution."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Keyword search query"},
            "category": {
                "type": "string",
                "enum": ["technical", "compliance", "pricing", "company-info"],
                "description": "Optional category filter to narrow results.",
            },
        },
        "required": ["query"],
    },
}


def execute_tool(name: str, inputs: dict) -> str:
    if name == "search_kb":
        return json.dumps(search_kb_impl(query=inputs["query"], category=inputs.get("category")), indent=2)
    return json.dumps({"error": f"unknown tool: {name}"})


# ----------------------------------------------------------------------------
# Drafting agent — Level 0 from the notebook, generalized.
# ----------------------------------------------------------------------------

SYSTEM_PROMPT = """You are an AI assistant helping Helios Security respond to RFP questionnaires.

Workflow for every question:
1. Call search_kb at least once, with keywords from the question. If two categories may apply,
   call search_kb a SECOND time with the other category. Do not answer from general knowledge.
2. Draft a professional, specific answer grounded in retrieved sources. Use concrete numbers.
3. Cite sources by their "source" field exactly as returned by search_kb.
4. If the KB lacks the information needed, lower confidence and add an explicit flag.

Return a JSON object — NO markdown fences, no preamble, no trailing prose — matching this schema:
{
  "question_id": <string>,
  "category": <string>,                  // copy from the question if present, else infer
  "answer": <string>,                    // 1–4 sentences, specific, professional
  "sources": [<string>, ...],            // list of source names cited, in order used
  "confidence": "high" | "medium" | "low",
  "flags": [<string>, ...]               // anything a human reviewer should check; [] if clean
}

Be specific. Use exact numbers and dates from the source. Mark confidence honestly: low if any
field would be guessed; medium if some inference required; high only when every claim is in a source."""


def draft_answer(q_id: str, q_text: str, category: str, *, model: str = MODEL, max_turns: int = 5) -> dict:
    user_msg = (
        f"Answer this RFP question.\n\n"
        f"Question ID: {q_id}\n"
        f"Category: {category}\n"
        f"Question: {q_text}\n\n"
        f"Search the knowledge base first, then draft your structured answer."
    )
    messages = [{"role": "user", "content": user_msg}]

    for _ in range(max_turns):
        resp = client.messages.create(
            model=model, max_tokens=2048,
            system=SYSTEM_PROMPT, messages=messages, tools=[SEARCH_KB_TOOL],
        )
        if resp.stop_reason == "end_turn":
            for block in resp.content:
                if block.type == "text":
                    text = block.text.strip()
                    # Tolerate markdown fences just in case
                    if "```json" in text:
                        text = text.split("```json", 1)[1].split("```", 1)[0].strip()
                    elif text.startswith("```"):
                        text = text.split("```", 2)[1]
                        if text.startswith("json"):
                            text = text[4:]
                        text = text.strip()
                    try:
                        return json.loads(text)
                    except json.JSONDecodeError:
                        return {"question_id": q_id, "category": category,
                                "answer": text, "sources": [], "confidence": "low",
                                "flags": ["model output was not valid JSON"]}
            break

        if resp.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": resp.content})
            tool_results = [{
                "type": "tool_result", "tool_use_id": b.id,
                "content": execute_tool(b.name, b.input),
            } for b in resp.content if b.type == "tool_use"]
            messages.append({"role": "user", "content": tool_results})

    return {"question_id": q_id, "category": category, "answer": "",
            "sources": [], "confidence": "low", "flags": ["max turns reached"]}


def process_rfp(questions: list[dict], *, parallel: int = 5) -> list[dict]:
    """Process N questions concurrently. Order of return matches input."""
    results: list[dict | None] = [None] * len(questions)
    with concurrent.futures.ThreadPoolExecutor(max_workers=parallel) as pool:
        futs = {
            pool.submit(draft_answer, q.get("id", f"Q{i+1}"), q["text"], q.get("category", "general")): i
            for i, q in enumerate(questions)
        }
        done = 0
        for f in concurrent.futures.as_completed(futs):
            i = futs[f]
            results[i] = f.result()
            done += 1
            print(f"    [{done}/{len(questions)}] drafted q{i+1}", flush=True)
    return [r for r in results if r is not None]


# ----------------------------------------------------------------------------
# Consistency reviewer — single Claude call, all answers in one prompt.
# ----------------------------------------------------------------------------

REVIEWER_SYSTEM = """You are reviewing a batch of RFP answers for a customer-facing response.
Your only job is to find INTERNAL INCONSISTENCIES that would embarrass us:

- Numerical contradictions (different counts/dates/prices stated for the same fact).
- Certification date drift (e.g. one answer says FedRAMP "June 2024", another says "2023").
- Tonal mismatches (overly casual in one, formal in another).
- Confidence/scope mismatches (claiming a number with high confidence in one answer, then
  hedging on the same fact elsewhere).
- Source citation conflicts (different sources cited for the same claim).

Return a JSON object — no fences, no preamble — matching:
{
  "issues": [
    {
      "kind": "numerical" | "date" | "tone" | "confidence" | "source" | "other",
      "question_ids": [<string>, ...],     // 2+ ids the issue spans
      "summary": <string>,                  // one sentence
      "recommended_fix": <string>           // one sentence — which answer to amend and how
    },
    ...
  ],
  "overall_assessment": <string>            // 1–2 sentence verdict
}

Be conservative: only flag issues you are confident about. An empty issues list is fine if
the answers are consistent."""


def review_answers(answers: list[dict], *, model: str = MODEL) -> dict:
    payload = "Drafted answers (JSON):\n\n" + json.dumps(answers, indent=2)
    resp = client.messages.create(
        model=model, max_tokens=2048,
        system=REVIEWER_SYSTEM,
        messages=[{"role": "user", "content": payload}],
    )
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    if "```json" in text:
        text = text.split("```json", 1)[1].split("```", 1)[0].strip()
    elif text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"issues": [], "overall_assessment": text[:400], "_parse_error": True}


# ----------------------------------------------------------------------------
# Evals — generic assertions that work on any RFP, plus a few content-specific
# ones for the sample questionnaire.
# ----------------------------------------------------------------------------

def run_evals(answers: list[dict], *, sample_specific: bool = True) -> dict:
    results = {"passed": 0, "failed": 0, "details": []}

    def record(qid: str, name: str, passed: bool, note: str = "") -> None:
        results["details"].append({"q": qid, "assertion": name, "passed": passed, "note": note})
        results["passed" if passed else "failed"] += 1

    # Generic assertions, run on every answer
    for ans in answers:
        qid = ans.get("question_id", "?")
        record(qid, "has_answer_text", bool(ans.get("answer", "").strip()))
        record(qid, "valid_confidence",
               ans.get("confidence") in {"high", "medium", "low"},
               f"got: {ans.get('confidence')}")
        record(qid, "has_sources_or_flagged",
               len(ans.get("sources", [])) > 0 or len(ans.get("flags", [])) > 0,
               f"sources={len(ans.get('sources', []))} flags={len(ans.get('flags', []))}")
        record(qid, "flags_is_list", isinstance(ans.get("flags"), list))

    # Sample-questionnaire-specific assertions (only run when those IDs are present)
    if sample_specific:
        by_id = {a.get("question_id"): a for a in answers}
        if "Q3" in by_id:
            a = by_id["Q3"]["answer"].lower()
            record("Q3", "pricing_500_seat_cited",
                   "$18" in a or "18 /seat" in a or "$108" in a,
                   "expected $18 or $108,000")
            record("Q3", "pricing_5000_seat_cited",
                   "$11" in a or "$660" in a, "expected $11 or $660,000")
        if "Q4" in by_id:
            a = by_id["Q4"]["answer"].lower()
            record("Q4", "names_at_least_two_refs",
                   sum(name in a for name in ("meridian", "crestview", "apex")) >= 2,
                   "expected 2+ of Meridian/Crestview/Apex")
        if "Q5" in by_id:
            a = by_id["Q5"]["answer"].lower()
            record("Q5", "names_eu_region", "frankfurt" in a or "dublin" in a)
            record("Q5", "mentions_encryption", "aes" in a or "tls" in a)

    return results


# ----------------------------------------------------------------------------
# Sample RFP + surprise RFP fixtures
# ----------------------------------------------------------------------------

SAMPLE_RFP = [
    {"id": "Q1", "category": "technical",
     "text": "Describe your platform's approach to real-time threat detection. "
             "What data sources are ingested, and what is the average detection-to-alert latency?"},
    {"id": "Q2", "category": "compliance",
     "text": "List all compliance certifications your organization currently holds "
             "(SOC 2, ISO 27001, FedRAMP, etc.) and the date of most recent audit for each."},
    {"id": "Q3", "category": "pricing",
     "text": "Provide per-seat pricing for 500, 1,000, and 5,000 endpoints. "
             "Are volume discounts available? Is there a minimum contract term?"},
    {"id": "Q4", "category": "company-info",
     "text": "How many customers do you currently serve in the financial services vertical? "
             "Provide 2–3 reference accounts."},
    {"id": "Q5", "category": "technical",
     "text": "How does your platform handle data residency requirements for customers "
             "operating in the EU? Describe encryption at rest and in transit."},
]

# Surprise RFP — questions the agent hasn't seen, including:
# - a multi-category question (technical + compliance + pricing)
# - a question with NO direct KB match (intentional robustness test)
# - a question that needs synthesis across multiple KB entries
SURPRISE_RFP = [
    {"id": "S1", "category": "technical + compliance",
     "text": "Walk through a P1 incident for an EU customer end-to-end: how the detection "
             "engine flags it, what data leaves the EU region (if any), and what compliance "
             "obligations our DPA imposes on us during incident response."},
    {"id": "S2", "category": "pricing",
     "text": "I'm running 2,500 endpoints, want EPP+EDR+SIEM+MDR, and would sign a 3-year. "
             "Walk me through expected annual cost AND the effective per-seat-per-month price "
             "after all discounts."},
    {"id": "S3", "category": "company-info",
     "text": "We're a Singapore-based asset manager looking for two reference customers "
             "with similar profile. Who would you put us in touch with and why?"},
    {"id": "S4", "category": "technical",
     "text": "What is your customer-success NPS score and how is it measured?"},
    {"id": "S5", "category": "compliance",
     "text": "How long do you retain raw security telemetry, and what is the customer's "
             "ability to configure retention?"},
]


# ----------------------------------------------------------------------------
# Driver
# ----------------------------------------------------------------------------

def section(title: str) -> None:
    print()
    print("=" * 72)
    print(f"  {title}")
    print("=" * 72)


def run_pipeline(questions: list[dict], rfp_name: str, *, sample_specific: bool = True) -> dict:
    section(f"DRAFT — {rfp_name} ({len(questions)} questions)")
    t0 = time.time()
    answers = process_rfp(questions)
    print(f"  drafted in {time.time() - t0:.1f}s")

    print("\n  Per-question summary:")
    for a in answers:
        flags = a.get("flags") or []
        flags_str = f"  flags={flags}" if flags else ""
        print(f"    {a.get('question_id'):>4} "
              f"conf={a.get('confidence', '?'):<6} "
              f"sources={len(a.get('sources', []))}{flags_str}")

    section(f"REVIEW — cross-answer consistency")
    review = review_answers(answers)
    if review.get("issues"):
        for issue in review["issues"]:
            print(f"  [{issue.get('kind')}] q={issue.get('question_ids')}  {issue.get('summary')}")
            print(f"     fix: {issue.get('recommended_fix')}")
    else:
        print("  no issues flagged.")
    print(f"\n  overall: {review.get('overall_assessment')}")

    section(f"EVALS — {rfp_name}")
    ev = run_evals(answers, sample_specific=sample_specific)
    total = ev["passed"] + ev["failed"]
    pct = round(100 * ev["passed"] / total) if total else 0
    print(f"  {ev['passed']}/{total} passed ({pct}%)")
    by_qid: dict[str, list[dict]] = {}
    for d in ev["details"]:
        by_qid.setdefault(d["q"], []).append(d)
    for qid in sorted(by_qid):
        n_pass = sum(1 for d in by_qid[qid] if d["passed"])
        n_tot = len(by_qid[qid])
        print(f"    {qid}: {n_pass}/{n_tot}")
        for d in by_qid[qid]:
            sign = "+" if d["passed"] else "-"
            note = f" ({d['note']})" if not d["passed"] and d.get("note") else ""
            print(f"      {sign} {d['assertion']}{note}")

    return {
        "rfp_name": rfp_name,
        "total_questions": len(questions),
        "answers": answers,
        "review": review,
        "evals": ev,
    }


def main() -> None:
    stage = (sys.argv[1].lower() if len(sys.argv) > 1 else "all")

    if stage in ("draft", "all", "sample"):
        out = run_pipeline(SAMPLE_RFP, "Helios sample RFP", sample_specific=True)
        with open("rfp_response_sample.json", "w") as f:
            json.dump(out, f, indent=2)
        print(f"\n  wrote rfp_response_sample.json")

    if stage in ("surprise", "all"):
        out = run_pipeline(SURPRISE_RFP, "SURPRISE RFP (unseen)", sample_specific=False)
        with open("rfp_response_surprise.json", "w") as f:
            json.dump(out, f, indent=2)
        print(f"\n  wrote rfp_response_surprise.json")


if __name__ == "__main__":
    main()
