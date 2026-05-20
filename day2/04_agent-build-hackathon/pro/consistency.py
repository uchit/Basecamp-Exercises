"""Self-consistency — generate N independent drafts in parallel, pick the
most-consistent answer.

For hard / ambiguous questions, single-shot drafting is noisy. Three parallel
drafts + a selector reduces variance and catches one-shot hallucinations.

Selection strategy:
  - Vote on confidence (medium ≥ low)
  - Vote on set of cited sources (intersection-of-three preferred)
  - Pick the draft whose answer text scores highest on Levenshtein-similarity
    to the modal answer (closest-to-center)
"""
from __future__ import annotations

import concurrent.futures
import statistics
from collections import Counter
from dataclasses import dataclass

from .agent import Draft, draft_answer
from .client import ProClient
from .retrieval import RetrievalHit


@dataclass
class ConsistencyResult:
    chosen: Draft
    drafts: list[Draft]
    agreement_score: float       # 0..1; 1.0 = all three identical on sources+confidence
    rationale: str

    def as_dict(self) -> dict:
        return {
            "chosen_answer": self.chosen.answer,
            "agreement_score": round(self.agreement_score, 3),
            "rationale": self.rationale,
            "n_drafts": len(self.drafts),
        }


def _lev_similarity(a: str, b: str) -> float:
    """Cheap normalized Levenshtein-like similarity. Uses Python stdlib only
    (difflib) so it works without rapidfuzz. Returns 0..1."""
    import difflib
    return difflib.SequenceMatcher(None, a, b).ratio()


def self_consistency_draft(
    question: dict,
    retrieved: list[RetrievalHit],
    client: ProClient,
    *,
    n: int = 3,
    system_prompt: str | None = None,
    model: str = "claude-sonnet-4-6",
) -> ConsistencyResult:
    """Generate N parallel drafts and select the most consistent.

    n=1 short-circuits to a single draft (no API overhead beyond standard).
    n=3 is the sweet spot: catches single-draft variance without quadrupling cost.
    """
    if n <= 1:
        d = draft_answer(question, retrieved, client, model=model)
        return ConsistencyResult(chosen=d, drafts=[d], agreement_score=1.0,
                                  rationale="n=1 — single draft path")

    # Parallel draft generation. Each call independent; client handles
    # retry/cost ledger.
    drafts: list[Draft] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=n) as pool:
        futs = [pool.submit(draft_answer, question, retrieved, client, model=model)
                for _ in range(n)]
        for f in concurrent.futures.as_completed(futs):
            try:
                drafts.append(f.result())
            except Exception:
                continue

    if not drafts:
        # Fallback to a single sequential attempt if all parallel failed.
        d = draft_answer(question, retrieved, client, model=model)
        return ConsistencyResult(chosen=d, drafts=[d], agreement_score=0.0,
                                  rationale="all parallel drafts failed; fell back to single")

    if len(drafts) == 1:
        return ConsistencyResult(chosen=drafts[0], drafts=drafts,
                                  agreement_score=1.0,
                                  rationale="only one draft survived; using it")

    # Agreement on cited sources (set intersection size / union size).
    source_sets = [frozenset(d.sources) for d in drafts]
    union = set().union(*source_sets)
    intersection = set.intersection(*[set(s) for s in source_sets])
    src_agreement = len(intersection) / max(len(union), 1)

    # Agreement on confidence (modal).
    conf_counter = Counter(d.confidence for d in drafts)
    most_common_conf, conf_count = conf_counter.most_common(1)[0]
    conf_agreement = conf_count / len(drafts)

    # Closest-to-center selection: pick the draft whose answer is most
    # similar to the other two on average.
    avg_sim: list[float] = []
    for i, d in enumerate(drafts):
        sims = [_lev_similarity(d.answer, other.answer)
                for j, other in enumerate(drafts) if j != i]
        avg_sim.append(statistics.mean(sims) if sims else 0.0)
    chosen_idx = max(range(len(drafts)), key=lambda i: (
        # Prefer drafts whose confidence matches the modal vote
        (1 if drafts[i].confidence == most_common_conf else 0),
        # Prefer drafts that cite the intersection set
        sum(1 for s in drafts[i].sources if s in intersection),
        # Prefer drafts closest to the center
        avg_sim[i],
    ))

    agreement = (src_agreement + conf_agreement) / 2

    return ConsistencyResult(
        chosen=drafts[chosen_idx],
        drafts=drafts,
        agreement_score=agreement,
        rationale=(
            f"chosen draft #{chosen_idx + 1} of {len(drafts)} · "
            f"confidence agreement {conf_agreement:.0%} (modal={most_common_conf}) · "
            f"source agreement {src_agreement:.0%} "
            f"(intersection={sorted(intersection)})"
        ),
    )
