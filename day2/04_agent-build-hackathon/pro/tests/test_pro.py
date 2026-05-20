"""Unit tests for the Pro hackathon stack. No API calls — pure module-level
checks that the building blocks behave correctly.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE))

import pytest

from pro import retrieval, verifier, evals, viewer, comparator  # noqa: E402


# ---------------------------------------------------------------------------
# Retrieval (BM25)
# ---------------------------------------------------------------------------

class TestRetriever:
    def test_search_returns_hits_for_threat_detection_query(self):
        r = retrieval.Retriever()
        hits = r.search("threat detection latency", k=3)
        assert len(hits) > 0
        # Either the canonical doc or the past-RFP detection answer is
        # acceptable as the top hit — both are directly on topic.
        assert hits[0].id in ("threat_detection", "past_rfp_detection_answer")

    def test_search_finds_compliance_for_fedramp_query(self):
        r = retrieval.Retriever()
        hits = r.search("FedRAMP authorization date", k=3)
        ids = [h.id for h in hits]
        assert "compliance_certs" in ids

    def test_search_finds_pricing_for_per_seat_query(self):
        r = retrieval.Retriever()
        hits = r.search("per seat pricing 5000 endpoints", k=3)
        ids = [h.id for h in hits]
        assert "pricing_model" in ids

    def test_search_respects_k_cap(self):
        r = retrieval.Retriever()
        hits = r.search("threat detection", k=2)
        assert len(hits) <= 2

    def test_search_returns_empty_for_nonsense(self):
        r = retrieval.Retriever()
        hits = r.search("xyzzy plover quux frobozz", k=5)
        # Pure noise tokens should match nothing or near-nothing.
        assert len(hits) == 0 or all(h.bm25_score < 0.5 for h in hits)


# ---------------------------------------------------------------------------
# Citation verifier
# ---------------------------------------------------------------------------

class TestVerifier:
    def test_extracts_dollar_amounts(self):
        claims = verifier.extract_claims("Endpoint Protection is $18/seat/month.")
        assert any("$18" in c for c in claims)

    def test_extracts_percentages(self):
        claims = verifier.extract_claims("17% volume discount applies.")
        assert any("17%" in c for c in claims)

    def test_extracts_certifications(self):
        claims = verifier.extract_claims(
            "We hold SOC 2 Type II, ISO 27001, FedRAMP Moderate, and HIPAA."
        )
        c_norm = [c.lower() for c in claims]
        assert any("soc" in c for c in c_norm)
        assert any("iso 27001" in c or "iso27001" in c for c in c_norm)
        assert any("fedramp" in c for c in c_norm)
        assert any("hipaa" in c for c in c_norm)

    def test_grounded_answer_passes(self):
        # Pricing source contains "$18/seat/month" verbatim.
        result = verifier.verify(
            "Pricing is $18/seat/month at 500 endpoints.",
            cited_sources=["Helios Commercial Pricing Sheet Q1 2025"],
        )
        assert result.fully_grounded
        assert len(result.ungrounded_claims) == 0

    def test_ungrounded_claim_caught(self):
        # The pricing source does NOT contain "$99/seat/month".
        result = verifier.verify(
            "Pricing is $99/seat/month at 500 endpoints.",
            cited_sources=["Helios Commercial Pricing Sheet Q1 2025"],
        )
        assert not result.fully_grounded
        assert any("$99" in c for c in result.ungrounded_claims)

    def test_citation_to_nonexistent_source_flagged(self):
        result = verifier.verify(
            "Some claim with no numbers.",
            cited_sources=["Helios Phantom Doc That Does Not Exist"],
        )
        assert "Helios Phantom Doc That Does Not Exist" in result.cited_sources_not_in_kb


# ---------------------------------------------------------------------------
# Composite scorer
# ---------------------------------------------------------------------------

class TestComposite:
    def _mk_answer(self, **kw):
        return {
            "question_id": kw.get("qid", "Q?"),
            "answer": kw.get("answer", "..."),
            "sources": kw.get("sources", ["A"]),
            "confidence": kw.get("confidence", "high"),
            "flags": kw.get("flags", []),
            "verification": kw.get("verification", {"fully_grounded": True}),
        }

    def test_perfect_run_scores_100(self):
        answers = [self._mk_answer(qid=f"Q{i}") for i in range(5)]
        review = {"issues": [], "overall_assessment": "clean"}
        c = evals.composite(answers, review)
        assert c.score == 100.0
        assert c.source_coverage == 1.0
        assert c.confidence_index == 1.0
        assert c.grounding_rate == 1.0
        assert c.reviewer_clean == 1.0

    def test_low_confidence_drops_index(self):
        answers = [self._mk_answer(confidence="low") for _ in range(3)]
        c = evals.composite(answers, {"issues": []})
        assert c.confidence_index == pytest.approx(0.2)
        assert c.score < 100

    def test_ungrounded_drops_grounding(self):
        answers = [
            self._mk_answer(verification={"fully_grounded": False}),
            self._mk_answer(verification={"fully_grounded": True}),
        ]
        c = evals.composite(answers, {"issues": []})
        assert c.grounding_rate == 0.5

    def test_blocker_zeroes_reviewer_clean(self):
        answers = [self._mk_answer()]
        review = {"issues": [{"severity": "blocker", "kind": "numerical"}]}
        c = evals.composite(answers, review)
        assert c.reviewer_clean == 0.0
        # source 30 + conf 30 + ground 25 + reviewer 0 = 85
        assert c.score == 85.0

    def test_warnings_partial_credit(self):
        answers = [self._mk_answer()]
        review = {"issues": [{"severity": "warning", "kind": "tone"}]}
        c = evals.composite(answers, review)
        assert 0.5 <= c.reviewer_clean < 1.0


# ---------------------------------------------------------------------------
# HTML viewer / comparator (smoke tests — render without crashing)
# ---------------------------------------------------------------------------

class TestRenderers:
    def test_viewer_renders_minimal_report(self, tmp_path):
        report = {
            "rfp_name": "Test RFP",
            "total_questions": 1,
            "answers": [{
                "question_id": "Q1", "category": "technical",
                "question": "What?", "answer": "An answer.",
                "sources": ["Helios Platform Architecture Doc v4.2"],
                "confidence": "high", "flags": [], "evidence_quotes": ["sample quote"],
                "retrieved": [{"source": "Helios Platform Architecture Doc v4.2",
                                "content": "test content", "bm25_score": 1.0,
                                "rerank_score": 90, "final_score": 65}],
                "critique": {"grounded": True, "cited_correctly": True,
                              "confidence_calibrated": True, "tone_professional": True,
                              "addresses_question": True, "should_revise": False,
                              "revision_notes": ""},
                "revision": None,
                "verification": {"fully_grounded": True, "grounded_claims": [],
                                  "ungrounded_claims": [], "cited_sources_resolved": ["Helios Platform Architecture Doc v4.2"],
                                  "cited_sources_not_in_kb": []},
            }],
            "review": {"issues": [], "overall_assessment": "ok"},
            "composite": {"score": 95.0, "source_coverage": 100.0,
                           "confidence_index": 100.0, "grounding_rate": 100.0,
                           "reviewer_clean": 100.0},
            "cost": {"total_cost": 0.01, "total_calls": 3, "total_input_tokens": 100,
                      "total_output_tokens": 50, "wall_clock_s": 1.5,
                      "by_stage": {"draft": {"calls": 1, "input_tokens": 100,
                                              "output_tokens": 50, "cost": 0.01,
                                              "elapsed_ms": 500}}},
            "elapsed_s": 1.5,
        }
        out = tmp_path / "viewer.html"
        viewer.render(report, out)
        assert out.exists()
        text = out.read_text()
        assert "Test RFP" in text
        assert "95.0" in text

    def test_comparator_renders(self, tmp_path):
        report = {
            "rfp_name": "Cmp RFP", "total_questions": 1, "answers": [],
            "review": {"issues": []}, "elapsed_s": 1,
            "composite": {"score": 60, "source_coverage": 60,
                           "confidence_index": 40, "grounding_rate": 0,
                           "reviewer_clean": 100},
            "cost": {"total_cost": 0, "total_calls": 0, "wall_clock_s": 0,
                      "total_input_tokens": 0, "total_output_tokens": 0, "by_stage": {}},
        }
        report2 = dict(report)
        report2["composite"] = {"score": 95, "source_coverage": 100,
                                 "confidence_index": 90, "grounding_rate": 100,
                                 "reviewer_clean": 100}
        out = tmp_path / "ab.html"
        comparator.render(report, report2, out)
        assert out.exists()
        text = out.read_text()
        assert "Baseline" in text and "Pro" in text
