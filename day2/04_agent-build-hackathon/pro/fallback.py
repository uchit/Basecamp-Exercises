"""Graceful API degradation.

When the primary model returns 5xx (or any APIStatusError), drop to a
secondary model rather than failing the run. Mark every fallback in the
returned envelope so the audit trail records the degradation.

Failure ladder (configurable):
  claude-sonnet-4-6   primary
  claude-haiku-4-5    cheap + fast fallback
  claude-opus-4-7     last resort (premium)

Use:
    out = with_fallback(client, stage='draft',
                         primary='claude-sonnet-4-6',
                         secondaries=['claude-haiku-4-5'],
                         fn=lambda model: agent.draft_answer(...))
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import anthropic


@dataclass
class FallbackResult:
    value: Any
    model_used: str
    fell_back: bool
    attempts: int


def with_fallback(*, fn: Callable[[str], Any],
                    primary: str,
                    secondaries: list[str]) -> FallbackResult:
    """Try the primary first; on any APIStatusError 5xx (or rate limit
    exhaustion), iterate through secondaries in order.

    `fn(model)` should perform the API call with the supplied model id.
    """
    models = [primary] + list(secondaries)
    last_exc: Exception | None = None
    for i, m in enumerate(models, start=1):
        try:
            value = fn(m)
            return FallbackResult(
                value=value, model_used=m,
                fell_back=(i > 1), attempts=i,
            )
        except anthropic.APIStatusError as e:
            if 500 <= e.status_code < 600:
                last_exc = e
                continue
            raise
        except anthropic.RateLimitError as e:
            last_exc = e
            continue
        except Exception:
            # Non-API failures shouldn't trigger fallback — surface them.
            raise

    if last_exc:
        raise last_exc
    raise RuntimeError("with_fallback exhausted with no exception (impossible)")


DEFAULT_LADDER = {
    "claude-sonnet-4-6": ["claude-haiku-4-5", "claude-opus-4-7"],
    "claude-haiku-4-5":  ["claude-sonnet-4-6"],
    "claude-opus-4-7":   ["claude-sonnet-4-6", "claude-haiku-4-5"],
}


def default_secondaries_for(primary: str) -> list[str]:
    return DEFAULT_LADDER.get(primary, [])
