"""HyDE — Hypothetical Document Embeddings.

Given an RFP question, ask Claude (Haiku) to draft a *hypothetical* answer
based purely on plausible content. Embed that draft instead of the question
to retrieve real KB chunks. Closes the "question phrasing ≠ source phrasing"
gap that pure question-embedding retrieval struggles with.
"""
from __future__ import annotations

from .client import ProClient


_HYDE_SYSTEM = """You are generating a SHORT (3-5 sentence) hypothetical answer to an RFP question, in the voice of a typical B2B SaaS vendor response. Use realistic terminology — specific compliance frameworks, plausible numbers, common product features.

This text will not be sent to a customer. It will be used as a retrieval query to find relevant source documents. Use language a source document would use.

Output ONLY the hypothetical answer text. No preamble, no bullets, no headers."""


def hyde_expand(question: str, client: ProClient,
                 model: str = "claude-haiku-4-5") -> str:
    """Generates a hypothetical answer to seed dense retrieval.

    One Haiku call, ~150 output tokens. Trade-off: ~5ms + $0.0002 per
    question for typically 5-15 point retrieval-recall improvement on
    paraphrased questions.
    """
    response = client.messages_create(
        stage="hyde", model=model, max_tokens=300,
        system=_HYDE_SYSTEM,
        messages=[{"role": "user", "content": question}],
    )
    text = "".join(b.text for b in response.content if b.type == "text").strip()
    return text or question  # fall back to original on empty
