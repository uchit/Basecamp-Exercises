"""RetrievalV2 — chunked dense + BM25 hybrid with RRF, multi-hop, freshness.

Pipeline:
  1. BM25 over chunks (lexical)         → ranked list A
  2. Dense cosine over chunks (semantic) → ranked list B
  3. RRF fusion: score = Σ 1/(K + rank_i)  with K=60 (standard)
  4. Freshness boost: +score for chunks whose source name contains the
     most-recent year in the KB
  5. Multi-hop: if top-1 RRF score < threshold, run HyDE-expanded query
     once and merge the second pass

The chunk-level returns enable paragraph-precision citations downstream.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from rank_bm25 import BM25Okapi
import numpy as np

from .chunker import Chunk, chunk_kb
from .embeddings import EmbeddedChunk, Embedder, cosine_topk, embed_chunks
from .kb import KNOWLEDGE_BASE

# Light tokenizer that survives dashes (FedRAMP-Moderate, SOC-2).
_TOKEN = re.compile(r"[a-z0-9][a-z0-9\-_]*")


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


_YEAR = re.compile(r"\b(20\d{2})\b")


@dataclass
class HitV2:
    """A chunk-level retrieval hit with full audit metadata."""
    chunk: Chunk
    bm25_score: float
    bm25_rank: int        # 1-indexed; -1 if not in BM25 top-K
    dense_score: float
    dense_rank: int       # 1-indexed; -1 if not in dense top-K
    rrf_score: float
    freshness_boost: float
    final_score: float
    via_hyde: bool = False

    def as_dict(self) -> dict:
        return {
            "chunk_id": self.chunk.chunk_id,
            "source": self.chunk.source,
            "cite": self.chunk.cite(),
            "content": self.chunk.content,
            "position": self.chunk.position,
            "bm25_score": round(self.bm25_score, 3),
            "bm25_rank": self.bm25_rank,
            "dense_score": round(self.dense_score, 3),
            "dense_rank": self.dense_rank,
            "rrf_score": round(self.rrf_score, 4),
            "freshness_boost": round(self.freshness_boost, 3),
            "final_score": round(self.final_score, 4),
            "via_hyde": self.via_hyde,
        }


class RetrieverV2:
    """Dense + BM25 hybrid over chunked KB. Caches everything on first
    construction so per-query latency is just one embedding + one BM25 scan."""

    def __init__(self, knowledge_base: dict | None = None,
                 *, rrf_k: int = 60,
                 # Freshness must be a tie-breaker, not a swing factor.
                 # Tuned so it never outweighs a real RRF gap.
                 freshness_weight: float = 0.003):
        self._kb = knowledge_base or KNOWLEDGE_BASE
        self._chunks: list[Chunk] = chunk_kb(self._kb)
        if not self._chunks:
            self._bm25 = None
            self._embeddings: list[EmbeddedChunk] = []
            self._max_year = 0
            return

        # BM25 over chunk content + tags from parent doc.
        corpus_tokens = []
        for c in self._chunks:
            tags = self._kb[c.doc_id].get("tags", [])
            corpus_tokens.append(_tokens(c.content + " " + " ".join(tags)))
        self._bm25 = BM25Okapi(corpus_tokens)
        self._embeddings = embed_chunks(self._chunks)
        self._embedder = Embedder()
        self._rrf_k = rrf_k
        self._freshness_weight = freshness_weight

        # Most-recent year mentioned anywhere — used as the freshness anchor.
        years = []
        for c in self._chunks:
            for m in _YEAR.findall(c.source + " " + c.content):
                years.append(int(m))
        self._max_year = max(years) if years else 0

    @property
    def chunks(self) -> list[Chunk]:
        return self._chunks

    def search(self, query: str, k: int = 5) -> list[HitV2]:
        """One-shot hybrid retrieval over chunks."""
        if not self._chunks:
            return []
        return self._search_internal(query, k=k, via_hyde=False)

    def search_with_hyde(self, query: str, hyde_text: str, k: int = 5
                          ) -> list[HitV2]:
        """Retrieval using a Claude-generated hypothetical answer (HyDE).

        BM25 still uses the original query (its statistics are tuned to it);
        dense uses the hypothetical-doc embedding to catch semantic paraphrase.
        """
        if not self._chunks:
            return []
        return self._search_internal(query, k=k, via_hyde=True, dense_text=hyde_text)

    # ──────────────────────────────────────────────────────────────────
    def _search_internal(self, query: str, k: int, via_hyde: bool,
                          dense_text: str | None = None) -> list[HitV2]:
        # BM25
        bm25_scores = self._bm25.get_scores(_tokens(query))
        bm25_ranked = sorted(range(len(bm25_scores)), key=lambda i: -bm25_scores[i])
        bm25_rank_by_idx = {idx: rank + 1 for rank, idx in enumerate(bm25_ranked[:50])}

        # Dense
        dense_query_vec = self._embedder.encode([dense_text or query])[0]
        dense_top = cosine_topk(dense_query_vec, self._embeddings, k=50)
        id_to_idx = {c.chunk_id: i for i, c in enumerate(self._chunks)}
        dense_score_by_idx: dict[int, float] = {}
        dense_rank_by_idx: dict[int, int] = {}
        for rank, (cid, score) in enumerate(dense_top, start=1):
            idx = id_to_idx[cid]
            dense_score_by_idx[idx] = score
            dense_rank_by_idx[idx] = rank

        # RRF fusion over the union of top-K from each.
        K = self._rrf_k
        rrf_by_idx: dict[int, float] = {}
        for idx, rank in bm25_rank_by_idx.items():
            rrf_by_idx[idx] = rrf_by_idx.get(idx, 0.0) + 1.0 / (K + rank)
        for idx, rank in dense_rank_by_idx.items():
            rrf_by_idx[idx] = rrf_by_idx.get(idx, 0.0) + 1.0 / (K + rank)

        # Freshness boost — small additive on chunks whose source mentions
        # the most-recent year in the KB.
        hits: list[HitV2] = []
        for idx, rrf in rrf_by_idx.items():
            chunk = self._chunks[idx]
            years_in_src = [int(m) for m in _YEAR.findall(chunk.source)]
            is_freshest = bool(years_in_src) and max(years_in_src) == self._max_year
            fresh = self._freshness_weight if is_freshest else 0.0
            final = rrf + fresh
            hits.append(HitV2(
                chunk=chunk,
                bm25_score=float(bm25_scores[idx]),
                bm25_rank=bm25_rank_by_idx.get(idx, -1),
                dense_score=float(dense_score_by_idx.get(idx, 0.0)),
                dense_rank=dense_rank_by_idx.get(idx, -1),
                rrf_score=rrf,
                freshness_boost=fresh,
                final_score=final,
                via_hyde=via_hyde,
            ))

        hits.sort(key=lambda h: -h.final_score)
        return hits[:k]


# ---------------------------------------------------------------------------
# Multi-hop helper
# ---------------------------------------------------------------------------

def multi_hop_search(retriever: RetrieverV2, query: str, hyde_text: str | None,
                      k: int = 5, threshold: float = 0.024) -> list[HitV2]:
    """If first-pass top hit has a weak RRF (< threshold), augment with a
    HyDE-expanded pass and merge.

    Threshold is calibrated to MiniLM + this KB size; tune per corpus.
    """
    first = retriever.search(query, k=k)
    if not first:
        return first
    if first[0].final_score >= threshold or not hyde_text:
        return first

    second = retriever.search_with_hyde(query, hyde_text, k=k)
    # Merge: keep highest final_score per chunk_id.
    by_id: dict[str, HitV2] = {}
    for h in first + second:
        prior = by_id.get(h.chunk.chunk_id)
        if prior is None or h.final_score > prior.final_score:
            by_id[h.chunk.chunk_id] = h
    merged = sorted(by_id.values(), key=lambda h: -h.final_score)
    return merged[:k]
