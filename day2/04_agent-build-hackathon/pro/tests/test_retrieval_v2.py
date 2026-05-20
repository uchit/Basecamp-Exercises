"""Tests for the chunker, embedder, RetrieverV2, and retrieval evaluation."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE))

from pro.chunker import Chunk, chunk_kb
from pro.kb import KNOWLEDGE_BASE
from pro.retrieval_v2 import RetrieverV2
from pro.retrieval_eval import evaluate, TESTS


class TestChunker:
    def test_chunks_every_doc(self):
        chunks = chunk_kb(KNOWLEDGE_BASE)
        # Every KB doc produces ≥1 chunk.
        doc_ids = {c.doc_id for c in chunks}
        assert doc_ids == set(KNOWLEDGE_BASE.keys())

    def test_chunk_ids_are_unique(self):
        chunks = chunk_kb(KNOWLEDGE_BASE)
        ids = [c.chunk_id for c in chunks]
        assert len(ids) == len(set(ids))

    def test_chunk_ids_follow_pattern(self):
        chunks = chunk_kb(KNOWLEDGE_BASE)
        for c in chunks:
            assert "." in c.chunk_id
            assert c.chunk_id.startswith(c.doc_id)

    def test_position_and_total_consistent(self):
        chunks = chunk_kb(KNOWLEDGE_BASE)
        by_doc: dict[str, list[Chunk]] = {}
        for c in chunks:
            by_doc.setdefault(c.doc_id, []).append(c)
        for doc_id, cs in by_doc.items():
            cs.sort(key=lambda c: c.position)
            for i, c in enumerate(cs, start=1):
                assert c.position == i
                assert c.total == len(cs)

    def test_citation_format(self):
        chunks = chunk_kb(KNOWLEDGE_BASE)
        for c in chunks:
            assert "§" in c.cite()
            assert c.source in c.cite()


class TestRetrieverV2:
    def test_construction_succeeds(self):
        r = RetrieverV2()
        assert len(r.chunks) >= len(KNOWLEDGE_BASE)
        # 7 KB docs but some have multiple paragraphs
        assert all(hasattr(c, "chunk_id") for c in r.chunks)

    def test_search_returns_hits(self):
        r = RetrieverV2()
        hits = r.search("threat detection latency", k=3)
        assert 1 <= len(hits) <= 3

    def test_hits_have_full_metadata(self):
        r = RetrieverV2()
        hits = r.search("FedRAMP certification date", k=3)
        for h in hits:
            assert h.chunk is not None
            assert h.rrf_score > 0
            assert h.final_score > 0

    def test_dense_retrieval_finds_paraphrased_query(self):
        # The KB content uses "telemetry retention". A semantic question
        # phrased as "how long do you keep logs" should still surface
        # data_residency_eu via dense embeddings.
        r = RetrieverV2()
        hits = r.search("how long do you keep our log data", k=5)
        ids = [h.chunk.chunk_id for h in hits]
        assert any("data_residency" in i for i in ids)

    def test_pricing_query_retrieves_pricing_doc(self):
        r = RetrieverV2()
        hits = r.search("cost per endpoint for 5000 seats", k=3)
        ids = [h.chunk.chunk_id for h in hits]
        assert any("pricing" in i for i in ids)

    def test_freshness_boost_applied_when_year_matches(self):
        r = RetrieverV2()
        hits = r.search("compliance certifications", k=10)
        # Compliance doc says 2025. Should get freshness boost.
        comp_hits = [h for h in hits if "compliance_certs" in h.chunk.chunk_id]
        assert comp_hits, "compliance hits should be retrieved"
        assert any(h.freshness_boost > 0 for h in comp_hits)


class TestRetrievalEval:
    def test_evaluate_returns_all_metrics(self):
        r = RetrieverV2()
        agg = evaluate(r, k=5)
        for key in ("recall_at_1", "recall_at_3", "recall_at_5", "mrr", "ndcg_at_5"):
            assert key in agg
            assert 0 <= agg[key] <= 1

    def test_per_test_records_match_input(self):
        r = RetrieverV2()
        agg = evaluate(r, k=5)
        assert len(agg["per_test"]) == len(TESTS)
        for record, test in zip(agg["per_test"], TESTS):
            assert record["id"] == test["id"]

    def test_recall_at_3_meets_minimum_quality_bar(self):
        # Acceptance gate for retrieval CI: ≥ 70% of held-out questions
        # find their expected chunk in top-3.
        r = RetrieverV2()
        agg = evaluate(r, k=5)
        assert agg["recall_at_3"] >= 0.7, \
            f"recall@3 = {agg['recall_at_3']:.2f} below 0.7 minimum bar"
