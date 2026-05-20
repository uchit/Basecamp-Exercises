"""Retrieval: BM25 + optional Claude rerank.

BM25 over (content + tags) gives semantic-aware token matching that beats
the baseline's set-intersection keyword overlap. The Claude rerank step
adds a quick LLM-judged relevance score for the top-K, which tends to surface
the right doc when keywords overlap but topic doesn't (the failure mode
of pure BM25 on RFP-style natural-language questions).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from rank_bm25 import BM25Okapi

from .kb import all_entries
from .client import ProClient

# Light tokenization: lowercase, alphanumeric runs, dashes preserved (for
# SOC-2, FedRAMP-Moderate, etc.).
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9\-_]*")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


@dataclass
class RetrievalHit:
    id: str
    source: str
    content: str
    bm25_score: float
    rerank_score: float | None = None
    final_score: float = 0.0

    def as_dict(self) -> dict:
        return {
            "id": self.id, "source": self.source, "content": self.content,
            "bm25_score": round(self.bm25_score, 4),
            "rerank_score": round(self.rerank_score, 2) if self.rerank_score is not None else None,
            "final_score": round(self.final_score, 4),
        }


class Retriever:
    """BM25 index over the KB. Builds once at construction."""

    def __init__(self):
        self._entries = all_entries()
        corpus = [
            _tokenize(e["content"] + " " + " ".join(e.get("tags", [])))
            for e in self._entries
        ]
        self._bm25 = BM25Okapi(corpus)

    def search(self, query: str, k: int = 5) -> list[RetrievalHit]:
        scores = self._bm25.get_scores(_tokenize(query))
        ranked = sorted(zip(scores, self._entries), key=lambda x: -x[0])
        out: list[RetrievalHit] = []
        for score, entry in ranked[:k]:
            if score <= 0:
                break
            out.append(RetrievalHit(
                id=entry["id"], source=entry["source"], content=entry["content"],
                bm25_score=float(score), final_score=float(score),
            ))
        return out


_RERANK_PROMPT = """You are scoring how relevant each document is to an RFP question.

Question: {question}

Documents (one per block):
{documents}

For each document, output a single line:
  <id> <score>
where score is an integer 0-100 (100 = directly answers the question, 0 = irrelevant).

No explanation, no other text. Just one "<id> <score>" line per document."""


def rerank(
    hits: list[RetrievalHit],
    question: str,
    client: ProClient,
    model: str = "claude-haiku-4-5",
) -> list[RetrievalHit]:
    """Light-weight Claude rerank: ask Haiku to score each hit 0-100 for
    relevance to the question, then re-sort. ~1 API call regardless of K.
    """
    if not hits:
        return hits

    docs_text = "\n\n".join(
        f"[{h.id}] {h.content[:600]}" + (" …" if len(h.content) > 600 else "")
        for h in hits
    )
    prompt = _RERANK_PROMPT.format(question=question, documents=docs_text)

    try:
        response = client.messages_create(
            stage="rerank", model=model, max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in response.content if b.type == "text").strip()
    except Exception:
        # Rerank is best-effort: fall back to BM25 ordering.
        for h in hits:
            h.rerank_score = None
        return hits

    # Parse "id score" lines.
    parsed: dict[str, float] = {}
    for line in text.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2:
            try:
                parsed[parts[0]] = float(parts[1])
            except ValueError:
                continue

    for h in hits:
        if h.id in parsed:
            h.rerank_score = parsed[h.id]
            # Combine: normalize BM25 to 0-100 vs rerank 0-100, weighted 30/70.
            normalized_bm25 = min(100.0, h.bm25_score * 10)
            h.final_score = 0.3 * normalized_bm25 + 0.7 * h.rerank_score
        else:
            # Doc wasn't scored — keep BM25 score but mark.
            h.rerank_score = None
            h.final_score = min(100.0, h.bm25_score * 10)

    return sorted(hits, key=lambda x: -x.final_score)
