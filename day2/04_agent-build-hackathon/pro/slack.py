"""Slack / Teams webhook formatter.

Formats a Pro RunReport into a Slack-compatible Blocks payload. Posts to a
webhook URL when SLACK_WEBHOOK_URL is set; otherwise returns the payload
for inspection (so tests can verify shape without network).

Same payload shape works for Microsoft Teams via its incoming-webhook
connector — Teams renders Slack Block Kit best-effort.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass


def build_payload(report: dict, *, viewer_url: str | None = None,
                   keynote_url: str | None = None) -> dict:
    """Slack Blocks payload summarizing one Pro run."""
    comp = report.get("composite") or {}
    cost = report.get("cost") or {}
    answers = report.get("answers") or []
    review = report.get("review") or {}
    issues = review.get("issues") or []
    blockers = [i for i in issues if i.get("severity") == "blocker"]

    score = float(comp.get("score", 0))
    score_emoji = "🟢" if score >= 90 else ("🟡" if score >= 70 else "🔴")

    blocks: list[dict] = [
        {
            "type": "header",
            "text": {"type": "plain_text",
                      "text": f"{score_emoji} {report.get('rfp_name', 'RFP')} · Pro composite {score:.1f}/100"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn",
                  "text": f"*Source coverage*\n{comp.get('source_coverage', 0):.1f}%"},
                {"type": "mrkdwn",
                  "text": f"*Confidence index*\n{comp.get('confidence_index', 0):.1f}%"},
                {"type": "mrkdwn",
                  "text": f"*Grounding rate*\n{comp.get('grounding_rate', 0):.1f}%"},
                {"type": "mrkdwn",
                  "text": f"*Reviewer clean*\n{comp.get('reviewer_clean', 0):.1f}%"},
            ],
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn",
                      "text": (f"*{len(answers)}* questions · "
                                f"cost ${cost.get('total_cost', 0):.4f} · "
                                f"{cost.get('total_calls', 0)} API calls · "
                                f"reviewer issues: {len(issues)} "
                                f"({len(blockers)} blockers)")},
        },
    ]

    if blockers:
        blockers_text = "\n".join(
            f"• *{i.get('kind', 'other')}* on {i.get('question_ids')}: {i.get('summary', '')[:140]}"
            for i in blockers[:5]
        )
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn",
                      "text": f":warning: *Blockers to resolve before sending:*\n{blockers_text}"},
        })

    actions: list[dict] = []
    if viewer_url:
        actions.append({"type": "button",
                         "text": {"type": "plain_text", "text": "Open data viewer"},
                         "url": viewer_url, "style": "primary"})
    if keynote_url:
        actions.append({"type": "button",
                         "text": {"type": "plain_text", "text": "Open keynote"},
                         "url": keynote_url})
    if actions:
        blocks.append({"type": "actions", "elements": actions})

    return {"blocks": blocks}


def post(payload: dict, *, webhook_url: str | None = None) -> dict:
    """POST to a Slack/Teams webhook. Returns {'posted': bool, 'status': int}.

    No-op when no URL configured + caller didn't supply one — returns
    {'posted': False, 'reason': 'no webhook URL'} so callers can detect.
    """
    url = webhook_url or os.environ.get("SLACK_WEBHOOK_URL")
    if not url:
        return {"posted": False, "reason": "no webhook URL configured"}

    req = urllib.request.Request(
        url, method="POST",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return {"posted": True, "status": resp.status}
    except urllib.error.HTTPError as e:
        return {"posted": False, "status": e.code, "reason": str(e)}
    except Exception as e:
        return {"posted": False, "reason": str(e)}
