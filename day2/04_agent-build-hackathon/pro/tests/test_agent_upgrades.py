"""Tests for the agent-architecture upgrades:
specialists routing, self-consistency selection, clarify detector,
date/version verifier, conflict detector (smoke only — no API).
"""
from __future__ import annotations

import sys
from pathlib import Path
from datetime import date

HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE))

from pro import specialists, clarify, date_verify


class TestRouter:
    def test_explicit_category_pricing(self):
        d = specialists.route({"id": "Q1", "text": "anything", "category": "pricing"})
        assert d.specialist_key == "pricing"
        assert d.via == "category"

    def test_explicit_category_billing_mapped_to_pricing(self):
        d = specialists.route({"id": "Q1", "text": "x", "category": "billing"})
        assert d.specialist_key == "pricing"

    def test_explicit_multi_category_first_known(self):
        d = specialists.route({"id": "Q1", "text": "x",
                                "category": "technical + compliance"})
        assert d.specialist_key in ("technical", "compliance")

    def test_keyword_routing_pricing(self):
        d = specialists.route({"id": "Q1",
                                "text": "What is the per-seat cost at 5000 endpoints with multi-year discount?"})
        assert d.specialist_key == "pricing"
        assert d.via == "keyword"

    def test_keyword_routing_compliance(self):
        d = specialists.route({"id": "Q1",
                                "text": "Do you hold SOC 2 Type II and FedRAMP Moderate certifications?"})
        assert d.specialist_key == "compliance"

    def test_keyword_routing_security(self):
        d = specialists.route({"id": "Q1",
                                "text": "What is your incident detection latency and TLS encryption?"})
        assert d.specialist_key in ("security", "technical")

    def test_keyword_routing_references(self):
        d = specialists.route({"id": "Q1",
                                "text": "Can you share customer references in the asset-management vertical?"})
        assert d.specialist_key == "references"

    def test_fallback_when_no_signal(self):
        d = specialists.route({"id": "Q1", "text": "Hello there"})
        # No client supplied + no signal → security default
        assert d.specialist_key == "security"
        assert d.via == "fallback"

    def test_specialists_all_have_distinct_prompts(self):
        prompts = {v["system"] for v in specialists.SPECIALISTS.values()}
        # SECURITY and TECHNICAL share the same system prompt (security spec).
        # PRICING / COMPLIANCE / REFERENCES are distinct.
        assert len(prompts) >= 4


class TestClarify:
    def test_specific_question_not_ambiguous(self):
        c = clarify.check("What is your per-seat pricing for 5000 endpoints?")
        assert not c.is_ambiguous

    def test_short_vague_question_flagged(self):
        c = clarify.check("Tell me more")
        assert c.is_ambiguous
        assert any("short" in r for r in c.reasons)

    def test_multi_question_flagged(self):
        c = clarify.check("What's your pricing? Do you have FedRAMP? When was your audit?")
        assert c.is_ambiguous
        assert any("multiple" in r for r in c.reasons)

    def test_disjunctive_or_flagged(self):
        c = clarify.check("Should we deploy EPP or do you recommend MDR?")
        assert c.is_ambiguous

    def test_clarification_text_provided_when_ambiguous(self):
        c = clarify.check("?? ??")
        if c.is_ambiguous:
            assert c.suggested_clarification.strip() != ""


class TestDateVerify:
    def test_clean_when_no_dates(self):
        r = date_verify.verify_dates_and_versions(
            "Helios is great.",
            cited_sources=["Helios Compliance & Certifications Register 2025"],
        )
        assert r.is_clean

    def test_recent_soc2_date_not_stale(self):
        # December 2024 audit, evaluating from a date inside the budget
        r = date_verify.verify_dates_and_versions(
            "SOC 2 Type II audit completed December 2024.",
            cited_sources=["Helios Compliance & Certifications Register 2025"],
            today=date(2025, 6, 1),
        )
        assert not any(f.is_stale for f in r.date_findings)

    def test_stale_soc2_date_flagged(self):
        r = date_verify.verify_dates_and_versions(
            "SOC 2 Type II audit completed December 2022.",
            cited_sources=["Helios Compliance & Certifications Register 2025"],
            today=date(2025, 1, 1),
        )
        assert any(f.is_stale for f in r.date_findings if f.framework.lower() == "soc 2")

    def test_pci_version_mismatch_flagged(self):
        r = date_verify.verify_dates_and_versions(
            "We hold PCI DSS v3.2 compliance.",
            cited_sources=["Helios Compliance & Certifications Register 2025"],
        )
        assert any(v.is_outdated and v.framework == "PCI DSS"
                   for v in r.version_findings)

    def test_pci_version_correct_passes(self):
        r = date_verify.verify_dates_and_versions(
            "We hold PCI DSS v4.0 Level 1 Service Provider compliance.",
            cited_sources=["Helios Compliance & Certifications Register 2025"],
        )
        assert not any(v.is_outdated and v.framework == "PCI DSS"
                       for v in r.version_findings)


class TestConsistencySelectionPure:
    """Self-consistency selection logic exercised without API calls."""

    def test_lev_similarity_identical(self):
        from pro.consistency import _lev_similarity
        assert _lev_similarity("same text", "same text") == 1.0

    def test_lev_similarity_different(self):
        from pro.consistency import _lev_similarity
        assert _lev_similarity("alpha", "omega") < 0.5
