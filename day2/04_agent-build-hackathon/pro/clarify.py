"""Disambiguation detector.

For genuinely ambiguous RFP questions, the right move is NOT to guess — it's
to surface a one-sentence clarifying question back to the requester. This
module owns the detection + the clarification text.

Triggers (any one):
  - Question text < 8 words AND no specific keyword (probably "tell me about X")
  - Question contains conjunctive ambiguity ("X or Y", "either ... or")
  - Multiple unrelated sub-questions in one
  - References to "your platform" without specifying SKU when product line is multi-SKU

When triggered, the agent returns clarification_needed in submit_answer. The
runner short-circuits the verifier and exposes the clarification in the
report instead of an answer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


_AMBIGUOUS_CONJUNCTIONS = re.compile(
    r"\b(either\s+\w+\s+or|or\s+(?:do\s+you|do you|should|would|could))\b",
    re.IGNORECASE,
)
_MULTI_QUESTION = re.compile(r"\?[^?]+\?")  # two question marks ≈ multi-question
_SKU_VAGUE = re.compile(r"\byour (?:platform|product|tool|solution)\b", re.IGNORECASE)
_SPECIFIC_KEYWORDS = re.compile(
    r"\b(pricing|cost|tier|seat|FedRAMP|SOC|ISO|HIPAA|PCI|GDPR|StateRAMP|encryption|"
    r"endpoint|webhook|API|rate|MITRE|detect|incident|customer|reference|NPS|EPP|EDR|MDR|SIEM)\b",
    re.IGNORECASE,
)


@dataclass
class ClarificationCheck:
    is_ambiguous: bool
    reasons: list[str]
    suggested_clarification: str = ""

    def as_dict(self) -> dict:
        return {
            "is_ambiguous": self.is_ambiguous,
            "reasons": self.reasons,
            "suggested_clarification": self.suggested_clarification,
        }


def check(question_text: str) -> ClarificationCheck:
    """Pure-Python rule-based ambiguity detection. No API call.

    Conservative on purpose: a false positive (asking for clarification when
    we could've answered) costs less than a false negative (guessing wrong).
    """
    reasons: list[str] = []
    text = (question_text or "").strip()

    word_count = len(text.split())
    has_specific_keyword = bool(_SPECIFIC_KEYWORDS.search(text))

    if word_count < 8 and not has_specific_keyword:
        reasons.append(f"very short ({word_count} words) and no specific keyword")
    if _AMBIGUOUS_CONJUNCTIONS.search(text):
        reasons.append("contains disjunctive ambiguity ('either/or')")
    if _MULTI_QUESTION.search(text):
        reasons.append("multiple distinct questions in one")
    if _SKU_VAGUE.search(text) and not has_specific_keyword:
        reasons.append("references the product generically without specifying SKU")

    is_ambig = bool(reasons)
    suggested = ""
    if is_ambig:
        # Build a short, polite clarifying question targeted at the matched pattern.
        if "multiple distinct questions" in reasons:
            suggested = ("Could you split the request into separate questions so "
                          "we can address each precisely?")
        elif "disjunctive ambiguity" in reasons[0] if reasons else "":
            suggested = ("Could you clarify which option you want us to address?")
        elif "very short" in reasons[0]:
            suggested = ("Could you share a bit more context — which area "
                          "(pricing, security, compliance, or references) are you most "
                          "interested in?")
        elif "generically" in reasons[0]:
            suggested = ("Which Helios product line would you like covered — "
                          "EPP, EPP+EDR, SIEM, or MDR?")
        else:
            suggested = "Could you provide a bit more detail so we answer the right thing?"

    return ClarificationCheck(
        is_ambiguous=is_ambig,
        reasons=reasons,
        suggested_clarification=suggested,
    )
