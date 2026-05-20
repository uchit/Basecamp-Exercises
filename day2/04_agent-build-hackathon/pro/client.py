"""Shared Anthropic client with retry, backoff, timeout, and a cost ledger.

Every call routed through this module accrues to a per-run CostLedger so the
runner can report total spend + per-stage breakdown.
"""
from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass, field
from typing import Any

import anthropic


# Pricing per million tokens (input / output). Update when Anthropic publishes
# new tiers. Used by both the ledger and the composite scorer.
PRICING: dict[str, dict[str, float]] = {
    "claude-haiku-4-5":  {"input": 0.25, "output": 1.25},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "claude-opus-4-7":   {"input": 15.00, "output": 75.00},
}


def cost_for(model: str, input_tokens: int, output_tokens: int) -> float:
    p = PRICING.get(model, {"input": 3.00, "output": 15.00})
    return input_tokens * p["input"] / 1_000_000 + output_tokens * p["output"] / 1_000_000


@dataclass
class CostEntry:
    stage: str
    model: str
    input_tokens: int
    output_tokens: int
    cost: float
    elapsed_ms: float


@dataclass
class CostLedger:
    """Accumulates per-call cost + timing. Group by stage with .by_stage()."""
    entries: list[CostEntry] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)

    def record(self, stage: str, model: str, input_tokens: int, output_tokens: int, elapsed_ms: float) -> None:
        self.entries.append(CostEntry(
            stage=stage, model=model,
            input_tokens=input_tokens, output_tokens=output_tokens,
            cost=cost_for(model, input_tokens, output_tokens),
            elapsed_ms=elapsed_ms,
        ))

    def total_cost(self) -> float:
        return sum(e.cost for e in self.entries)

    def total_calls(self) -> int:
        return len(self.entries)

    def total_input(self) -> int:
        return sum(e.input_tokens for e in self.entries)

    def total_output(self) -> int:
        return sum(e.output_tokens for e in self.entries)

    def wall_clock_s(self) -> float:
        return time.time() - self.started_at

    def by_stage(self) -> dict[str, dict]:
        agg: dict[str, dict] = {}
        for e in self.entries:
            a = agg.setdefault(e.stage, {"calls": 0, "input_tokens": 0,
                                          "output_tokens": 0, "cost": 0.0,
                                          "elapsed_ms": 0.0})
            a["calls"] += 1
            a["input_tokens"] += e.input_tokens
            a["output_tokens"] += e.output_tokens
            a["cost"] += e.cost
            a["elapsed_ms"] += e.elapsed_ms
        return agg

    def as_dict(self) -> dict:
        return {
            "total_cost": round(self.total_cost(), 6),
            "total_calls": self.total_calls(),
            "total_input_tokens": self.total_input(),
            "total_output_tokens": self.total_output(),
            "wall_clock_s": round(self.wall_clock_s(), 2),
            "by_stage": {k: {**v, "cost": round(v["cost"], 6),
                              "elapsed_ms": round(v["elapsed_ms"], 1)}
                          for k, v in self.by_stage().items()},
        }


class ProClient:
    """Anthropic client wrapper. Retries on 429/529 with exponential backoff +
    jitter. Every successful call records into the supplied CostLedger.
    """

    def __init__(self, ledger: CostLedger, *, timeout: float = 600.0):
        self.ledger = ledger
        self._client = anthropic.Anthropic(timeout=timeout)

    def messages_create(self, *, stage: str, model: str, **kwargs) -> Any:
        delays = [0.5, 1.0, 2.0, 4.0]
        last_exc: Exception | None = None
        for attempt in range(len(delays) + 1):
            t0 = time.perf_counter()
            try:
                response = self._client.messages.create(model=model, **kwargs)
                elapsed_ms = (time.perf_counter() - t0) * 1000
                usage = response.usage
                self.ledger.record(stage, model,
                                   usage.input_tokens, usage.output_tokens,
                                   elapsed_ms)
                return response
            except anthropic.RateLimitError as e:
                last_exc = e
            except anthropic.APIStatusError as e:
                if e.status_code in (529, 503, 504):
                    last_exc = e
                else:
                    raise
            if attempt < len(delays):
                wait = delays[attempt] + random.uniform(0, 0.2)
                time.sleep(wait)
        # Exhausted retries.
        if last_exc:
            raise last_exc
        raise RuntimeError("messages_create failed without an exception (impossible)")
