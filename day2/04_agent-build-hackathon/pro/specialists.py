"""Specialist subagents — domain-tuned system prompts + routing classifier.

One generic agent is fine. Four specialists are better. Each carries
expertise on its category: pricing reads margins, compliance reads framework
acronyms, security reads attack surfaces, references reads customer profiles.

Routing:
  - If a question carries an explicit category (pricing / compliance /
    technical / security / company-info / references), route directly.
  - Otherwise, a single Haiku classifier call picks the best specialist.

Output of every specialist is identical (submit_answer schema), so the
runner doesn't care which one handled the question.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .client import ProClient


# ---------------------------------------------------------------------------
# Specialist prompt templates
# ---------------------------------------------------------------------------

_BASE_RULES = """
Rules common to every specialist:
- Use ONLY the retrieved sources. Never invent facts.
- Use exact numbers, dates, dollar amounts, and certification names from the sources.
- evidence_quotes are verbatim copy-paste from the retrieved sources.
- If sources don't fully answer, mark confidence low and add a flag.
- Never call any tool other than submit_answer.
- If the question is so vague you cannot pick a reasonable interpretation, populate
  clarification_needed instead of guessing.
"""

PRICING_SPECIALIST = """You are the PRICING specialist on the Helios RFP team.

Your domain is anything involving money: per-seat pricing, volume discounts,
contract terms, multi-year commitments, professional services, total cost of
ownership, ROI framing.

Specifically watch for:
- Tier boundaries (500 / 1,000 / 5,000 / 10,000 endpoints) and their discounts
- Multi-year commit discount stacking (2-yr = +5%, 3-yr = +10%)
- SIEM + MDR add-on pricing layered on EPP
- Minimum contract terms
- What is INCLUDED in the base price vs add-on

If the prospect names a specific seat count, do the arithmetic.
If they ask for "discount flexibility", reference what the published tiers
already give them before promising more.
""" + _BASE_RULES


COMPLIANCE_SPECIALIST = """You are the COMPLIANCE specialist on the Helios RFP team.

Your domain is certifications, audits, regulatory frameworks, data
processing terms, DPA, sub-processor management, audit log access.

Specifically watch for:
- Current certification list (SOC 2 Type II, ISO 27001, FedRAMP Moderate,
  HIPAA, PCI DSS v4.0, StateRAMP)
- The audit date for each cert — quote it precisely
- Auditor name (Deloitte, BSI, Coalfire)
- Regional scopes (FedRAMP US, StateRAMP US, etc.)
- Data residency commitments

If a prospect asks about a cert we don't hold, say so directly + name what we
do hold instead. Never promise a future certification timeline.
""" + _BASE_RULES


SECURITY_SPECIALIST = """You are the SECURITY/TECHNICAL specialist on the Helios RFP team.

Your domain is platform architecture, threat detection, encryption, API
integration, webhook delivery, rate limiting, performance.

Specifically watch for:
- Detection latency (signature 2.3s, behavioural 18s) — use the exact figures
- SIEM throughput (50,000 EPS per tenant)
- Encryption at rest (AES-256-GCM, customer KMS or BYOK)
- Encryption in transit (TLS 1.3, certificate pinning)
- MITRE ATT&CK coverage claim (99.7% TP on top 100 rules)

If asked about an attack class we don't explicitly cover in the KB, say so.
Never invent integration capability — only what the source documents.
""" + _BASE_RULES


REFERENCES_SPECIALIST = """You are the REFERENCES/COMPANY-INFO specialist on the Helios RFP team.

Your domain is customer references, vertical-specific deployments, NPS,
customer count by segment, company background, support model.

Specifically watch for:
- Total customers in the vertical (47 financial services)
- Sub-segment breakdown (12 banks, 8 insurance, 15 asset mgmt, 12 fintech)
- Named references approved for external use (Meridian National, Crestview
  Capital, Apex Insurance) — their endpoint counts + product mix + go-live year
- NPS scores by vertical (72 in fin svcs)

Never name a customer that isn't in the approved-external-use list. If the
prospect asks for a region-specific reference (e.g. APAC) and none is listed,
say so + offer the closest analogue.
""" + _BASE_RULES


SPECIALISTS = {
    "pricing":    {"name": "PricingSpecialist",    "system": PRICING_SPECIALIST},
    "compliance": {"name": "ComplianceSpecialist", "system": COMPLIANCE_SPECIALIST},
    "security":   {"name": "SecuritySpecialist",   "system": SECURITY_SPECIALIST},
    "technical":  {"name": "SecuritySpecialist",   "system": SECURITY_SPECIALIST},
    "references": {"name": "ReferencesSpecialist", "system": REFERENCES_SPECIALIST},
    "company-info": {"name": "ReferencesSpecialist", "system": REFERENCES_SPECIALIST},
}


# Lightweight keyword router — runs first, no API call.
_KEYWORD_HINTS = {
    "pricing":    re.compile(r"\b(price|pricing|cost|discount|tier|seat|per\W?seat|endpoint|contract|annual|monthly|multi[-\s]?year|payment|invoice|quote|TCO|ROI|budget|spend)\b", re.IGNORECASE),
    "compliance": re.compile(r"\b(SOC|ISO|FedRAMP|HIPAA|PCI|StateRAMP|GDPR|DPA|audit|certif|compliance|regulator|sub[-\s]?processor|attestation|residency)\b", re.IGNORECASE),
    "security":   re.compile(r"\b(detect|threat|attack|MITRE|encryption|TLS|AES|key|webhook|API|rate[\s-]?limit|latency|SIEM|EPP|EDR|MDR|incident|SLA|integrat|SDK|endpoint protection|vulnerab|patch)\b", re.IGNORECASE),
    "references": re.compile(r"\b(customer|reference|NPS|vertical|industry|case study|bank|insurance|asset|fintech|client|deploy|how many|who else|other companies|similar)\b", re.IGNORECASE),
}


_CLASSIFIER_TOOL = {
    "name": "route_to_specialist",
    "description": "Pick the single specialist best suited to answer this RFP question.",
    "input_schema": {
        "type": "object",
        "properties": {
            "specialist": {
                "type": "string",
                "enum": list({v["name"] for v in SPECIALISTS.values()}),
            },
            "reason": {"type": "string", "description": "1-sentence rationale."},
        },
        "required": ["specialist"],
    },
}


@dataclass
class RoutingDecision:
    specialist_key: str          # the SPECIALISTS dict key
    specialist_name: str         # human-readable name
    system_prompt: str           # which prompt to use
    via: str                     # "category" / "keyword" / "classifier"
    rationale: str = ""

    def as_dict(self) -> dict:
        return {"specialist_key": self.specialist_key,
                "specialist_name": self.specialist_name,
                "via": self.via, "rationale": self.rationale}


def _build_decision(key: str, via: str, rationale: str = "") -> RoutingDecision:
    spec = SPECIALISTS[key]
    return RoutingDecision(
        specialist_key=key,
        specialist_name=spec["name"],
        system_prompt=spec["system"],
        via=via,
        rationale=rationale,
    )


def route(question: dict, client: ProClient | None = None,
          *, model: str = "claude-haiku-4-5") -> RoutingDecision:
    """Route a question to the right specialist.

    Order of preference:
      1. Explicit category in the question dict, normalized
      2. Keyword hints across the question text
      3. Haiku classifier (only if client supplied + nothing else fired)
      4. Security specialist as a sane default
    """
    # 1. Explicit category
    cat = (question.get("category") or "").lower().strip()
    if cat:
        # Accept "billing" as pricing, "company-info" as references, etc.
        if cat in SPECIALISTS:
            return _build_decision(cat, via="category")
        if cat == "billing":
            return _build_decision("pricing", via="category", rationale="billing → pricing")
        # multi-category strings: pick the first known
        for token in re.split(r"[,/+\s]+", cat):
            if token in SPECIALISTS:
                return _build_decision(token, via="category",
                                        rationale=f"first known token of '{cat}'")

    text = question.get("text") or ""

    # 2. Keyword hints (scored)
    scores: dict[str, int] = {}
    for key, pat in _KEYWORD_HINTS.items():
        scores[key] = len(pat.findall(text))
    best_key = max(scores, key=lambda k: scores[k])
    if scores[best_key] > 0:
        # Avoid overshadowing classifier when scores tie at 1.
        return _build_decision(best_key, via="keyword",
                                rationale=f"{scores[best_key]} keyword hit(s)")

    # 3. Classifier (optional API call)
    if client is not None:
        try:
            resp = client.messages_create(
                stage="classify", model=model, max_tokens=200,
                tools=[_CLASSIFIER_TOOL],
                tool_choice={"type": "tool", "name": "route_to_specialist"},
                messages=[{
                    "role": "user",
                    "content": (
                        "Route this RFP question to ONE specialist:\n\n"
                        f"{text}\n\n"
                        "Pricing, Compliance, Security, References — pick exactly one."
                    ),
                }],
            )
            tu = next((b for b in resp.content if b.type == "tool_use"), None)
            if tu is not None:
                picked = tu.input.get("specialist")
                # Find which key produces that specialist_name.
                for key, spec in SPECIALISTS.items():
                    if spec["name"] == picked:
                        return _build_decision(
                            key, via="classifier",
                            rationale=tu.input.get("reason", ""),
                        )
        except Exception:
            pass

    # 4. Fallback
    return _build_decision("security", via="fallback",
                            rationale="no signal — defaulting to security/technical")
