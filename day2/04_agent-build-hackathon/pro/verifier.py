"""Citation verifier.

Every numeric, currency, percent, and named-certification claim in the
answer must appear in at least one cited source. Programmatic, fast, no API.

Compared to a critic LLM, this catches the precise failure mode of
"plausible-sounding number that no source supports" with zero false negatives
on string-grounded claims.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .kb import KNOWLEDGE_BASE, by_source

# Patterns ordered by specificity.
_NUMBER_PATTERNS = [
    re.compile(r"\$[\d,]+(?:\.\d+)?(?:[mMkK])?\b"),         # $1,200 / $1.5M
    re.compile(r"\b\d+(?:\.\d+)?\s?%"),                      # 23%, 99.9% (no \b after % — % is non-word)
    re.compile(r"\b\d{1,3}(?:,\d{3})+\b"),                   # 1,200 / 47,000
    re.compile(r"\b\d+\s?(?:years?|months?|days?|hours?|minutes?|seconds?)\b", re.IGNORECASE),  # 90 days
    re.compile(r"\b(?:19|20)\d{2}\b"),                        # 2024, 2025
    re.compile(r"\b\d{2,}\b"),                                # bare integers >=10
]

# Things we should detect as named claims even though they're not numeric.
_NAMED_PATTERNS = [
    re.compile(r"\bSOC\s?2(?:\s?Type\s?I{1,2})?\b", re.IGNORECASE),
    re.compile(r"\bISO\s?27001(?::\d{4})?\b", re.IGNORECASE),
    re.compile(r"\bFedRAMP(?:\s+(?:Moderate|High|Low))?\b", re.IGNORECASE),
    re.compile(r"\bHIPAA\b"),
    re.compile(r"\bPCI\s?DSS(?:\s?v?\d(?:\.\d)?)?\b", re.IGNORECASE),
    re.compile(r"\bGDPR\b"),
    re.compile(r"\bStateRAMP\b", re.IGNORECASE),
]


def _normalize(s: str) -> str:
    return re.sub(r"\s+", "", s).lower()


@dataclass
class VerificationResult:
    fully_grounded: bool
    grounded_claims: list[str]
    ungrounded_claims: list[str]
    cited_sources_resolved: list[str]      # sources actually in the KB
    cited_sources_not_in_kb: list[str]     # citations to unknown sources

    def as_dict(self) -> dict:
        return {
            "fully_grounded": self.fully_grounded,
            "grounded_claims": self.grounded_claims,
            "ungrounded_claims": self.ungrounded_claims,
            "cited_sources_resolved": self.cited_sources_resolved,
            "cited_sources_not_in_kb": self.cited_sources_not_in_kb,
        }


def extract_claims(answer_text: str) -> list[str]:
    """Pull every numeric and named claim from the answer text. Dedupes."""
    found: list[str] = []
    seen: set[str] = set()
    for patterns in (_NUMBER_PATTERNS, _NAMED_PATTERNS):
        for pat in patterns:
            for m in pat.finditer(answer_text):
                token = m.group(0).strip()
                key = _normalize(token)
                if key and key not in seen and len(key) >= 2:
                    seen.add(key)
                    found.append(token)
    return found


def verify(answer_text: str, cited_sources: list[str]) -> VerificationResult:
    """Check every extracted claim against the joined content of cited sources."""
    cited_resolved: list[str] = []
    not_in_kb: list[str] = []
    cited_content_parts: list[str] = []
    for s in cited_sources:
        entry = by_source(s)
        if entry is None:
            not_in_kb.append(s)
        else:
            cited_resolved.append(s)
            cited_content_parts.append(entry["content"])
    cited_content_normalized = _normalize(" ".join(cited_content_parts))

    grounded: list[str] = []
    ungrounded: list[str] = []
    for claim in extract_claims(answer_text):
        if _normalize(claim) in cited_content_normalized:
            grounded.append(claim)
        else:
            # Try a couple of normalizations: strip $/% to allow loose match.
            stripped = re.sub(r"[$,%\s]", "", claim).lower()
            if stripped and stripped in cited_content_normalized:
                grounded.append(claim)
            else:
                ungrounded.append(claim)

    return VerificationResult(
        fully_grounded=(len(ungrounded) == 0 and len(not_in_kb) == 0),
        grounded_claims=grounded,
        ungrounded_claims=ungrounded,
        cited_sources_resolved=cited_resolved,
        cited_sources_not_in_kb=not_in_kb,
    )
