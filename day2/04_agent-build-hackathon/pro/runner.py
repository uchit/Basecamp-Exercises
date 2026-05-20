"""End-to-end orchestrator.

  for each question (in parallel):
    retrieve(BM25, k=5) → rerank(Haiku) → draft(Sonnet, structured) →
    critique(Sonnet, structured) → if should_revise: revise(Sonnet) →
    verify(programmatic)
  cross-answer review(Sonnet) → composite score → write JSON
"""
from __future__ import annotations

import concurrent.futures
import json
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from .agent import Critique, Draft, draft_answer, critique_draft, revise_draft
from .client import CostLedger, ProClient
from .evals import composite as composite_score
from .retrieval import Retriever, rerank
from .reviewer import review_answers
from .verifier import verify

DRAFT_MODEL = "claude-sonnet-4-6"
CRITIQUE_MODEL = "claude-sonnet-4-6"
RERANK_MODEL = "claude-haiku-4-5"


def _process_one(
    question: dict,
    retriever: Retriever,
    client: ProClient,
    *,
    k: int = 5,
    enable_critique: bool = True,
) -> dict:
    """Pipeline for one question. Returns the export-shape dict."""
    # 1. Retrieve
    hits = retriever.search(question["text"], k=k)
    if not hits:
        # KB-miss path: synthesize a no-source answer with low confidence.
        return {
            "question_id": question["id"],
            "category": question.get("category", "general"),
            "question": question["text"],
            "answer": "Our knowledge base does not contain information to answer this question reliably.",
            "sources": [],
            "confidence": "low",
            "flags": ["KB-miss: no retrieved sources matched"],
            "evidence_quotes": [],
            "retrieved": [],
            "critique": None,
            "revision": None,
            "verification": {"fully_grounded": False, "grounded_claims": [],
                              "ungrounded_claims": [],
                              "cited_sources_resolved": [],
                              "cited_sources_not_in_kb": []},
        }

    # 2. Rerank
    hits = rerank(hits, question["text"], client, model=RERANK_MODEL)
    top = hits[:min(3, len(hits))]

    # 3. Draft
    draft = draft_answer(question, top, client, model=DRAFT_MODEL)

    # 4. Critique → revise if needed
    if enable_critique:
        crit = critique_draft(draft, client, model=CRITIQUE_MODEL)
        draft.critique = crit
        if crit.should_revise:
            draft = revise_draft(draft, crit, client, model=DRAFT_MODEL)
            # Re-attach the critique to the revised draft for the audit trail.
            draft.critique = crit

    # 5. Verify
    vr = verify(draft.answer, draft.sources)
    out = draft.as_export()
    out["verification"] = vr.as_dict()

    # If verifier found ungrounded numeric claims, downgrade confidence and add a flag
    if not vr.fully_grounded:
        if draft.confidence == "high":
            out["confidence"] = "medium"
        out["flags"] = list(set(out.get("flags", []) + [
            f"verifier: {len(vr.ungrounded_claims)} ungrounded claim(s): {vr.ungrounded_claims[:3]}"
        ]))
    return out


@dataclass
class RunReport:
    rfp_name: str
    total_questions: int
    answers: list[dict]
    review: dict
    composite: dict
    cost: dict
    elapsed_s: float

    def as_dict(self) -> dict:
        return {
            "rfp_name": self.rfp_name,
            "total_questions": self.total_questions,
            "answers": self.answers,
            "review": self.review,
            "composite": self.composite,
            "cost": self.cost,
            "elapsed_s": round(self.elapsed_s, 2),
        }


def run(
    rfp_name: str,
    questions: list[dict],
    *,
    parallel: int = 5,
    enable_critique: bool = True,
    out_dir: Path | None = None,
) -> RunReport:
    t0 = time.time()
    ledger = CostLedger()
    client = ProClient(ledger)
    retriever = Retriever()

    print(f"\n{'=' * 72}")
    print(f"  Pro · {rfp_name}   {len(questions)} questions, parallel={parallel}, critique={enable_critique}")
    print(f"{'=' * 72}")

    answers: list[dict | None] = [None] * len(questions)
    with concurrent.futures.ThreadPoolExecutor(max_workers=parallel) as pool:
        futs = {pool.submit(_process_one, q, retriever, client,
                             enable_critique=enable_critique): i
                 for i, q in enumerate(questions)}
        done = 0
        for fut in concurrent.futures.as_completed(futs):
            i = futs[fut]
            answers[i] = fut.result()
            done += 1
            a = answers[i] or {}
            conf = a.get("confidence", "?")
            n_src = len(a.get("sources") or [])
            grounded = "✓" if (a.get("verification") or {}).get("fully_grounded") else "•"
            print(f"  [{done}/{len(questions)}] {a.get('question_id'):>4}  conf={conf:<6}  sources={n_src}  grounded={grounded}")

    answers = [a for a in answers if a is not None]

    # Cross-answer review
    print("\n  Reviewing batch …")
    review = review_answers(answers, client, model=CRITIQUE_MODEL)
    review_dict = review.as_dict()
    if review.issues:
        for i in review.issues:
            print(f"    [{i.severity}] {i.kind} on {i.question_ids}: {i.summary}")
    else:
        print(f"    (no issues)")

    # Composite
    comp = composite_score(answers, review_dict)
    print(f"\n  Composite quality score: {comp.score:.1f}/100")
    print(f"    source coverage: {comp.source_coverage*100:.1f}%  "
          f"confidence index: {comp.confidence_index*100:.1f}%")
    print(f"    grounding rate:  {comp.grounding_rate*100:.1f}%  "
          f"reviewer clean:   {comp.reviewer_clean*100:.1f}%")

    cost = ledger.as_dict()
    print(f"\n  Cost: ${cost['total_cost']:.4f}  ({cost['total_calls']} API calls, "
          f"{cost['total_input_tokens']:,} in / {cost['total_output_tokens']:,} out)")
    for stage, s in cost["by_stage"].items():
        print(f"    {stage:<10} {s['calls']} calls  ${s['cost']:.4f}  {s['elapsed_ms']:.0f}ms")

    report = RunReport(
        rfp_name=rfp_name,
        total_questions=len(questions),
        answers=answers,
        review=review_dict,
        composite=comp.as_dict(),
        cost=cost,
        elapsed_s=time.time() - t0,
    )

    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"pro_{rfp_name.replace(' ', '_').lower()}.json"
        out_path.write_text(json.dumps(report.as_dict(), indent=2))
        print(f"\n  wrote {out_path}")

    return report
