"""Retrieval evaluation harness.

Held-out (question → expected_chunk_id) pairs. Measures:
- recall@1, recall@3, recall@5: did the expected chunk appear in top-N
- MRR  (Mean Reciprocal Rank): how high in the ranking
- NDCG@5 (Normalized DCG): position-weighted relevance

Designed to be run in CI as a gate: "retrieval changes that drop recall@3
below baseline are blocked at merge time."
"""
from __future__ import annotations

import math
from dataclasses import dataclass


# Held-out test cases. Each question maps to the chunk_ids that SHOULD be in
# the top results. Tuned for the Helios KB. Add a case whenever a real
# question surfaces a retrieval failure.
TESTS: list[dict] = [
    {
        "id": "rt-001",
        "question": "What is your incident detection latency?",
        "expected_in_top_3": {"threat_detection.p1"},
    },
    {
        "id": "rt-002",
        "question": "Tell me about your compliance certifications, especially FedRAMP.",
        "expected_in_top_3": {"compliance_certs.p1"},
    },
    {
        "id": "rt-003",
        "question": "How much does the platform cost for 5000 endpoints?",
        "expected_in_top_3": {"pricing_model.p1"},
    },
    {
        "id": "rt-004",
        "question": "Do you have reference customers in financial services?",
        "expected_in_top_3": {"financial_services_customers.p1"},
    },
    {
        "id": "rt-005",
        "question": "How does your platform handle EU data residency?",
        "expected_in_top_3": {"data_residency_eu.p1"},
    },
    {
        "id": "rt-006",
        "question": "What encryption do you use at rest and in transit?",
        "expected_in_top_3": {"data_residency_eu.p1"},
    },
    {
        "id": "rt-007",
        "question": "How long do you retain telemetry data?",
        "expected_in_top_3": {"data_residency_eu.p1"},
    },
    {
        "id": "rt-008",
        "question": "How many financial services customers do you serve?",
        "expected_in_top_3": {"financial_services_customers.p1"},
    },
    {
        "id": "rt-009",
        "question": "Are multi-year discounts available?",
        "expected_in_top_3": {"pricing_model.p1"},
    },
    {
        "id": "rt-010",
        "question": "What happens if I'm rate-limited?",
        # Both threat detection (50K EPS) and any rate-limit doc could match;
        # this case is intentionally underspecified to test recall under
        # ambiguity. The retriever should return SOMETHING reasonable.
        "expected_in_top_3": {"threat_detection.p1", "data_residency_eu.p1"},
    },
]


@dataclass
class EvalResult:
    test_id: str
    question: str
    expected: set
    top_chunk_ids: list[str]
    rank_of_first_hit: int  # 1-indexed, or 0 if none of expected appeared in top-K
    reciprocal_rank: float
    dcg5: float
    ideal_dcg5: float
    ndcg5: float

    @property
    def recall_at(self):
        def at(k: int) -> int:
            return 1 if any(c in self.expected for c in self.top_chunk_ids[:k]) else 0
        return at


def _evaluate_one(test: dict, retrieved_chunk_ids: list[str]) -> EvalResult:
    expected = test["expected_in_top_3"]
    rank = 0
    rel: list[int] = []
    for i, cid in enumerate(retrieved_chunk_ids[:5], start=1):
        is_rel = 1 if cid in expected else 0
        rel.append(is_rel)
        if is_rel and rank == 0:
            rank = i

    rr = (1.0 / rank) if rank > 0 else 0.0

    # DCG / NDCG over the top-5
    dcg = sum(r / math.log2(i + 1) for i, r in enumerate(rel, start=1))
    # Ideal: assume up to len(expected) results, all rank 1..N
    ideal_rel = [1] * min(len(expected), 5)
    idcg = sum(r / math.log2(i + 1) for i, r in enumerate(ideal_rel, start=1)) or 1.0
    ndcg = dcg / idcg

    return EvalResult(
        test_id=test["id"],
        question=test["question"],
        expected=expected,
        top_chunk_ids=retrieved_chunk_ids[:5],
        rank_of_first_hit=rank,
        reciprocal_rank=rr,
        dcg5=dcg,
        ideal_dcg5=idcg,
        ndcg5=ndcg,
    )


def evaluate(retriever, *, k: int = 5) -> dict:
    """Run every TESTS case against the retriever. Returns aggregate metrics.

    retriever must implement .search(query, k=K) returning objects with a
    .chunk.chunk_id attribute (RetrieverV2's HitV2 satisfies this).
    """
    results: list[EvalResult] = []
    for t in TESTS:
        hits = retriever.search(t["question"], k=k)
        ids = [h.chunk.chunk_id for h in hits]
        results.append(_evaluate_one(t, ids))

    n = len(results) or 1
    aggregate = {
        "n_tests": n,
        "recall_at_1": sum(r.recall_at(1) for r in results) / n,
        "recall_at_3": sum(r.recall_at(3) for r in results) / n,
        "recall_at_5": sum(r.recall_at(5) for r in results) / n,
        "mrr": sum(r.reciprocal_rank for r in results) / n,
        "ndcg_at_5": sum(r.ndcg5 for r in results) / n,
        "per_test": [
            {
                "id": r.test_id, "question": r.question,
                "expected": sorted(r.expected),
                "top_chunk_ids": r.top_chunk_ids,
                "rank_of_first_hit": r.rank_of_first_hit,
                "reciprocal_rank": round(r.reciprocal_rank, 3),
                "ndcg5": round(r.ndcg5, 3),
            }
            for r in results
        ],
    }
    return aggregate


def format_report(agg: dict) -> str:
    lines = []
    lines.append("=" * 60)
    lines.append(f"  Retrieval evaluation · {agg['n_tests']} held-out cases")
    lines.append("=" * 60)
    lines.append(f"  recall@1: {agg['recall_at_1']*100:>5.1f}%")
    lines.append(f"  recall@3: {agg['recall_at_3']*100:>5.1f}%")
    lines.append(f"  recall@5: {agg['recall_at_5']*100:>5.1f}%")
    lines.append(f"  MRR:      {agg['mrr']:.3f}")
    lines.append(f"  NDCG@5:   {agg['ndcg_at_5']:.3f}")
    lines.append("")
    lines.append("  Per-case:")
    for r in agg["per_test"]:
        mark = "✓" if r["rank_of_first_hit"] in (1, 2, 3) else "✗"
        lines.append(f"    {mark} {r['id']}  rank={r['rank_of_first_hit']}  "
                     f"rr={r['reciprocal_rank']}  ndcg={r['ndcg5']}  "
                     f"top1={r['top_chunk_ids'][0] if r['top_chunk_ids'] else '-'}")
    return "\n".join(lines)


if __name__ == "__main__":
    from .retrieval_v2 import RetrieverV2
    r = RetrieverV2()
    print(format_report(evaluate(r)))
