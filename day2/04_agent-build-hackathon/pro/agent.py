"""Multi-stage agent.

  draft  →  critique  →  (optionally) revise

Structured output via tool_choice — the model is forced to call the
submit_answer tool with a typed schema. No more "model returned plain text"
parse-failure fallback.

Output shape mirrors the baseline schema plus an evidence_quotes field that
the citation verifier consumes downstream.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .client import ProClient
from .retrieval import RetrievalHit


SUBMIT_ANSWER_TOOL = {
    "name": "submit_answer",
    "description": "Submit the final structured answer for one RFP question.",
    "input_schema": {
        "type": "object",
        "properties": {
            "answer": {
                "type": "string",
                "description": "Customer-facing answer text. 1-4 sentences. Specific, professional, concrete. Use the exact numbers/dates from the retrieved sources."
            },
            "sources": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of source names actually cited in the answer, exactly as returned by retrieval. Order: most-cited first."
            },
            "confidence": {
                "type": "string",
                "enum": ["high", "medium", "low"],
                "description": "high: every claim is in a source. medium: some inference required. low: vague/missing info; mark this when you would not personally send the answer."
            },
            "flags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Anything a human reviewer should double-check before sending. [] when clean."
            },
            "evidence_quotes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Up to 4 verbatim quotes from the retrieved sources that ground the answer. Strict copy-paste — no paraphrasing. The verifier compares these to retrieved text."
            }
        },
        "required": ["answer", "sources", "confidence", "flags", "evidence_quotes"]
    }
}


CRITIQUE_TOOL = {
    "name": "submit_critique",
    "description": "Critique a drafted RFP answer.",
    "input_schema": {
        "type": "object",
        "properties": {
            "grounded": {"type": "boolean",
                          "description": "Every numeric/date/$ claim in the answer appears in at least one cited source."},
            "cited_correctly": {"type": "boolean",
                                 "description": "Every cited source was in the retrieved set."},
            "confidence_calibrated": {"type": "boolean",
                                       "description": "High confidence implies all claims grounded; medium allows some inference; low requires hedging language."},
            "tone_professional": {"type": "boolean",
                                   "description": "No all-caps, no apologetic, no defensive language."},
            "addresses_question": {"type": "boolean",
                                    "description": "Actually answers what was asked, not adjacent."},
            "should_revise": {"type": "boolean",
                               "description": "True when any criterion failed AND the answer can be improved with a revision pass."},
            "revision_notes": {"type": "string",
                                "description": "If should_revise: one-paragraph guidance to the revise pass. Empty otherwise."}
        },
        "required": ["grounded", "cited_correctly", "confidence_calibrated",
                     "tone_professional", "addresses_question", "should_revise", "revision_notes"]
    }
}


SYSTEM_PROMPT = """You are Pro, the RFP-response agent for Helios Security.

For every question:
1. Read the question.
2. Use ONLY the retrieved sources provided. Do not introduce facts from prior knowledge.
3. Call submit_answer with a structured response that includes:
   - answer: 1-4 sentences, specific, professional, customer-facing
   - sources: the exact source names you cited
   - confidence: high / medium / low (be honest)
   - flags: anything a human should double-check
   - evidence_quotes: up to 4 verbatim copy-paste excerpts from the retrieved sources that ground your specific claims

Rules:
- Use the exact numbers, dates, $ amounts, certifications from sources. Do not round.
- If sources don't fully answer the question, mark confidence low and add a flag.
- Never narrate "I will look this up" or similar — just call submit_answer once.
- evidence_quotes is verbatim text from the source content — the verifier compares them to ground truth."""


CRITIC_SYSTEM = """You are a critique agent for Helios Security RFP responses. You receive:
- The original question
- The retrieved sources (with their source names)
- The drafted answer + cited sources + confidence + evidence quotes

Your job is to evaluate the draft against five criteria. For each, decide PASS/FAIL strictly.

- grounded: every numeric, date, dollar, and named-certification claim in the answer must appear in at least one of the cited sources. If even one number is from outside the sources, FAIL.
- cited_correctly: every source name in the draft's "sources" must be in the retrieved-sources set provided to you. FAIL if any citation references a source not actually retrieved.
- confidence_calibrated: high confidence requires all claims grounded; medium allows some hedge; low requires explicit hedging language in the answer. If the draft says "high" but you can't ground a claim, FAIL.
- tone_professional: no ALL-CAPS, no over-apologetic, no defensive phrasing.
- addresses_question: directly answers what was asked, not adjacent.

If any criterion failed AND the draft could be improved on a revise pass, set should_revise=true and put one paragraph of guidance in revision_notes.

Call submit_critique with your verdict. Do not call any other tool. Do not produce text."""


@dataclass
class Critique:
    grounded: bool
    cited_correctly: bool
    confidence_calibrated: bool
    tone_professional: bool
    addresses_question: bool
    should_revise: bool
    revision_notes: str

    def all_passed(self) -> bool:
        return all([self.grounded, self.cited_correctly,
                    self.confidence_calibrated, self.tone_professional,
                    self.addresses_question])

    def failed_criteria(self) -> list[str]:
        out = []
        for name in ("grounded", "cited_correctly", "confidence_calibrated",
                     "tone_professional", "addresses_question"):
            if not getattr(self, name):
                out.append(name)
        return out


@dataclass
class Draft:
    question_id: str
    category: str
    question_text: str
    answer: str
    sources: list[str]
    confidence: str
    flags: list[str]
    evidence_quotes: list[str]
    retrieved: list[RetrievalHit]
    critique: Critique | None = None
    revision: dict | None = None

    def as_export(self) -> dict:
        """Customer-facing JSON shape (compatible with baseline + viewer)."""
        return {
            "question_id": self.question_id,
            "category": self.category,
            "question": self.question_text,
            "answer": self.answer,
            "sources": self.sources,
            "confidence": self.confidence,
            "flags": self.flags,
            "evidence_quotes": self.evidence_quotes,
            "retrieved": [h.as_dict() for h in self.retrieved],
            "critique": (self.critique.__dict__ if self.critique else None),
            "revision": self.revision,
        }


def _format_sources_for_prompt(hits: list[RetrievalHit]) -> str:
    parts = []
    for h in hits:
        score = f"  (rerank={h.rerank_score:.0f}, bm25={h.bm25_score:.2f})" if h.rerank_score is not None else f"  (bm25={h.bm25_score:.2f})"
        parts.append(f"### Source: {h.source}{score}\n{h.content}")
    return "\n\n".join(parts)


def draft_answer(
    question: dict,
    retrieved: list[RetrievalHit],
    client: ProClient,
    model: str = "claude-sonnet-4-6",
) -> Draft:
    """Single Sonnet call with forced submit_answer tool use — guaranteed
    structured output. No parse-failure fallback path because there's no parse."""
    sources_text = _format_sources_for_prompt(retrieved)
    user_msg = (
        f"Question ID: {question['id']}\n"
        f"Category: {question.get('category', 'general')}\n"
        f"Question: {question['text']}\n\n"
        f"Retrieved sources ({len(retrieved)}):\n\n{sources_text}\n\n"
        f"Call submit_answer with your structured response."
    )

    response = client.messages_create(
        stage="draft", model=model, max_tokens=2048,
        system=SYSTEM_PROMPT,
        tools=[SUBMIT_ANSWER_TOOL],
        tool_choice={"type": "tool", "name": "submit_answer"},
        messages=[{"role": "user", "content": user_msg}],
    )

    tool_use = next((b for b in response.content if b.type == "tool_use"), None)
    if tool_use is None:
        # tool_choice forces tool_use, so this is genuinely unexpected.
        raise RuntimeError(f"draft_answer: model did not call submit_answer (stop={response.stop_reason})")

    data = tool_use.input
    return Draft(
        question_id=question["id"],
        category=question.get("category", "general"),
        question_text=question["text"],
        answer=data["answer"],
        sources=data.get("sources") or [],
        confidence=data.get("confidence", "low"),
        flags=data.get("flags") or [],
        evidence_quotes=data.get("evidence_quotes") or [],
        retrieved=retrieved,
    )


def critique_draft(
    draft: Draft,
    client: ProClient,
    model: str = "claude-sonnet-4-6",
) -> Critique:
    """One Sonnet call against the critique tool. Forced tool use, no text."""
    retrieved_text = _format_sources_for_prompt(draft.retrieved)
    user_msg = (
        f"Question: {draft.question_text}\n\n"
        f"Retrieved sources ({len(draft.retrieved)}):\n\n{retrieved_text}\n\n"
        f"--- DRAFT ANSWER ---\n"
        f"answer: {draft.answer}\n"
        f"cited sources: {draft.sources}\n"
        f"confidence: {draft.confidence}\n"
        f"flags: {draft.flags}\n"
        f"evidence quotes: {draft.evidence_quotes}\n\n"
        f"Evaluate the draft. Call submit_critique."
    )

    response = client.messages_create(
        stage="critique", model=model, max_tokens=1024,
        system=CRITIC_SYSTEM,
        tools=[CRITIQUE_TOOL],
        tool_choice={"type": "tool", "name": "submit_critique"},
        messages=[{"role": "user", "content": user_msg}],
    )

    tool_use = next((b for b in response.content if b.type == "tool_use"), None)
    if tool_use is None:
        # Fail open: assume passes.
        return Critique(grounded=True, cited_correctly=True,
                        confidence_calibrated=True, tone_professional=True,
                        addresses_question=True,
                        should_revise=False, revision_notes="")

    d = tool_use.input
    return Critique(
        grounded=bool(d.get("grounded")),
        cited_correctly=bool(d.get("cited_correctly")),
        confidence_calibrated=bool(d.get("confidence_calibrated")),
        tone_professional=bool(d.get("tone_professional")),
        addresses_question=bool(d.get("addresses_question")),
        should_revise=bool(d.get("should_revise")),
        revision_notes=d.get("revision_notes") or "",
    )


REVISE_SYSTEM = SYSTEM_PROMPT + """

REVISION PASS: a previous draft failed one or more critique criteria. The
critic's notes are below. Produce an improved submit_answer call that
addresses each note specifically. Keep what was good; fix what was flagged.
"""


def revise_draft(
    draft: Draft,
    critique: Critique,
    client: ProClient,
    model: str = "claude-sonnet-4-6",
) -> Draft:
    """One additional Sonnet draft pass, given the original retrieved set +
    the prior draft + critique notes."""
    sources_text = _format_sources_for_prompt(draft.retrieved)
    user_msg = (
        f"Question ID: {draft.question_id}\n"
        f"Category: {draft.category}\n"
        f"Question: {draft.question_text}\n\n"
        f"Retrieved sources:\n\n{sources_text}\n\n"
        f"--- PRIOR DRAFT (needs revision) ---\n"
        f"answer: {draft.answer}\n"
        f"sources: {draft.sources}\n"
        f"confidence: {draft.confidence}\n"
        f"flags: {draft.flags}\n\n"
        f"--- CRITIC NOTES ---\n"
        f"Failed criteria: {critique.failed_criteria()}\n"
        f"Guidance: {critique.revision_notes}\n\n"
        f"Call submit_answer with the improved response."
    )

    response = client.messages_create(
        stage="revise", model=model, max_tokens=2048,
        system=REVISE_SYSTEM,
        tools=[SUBMIT_ANSWER_TOOL],
        tool_choice={"type": "tool", "name": "submit_answer"},
        messages=[{"role": "user", "content": user_msg}],
    )
    tool_use = next((b for b in response.content if b.type == "tool_use"), None)
    if tool_use is None:
        return draft  # keep original

    data = tool_use.input
    revised = Draft(
        question_id=draft.question_id,
        category=draft.category,
        question_text=draft.question_text,
        answer=data["answer"],
        sources=data.get("sources") or [],
        confidence=data.get("confidence", "low"),
        flags=data.get("flags") or [],
        evidence_quotes=data.get("evidence_quotes") or [],
        retrieved=draft.retrieved,
    )
    # Attach an audit pointer to the prior draft.
    revised.revision = {
        "prior_answer": draft.answer,
        "prior_confidence": draft.confidence,
        "prior_sources": draft.sources,
        "applied_notes": critique.revision_notes,
    }
    return revised
