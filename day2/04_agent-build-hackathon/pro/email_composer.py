"""Cover-email + executive-summary composer.

One Sonnet call per RFP. Forced tool_choice so the output is structured:
  - subject (≤80 chars)
  - greeting (Dear …)
  - body_html (3 paragraphs: thanks + summary + next steps)
  - executive_summary (5 bullets, 1 line each — what was answered, what was
    flagged, what's outstanding)
  - signoff

The output is what the SE pastes into Outlook / Gmail before sending the
DOCX/PDF deliverable to the prospect.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from .client import ProClient


_EMAIL_TOOL = {
    "name": "submit_email",
    "description": "Compose the cover email + executive summary for an RFP response.",
    "input_schema": {
        "type": "object",
        "properties": {
            "subject": {"type": "string", "description": "Email subject (≤80 chars)."},
            "greeting": {"type": "string", "description": "e.g. 'Dear Sarah,'"},
            "body_html": {"type": "string", "description": "3-paragraph HTML body. Plain HTML only (<p>, <ul>, <li>, <strong>). No inline styles."},
            "executive_summary": {
                "type": "array",
                "items": {"type": "string"},
                "description": "5 one-line bullets: scope answered + highlights + flags + outstanding items + next step.",
            },
            "signoff": {"type": "string", "description": "e.g. 'Best regards,\\nThe Helios Security RFP team'"},
        },
        "required": ["subject", "greeting", "body_html", "executive_summary", "signoff"],
    },
}


_SYSTEM = """You compose RFP cover emails for the Helios Security Solutions team.

Tone: warm + concise + professional. Never gushing. Never apologetic. The
prospect already knows we're sending a response; the email frames it.

Body structure:
  paragraph 1 — short thanks + acknowledgement of the RFP context
  paragraph 2 — 2 sentence highlight of what the response covers
  paragraph 3 — what they should look at first + how to ask follow-ups

Executive summary: 5 bullets, each one short sentence:
  - what scope was answered (e.g. '5 questions across pricing, compliance,
    technical')
  - highest-confidence highlight (e.g. 'FedRAMP Moderate authorized June 2024')
  - what's flagged for human review (e.g. '1 answer needs SLA verification')
  - what's outstanding / clarification needed
  - next step (e.g. 'reply with go/no-go on a follow-up demo')

Call submit_email."""


@dataclass
class Email:
    subject: str
    greeting: str
    body_html: str
    executive_summary: list[str]
    signoff: str

    def as_dict(self) -> dict:
        return {
            "subject": self.subject,
            "greeting": self.greeting,
            "body_html": self.body_html,
            "executive_summary": self.executive_summary,
            "signoff": self.signoff,
        }

    def as_text(self) -> str:
        """Plain-text rendering for terminal preview."""
        import re
        body_text = re.sub(r"<[^>]+>", "", self.body_html).strip()
        bullets = "\n".join(f"  • {b}" for b in self.executive_summary)
        return (
            f"Subject: {self.subject}\n\n"
            f"{self.greeting}\n\n"
            f"{body_text}\n\n"
            f"Executive summary:\n{bullets}\n\n"
            f"{self.signoff}"
        )


def compose_email(report: dict, client: ProClient,
                   *, customer_name: str = "the team",
                   prospect_company: str | None = None,
                   model: str = "claude-sonnet-4-6") -> Email:
    answers = report.get("answers", []) or []
    summary_input = {
        "rfp_name": report.get("rfp_name"),
        "customer_name": customer_name,
        "prospect_company": prospect_company,
        "n_questions": len(answers),
        "categories": sorted({a.get("category") for a in answers if a.get("category")}),
        "high_conf_count": sum(1 for a in answers if a.get("confidence") == "high"),
        "low_conf_count": sum(1 for a in answers if a.get("confidence") == "low"),
        "n_flags": sum(len(a.get("flags") or []) for a in answers),
        "composite_score": (report.get("composite") or {}).get("score"),
    }

    user = (
        f"Compose the cover email for this RFP response:\n\n"
        f"{json.dumps(summary_input, indent=2)}"
    )

    response = client.messages_create(
        stage="email", model=model, max_tokens=1024,
        system=_SYSTEM,
        tools=[_EMAIL_TOOL],
        tool_choice={"type": "tool", "name": "submit_email"},
        messages=[{"role": "user", "content": user}],
    )
    tu = next((b for b in response.content if b.type == "tool_use"), None)
    if tu is None:
        raise RuntimeError("email composer: model did not call submit_email")
    d = tu.input
    return Email(
        subject=d["subject"], greeting=d["greeting"],
        body_html=d["body_html"], executive_summary=d["executive_summary"],
        signoff=d["signoff"],
    )
