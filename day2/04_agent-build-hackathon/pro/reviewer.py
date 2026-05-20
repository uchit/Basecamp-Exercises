"""Cross-answer consistency reviewer. Sharper than the baseline reviewer:

- Forces structured output via tool_choice
- Hands each issue a severity level (blocker / warning / info)
- Asks the reviewer to recommend the specific fix
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .client import ProClient


REVIEW_TOOL = {
    "name": "submit_review",
    "description": "Submit a cross-answer consistency review.",
    "input_schema": {
        "type": "object",
        "properties": {
            "issues": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string",
                                  "enum": ["numerical", "date", "tone", "confidence", "source", "scope", "other"]},
                        "severity": {"type": "string", "enum": ["blocker", "warning", "info"]},
                        "question_ids": {"type": "array", "items": {"type": "string"}},
                        "summary": {"type": "string"},
                        "recommended_fix": {"type": "string"}
                    },
                    "required": ["kind", "severity", "question_ids", "summary", "recommended_fix"]
                }
            },
            "overall_assessment": {"type": "string",
                                    "description": "1-2 sentence verdict on whether the batch is ready to send."}
        },
        "required": ["issues", "overall_assessment"]
    }
}


REVIEWER_SYSTEM = """You review a batch of RFP answers for INTERNAL INCONSISTENCIES that would embarrass us if sent to a customer.

Severity levels:
- blocker: must fix before sending (factual contradiction between two answers)
- warning: should fix (tone/scope mismatch, ambiguous wording)
- info: a soft note worth tracking

Issue kinds:
- numerical / date: same fact, different number/date across answers
- tone: register mismatch (overly casual in one, formal in another)
- confidence: same fact claimed high in one answer, hedged low in another
- source: different sources cited for the same claim
- scope: an answer addresses a question not actually asked, or misses part of one
- other: anything else worth flagging

Be conservative: only flag issues you are confident about. An empty issues list is acceptable when the batch is consistent. Call submit_review."""


@dataclass
class ReviewIssue:
    kind: str
    severity: str
    question_ids: list[str]
    summary: str
    recommended_fix: str


@dataclass
class Review:
    issues: list[ReviewIssue]
    overall_assessment: str

    def blockers(self) -> list[ReviewIssue]:
        return [i for i in self.issues if i.severity == "blocker"]

    def warnings(self) -> list[ReviewIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    def as_dict(self) -> dict:
        return {
            "issues": [i.__dict__ for i in self.issues],
            "overall_assessment": self.overall_assessment,
        }


def review_answers(
    answers: list[dict],
    client: ProClient,
    model: str = "claude-sonnet-4-6",
) -> Review:
    if not answers:
        return Review(issues=[], overall_assessment="No answers to review.")

    payload = "Drafted answers (JSON):\n\n" + json.dumps(
        [{"question_id": a.get("question_id"),
          "category": a.get("category"),
          "answer": a.get("answer"),
          "sources": a.get("sources"),
          "confidence": a.get("confidence"),
          "flags": a.get("flags")}
         for a in answers], indent=2)

    response = client.messages_create(
        stage="reviewer", model=model, max_tokens=2048,
        system=REVIEWER_SYSTEM,
        tools=[REVIEW_TOOL],
        tool_choice={"type": "tool", "name": "submit_review"},
        messages=[{"role": "user", "content": payload}],
    )
    tool_use = next((b for b in response.content if b.type == "tool_use"), None)
    if tool_use is None:
        return Review(issues=[], overall_assessment="Reviewer did not return a structured verdict.")

    data = tool_use.input
    issues_raw = data.get("issues") or []
    issues = [ReviewIssue(
        kind=i.get("kind", "other"),
        severity=i.get("severity", "info"),
        question_ids=i.get("question_ids") or [],
        summary=i.get("summary", ""),
        recommended_fix=i.get("recommended_fix", ""),
    ) for i in issues_raw]
    return Review(issues=issues, overall_assessment=data.get("overall_assessment", ""))
