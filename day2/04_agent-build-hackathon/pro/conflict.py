"""Cross-document conflict detector.

When two cited sources disagree on the same fact (e.g. compliance register
says "FedRAMP authorized June 2024" but past-RFP-response says "FedRAMP
authorized 2023"), surface it as a high-severity finding — the agent shouldn't
silently pick a side.

Implementation: a single Sonnet call over the joined content of every UNIQUE
source cited across the batch. Forced structured output via tool_choice.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from .client import ProClient
from .kb import by_source


_CONFLICT_TOOL = {
    "name": "submit_conflicts",
    "description": "Surface any factual contradictions between cited sources.",
    "input_schema": {
        "type": "object",
        "properties": {
            "conflicts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string",
                                  "enum": ["date", "numeric", "scope", "named-entity", "version", "other"]},
                        "sources": {"type": "array", "items": {"type": "string"},
                                     "description": "2+ source names that disagree"},
                        "claim_a": {"type": "string",
                                     "description": "the specific claim in source A, quoted verbatim if possible"},
                        "claim_b": {"type": "string",
                                     "description": "the specific claim in source B, quoted verbatim if possible"},
                        "resolution_hint": {"type": "string",
                                             "description": "one sentence — which source is likely canonical and why (e.g. newer dated, official register vs past response)"},
                    },
                    "required": ["kind", "sources", "claim_a", "claim_b", "resolution_hint"],
                },
            },
        },
        "required": ["conflicts"],
    },
}


_SYSTEM = """You are auditing a set of cited KB sources for FACTUAL CONTRADICTIONS that would embarrass us if cited in the same RFP response.

Look for:
- DATE conflicts: same event/audit/cert with different dates across sources
- NUMERIC conflicts: same metric stated as different values
- SCOPE conflicts: one source says X covers Y, another says X excludes Y
- NAMED-ENTITY conflicts: one source names a different person/vendor for the same role
- VERSION conflicts: same product/standard at different versions

Rules:
- Be CONSERVATIVE: only flag actual contradictions. Same fact stated with different precision (e.g. "Q1 2025" vs "March 2025") is NOT a conflict.
- Always quote the conflicting claims, ideally verbatim.
- The resolution_hint should be one sentence: "X is likely canonical because it's the official register; Y is the past-RFP response that may be stale."
- An empty conflicts list is the correct answer when sources agree.

Call submit_conflicts. Do not output text."""


@dataclass
class Conflict:
    kind: str
    sources: list[str]
    claim_a: str
    claim_b: str
    resolution_hint: str


def detect_conflicts(cited_source_names: set[str], client: ProClient,
                      *, model: str = "claude-sonnet-4-6") -> list[Conflict]:
    """Resolve source names to KB content, ask Claude to find contradictions.

    Returns [] when there are < 2 sources to compare or when Claude finds none.
    """
    resolved = []
    for name in cited_source_names:
        entry = by_source(name)
        if entry is not None:
            resolved.append({"source": name, "content": entry["content"]})

    if len(resolved) < 2:
        return []

    payload = "Cited sources (compare these for contradictions):\n\n" + "\n\n".join(
        f"### {r['source']}\n{r['content']}" for r in resolved
    )

    try:
        resp = client.messages_create(
            stage="conflict_detect", model=model, max_tokens=2048,
            system=_SYSTEM,
            tools=[_CONFLICT_TOOL],
            tool_choice={"type": "tool", "name": "submit_conflicts"},
            messages=[{"role": "user", "content": payload}],
        )
    except Exception:
        return []

    tu = next((b for b in resp.content if b.type == "tool_use"), None)
    if tu is None:
        return []

    raw = tu.input.get("conflicts") or []
    return [Conflict(
        kind=c.get("kind", "other"),
        sources=c.get("sources") or [],
        claim_a=c.get("claim_a", ""),
        claim_b=c.get("claim_b", ""),
        resolution_hint=c.get("resolution_hint", ""),
    ) for c in raw]
