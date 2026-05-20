"""Token-budget enforcement.

Three caps:
  per_question_usd     hard limit on one question's spend across all stages
  per_rfp_usd          hard limit on one RFP's total spend
  per_day_usd          hard limit on aggregate spend in a 24h window

Limits are advisory by default (warn + continue) and enforcing on explicit
flip. A token-budget breach in a real RFP run should fail-fast, not silently
roll on into a five-figure invoice.
"""
from __future__ import annotations

import time
from dataclasses import dataclass


class BudgetExceeded(Exception):
    """Raised when a hard cap is exceeded and enforcement is on."""


@dataclass
class BudgetConfig:
    per_question_usd: float = 1.00
    per_rfp_usd: float = 10.00
    per_day_usd: float = 100.00
    enforce: bool = True  # False = warn-only


class BudgetTracker:
    """Owns the running totals + decides whether to allow the next call.

    Use:
        tracker = BudgetTracker(BudgetConfig())
        tracker.note_call(stage='draft', question_id='Q1', cost=0.0042)
        tracker.assert_within_budget(question_id='Q1')   # raises if over
    """

    def __init__(self, config: BudgetConfig | None = None):
        self.config = config or BudgetConfig()
        self._per_question: dict[str, float] = {}
        self._per_rfp: float = 0.0
        self._per_day: list[tuple[float, float]] = []  # (ts, cost)

    def note_call(self, *, stage: str, question_id: str | None, cost: float) -> None:
        ts = time.time()
        self._per_rfp += cost
        if question_id:
            self._per_question[question_id] = (
                self._per_question.get(question_id, 0.0) + cost
            )
        # Trim per-day window to last 24h
        cutoff = ts - 86400
        self._per_day = [(t, c) for (t, c) in self._per_day if t >= cutoff]
        self._per_day.append((ts, cost))

    def per_question_total(self, question_id: str) -> float:
        return self._per_question.get(question_id, 0.0)

    def per_rfp_total(self) -> float:
        return self._per_rfp

    def per_day_total(self) -> float:
        cutoff = time.time() - 86400
        return sum(c for (t, c) in self._per_day if t >= cutoff)

    def assert_within_budget(self, *, question_id: str | None = None) -> None:
        violations: list[str] = []
        if question_id is not None:
            q = self.per_question_total(question_id)
            if q > self.config.per_question_usd:
                violations.append(
                    f"per-question cap exceeded for {question_id}: "
                    f"${q:.4f} > ${self.config.per_question_usd:.2f}"
                )
        if self._per_rfp > self.config.per_rfp_usd:
            violations.append(
                f"per-RFP cap exceeded: ${self._per_rfp:.4f} > "
                f"${self.config.per_rfp_usd:.2f}"
            )
        d = self.per_day_total()
        if d > self.config.per_day_usd:
            violations.append(
                f"per-day cap exceeded: ${d:.4f} > ${self.config.per_day_usd:.2f}"
            )
        if violations:
            msg = "; ".join(violations)
            if self.config.enforce:
                raise BudgetExceeded(msg)
            # Warn-only path — emit a structured log line.
            try:
                from .observe import warn
                warn("budget.exceeded", details=msg)
            except Exception:
                pass

    def snapshot(self) -> dict:
        return {
            "per_question": dict(self._per_question),
            "per_rfp": round(self._per_rfp, 4),
            "per_day": round(self.per_day_total(), 4),
            "config": {
                "per_question_usd": self.config.per_question_usd,
                "per_rfp_usd": self.config.per_rfp_usd,
                "per_day_usd": self.config.per_day_usd,
                "enforce": self.config.enforce,
            },
        }
