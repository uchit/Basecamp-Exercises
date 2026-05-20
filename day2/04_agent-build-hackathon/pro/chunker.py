"""Chunker — splits KB documents into retrievable chunks.

Each chunk carries enough metadata to be cited at paragraph precision:
chunk_id, doc_id, source (doc title), position (1..N), content. The agent's
evidence_quotes and the verifier's grounding check both operate on these
chunks rather than whole documents.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Chunk:
    """A single retrievable chunk with full provenance."""
    chunk_id: str        # e.g. "compliance_certs.p2"
    doc_id: str          # original KB key, e.g. "compliance_certs"
    source: str          # doc title as it appears in cites
    content: str
    position: int        # 1-indexed paragraph position in the parent doc
    total: int           # total chunks from the parent doc

    def cite(self) -> str:
        """Human-readable citation for this chunk."""
        return f"{self.source} §{self.position}/{self.total}"

    def as_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id, "doc_id": self.doc_id,
            "source": self.source, "content": self.content,
            "position": self.position, "total": self.total,
            "cite": self.cite(),
        }


# Light sentence splitter for fine-grained chunking when a paragraph is huge.
_SENT_BOUND = re.compile(r"(?<=[\.\?\!])\s+(?=[A-Z\"\(\[])")


def _split_paragraphs(text: str) -> list[str]:
    """Split on blank lines first; fall back to single-sentence chunks if a
    paragraph blows past 600 chars (rough KB doc heuristic)."""
    # Normalize whitespace, split on double newline.
    text = text.strip()
    raw_paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not raw_paras:
        return []

    # If the doc has no paragraph breaks, fall back to sentence-grouping.
    if len(raw_paras) == 1 and len(raw_paras[0]) > 600:
        sents = _SENT_BOUND.split(raw_paras[0])
        # Group every 3 sentences into a chunk.
        groups: list[str] = []
        buf: list[str] = []
        for s in sents:
            buf.append(s.strip())
            if len(" ".join(buf)) > 350 or len(buf) >= 3:
                groups.append(" ".join(buf))
                buf = []
        if buf:
            groups.append(" ".join(buf))
        return groups

    return raw_paras


def chunk_kb(knowledge_base: dict[str, dict]) -> list[Chunk]:
    """Convert a KNOWLEDGE_BASE-shaped dict into chunks.

    knowledge_base is {doc_id: {"source": ..., "content": ..., "tags": [...]}}.
    """
    out: list[Chunk] = []
    for doc_id, entry in knowledge_base.items():
        paras = _split_paragraphs(entry["content"])
        for i, p in enumerate(paras, start=1):
            out.append(Chunk(
                chunk_id=f"{doc_id}.p{i}",
                doc_id=doc_id,
                source=entry["source"],
                content=p,
                position=i,
                total=len(paras),
            ))
    return out
