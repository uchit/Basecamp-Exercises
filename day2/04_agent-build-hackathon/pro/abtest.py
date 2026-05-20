"""Prompt / model A/B harness.

Two responsibilities:
  1. Variant registry — name → {model, system_prompt_override, settings}
  2. Assignment — deterministic-by-hash so the same run_id always lands on
     the same variant (reproducible). When unset, picks the control.

The runner consults `assign(variant_name=None)` to get the active variant
for the current run; the audit_db rows record `prompt_variant` so
downstream analytics can group by variant.
"""
from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field


@dataclass
class Variant:
    """One A/B variant. system_prompt_override is None when this variant
    reuses the default system prompt."""
    name: str
    model: str = "claude-sonnet-4-6"
    system_prompt_override: str | None = None
    settings: dict = field(default_factory=dict)
    weight: float = 1.0    # relative sampling weight; 0 = retired


# Built-in variant pool. Add new variants here; they're live in the next run.
VARIANTS: dict[str, Variant] = {
    # Production control — current Pro defaults.
    "control": Variant(
        name="control",
        model="claude-sonnet-4-6",
        system_prompt_override=None,
        settings={"critique_rounds": 1, "self_consistency_n": 1},
        weight=1.0,
    ),
    # Sonnet with iterative critique (3 rounds) — quality bias.
    "sonnet-iter3": Variant(
        name="sonnet-iter3",
        model="claude-sonnet-4-6",
        system_prompt_override=None,
        settings={"critique_rounds": 3, "self_consistency_n": 1},
        weight=1.0,
    ),
    # Sonnet with self-consistency (3 parallel drafts) — quality+cost bias.
    "sonnet-sc3": Variant(
        name="sonnet-sc3",
        model="claude-sonnet-4-6",
        system_prompt_override=None,
        settings={"critique_rounds": 1, "self_consistency_n": 3},
        weight=1.0,
    ),
    # Haiku stress test — cost bias to see if quality holds at 1/12 the price.
    "haiku-control": Variant(
        name="haiku-control",
        model="claude-haiku-4-5",
        system_prompt_override=None,
        settings={"critique_rounds": 1, "self_consistency_n": 1},
        weight=0.0,  # disabled until manually flipped on
    ),
    # Opus reach for the hardest RFPs.
    "opus-control": Variant(
        name="opus-control",
        model="claude-opus-4-7",
        system_prompt_override=None,
        settings={"critique_rounds": 1, "self_consistency_n": 1},
        weight=0.0,
    ),
}


def list_active_variants() -> list[Variant]:
    return [v for v in VARIANTS.values() if v.weight > 0]


def assign(run_id: str | None = None, *,
            forced: str | None = None,
            rng_seed: int | None = None) -> Variant:
    """Deterministic-by-run-id assignment.

    If forced is supplied, returns that variant directly (e.g. for manual
    experiments). Otherwise hashes the run_id into the cumulative weight
    distribution of active variants.
    """
    if forced:
        if forced not in VARIANTS:
            raise ValueError(f"unknown variant: {forced}")
        return VARIANTS[forced]

    active = list_active_variants()
    if not active:
        return VARIANTS["control"]

    total = sum(v.weight for v in active)
    # Map run_id → uniform float in [0, 1).
    if run_id:
        h = int(hashlib.sha256(run_id.encode()).hexdigest(), 16)
        roll = (h % 10_000_000) / 10_000_000
    else:
        roll = random.Random(rng_seed).random()

    cumulative = 0.0
    for v in active:
        cumulative += v.weight / total
        if roll < cumulative:
            return v
    return active[-1]  # numerical floor


def register(variant: Variant) -> None:
    """Add or overwrite a variant. Called by external configs."""
    VARIANTS[variant.name] = variant


def disable(name: str) -> None:
    """Retire a variant (weight=0) without removing its history."""
    if name in VARIANTS:
        VARIANTS[name].weight = 0.0
