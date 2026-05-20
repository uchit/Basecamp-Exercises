"""Async load-test harness.

No external dependency (no Locust). Fires N concurrent retrieval queries
against RetrieverV2, measures end-to-end latency, reports mean + p50 + p95 +
p99 + error rate. Use to verify the chunk-level dense retrieval holds up
under realistic concurrent load.

Run with:
    python -m pro.load_test 100   # 100 concurrent queries

Sample output:
    n=100  ok=100  err=0  mean=12.4ms  p50=11.8ms  p95=19.2ms  p99=24.6ms
"""
from __future__ import annotations

import asyncio
import statistics
import sys
import time

from .retrieval_v2 import RetrieverV2


# Realistic mix of question shapes pulled from the surprise + sample RFPs.
SAMPLE_QUERIES = [
    "What is your incident response SLA for P1?",
    "Per-seat pricing for 5000 endpoints",
    "Do you hold FedRAMP Moderate?",
    "How is EU data residency handled?",
    "Reference customers in financial services",
    "What encryption do you use at rest?",
    "Rate-limit policy for high-volume customers",
    "Multi-year contract discount stacking",
    "Webhook delivery retry policy",
    "Sub-processor management process",
]


def _percentile(sorted_data: list[float], p: float) -> float:
    if not sorted_data:
        return 0.0
    n = len(sorted_data)
    idx = (n - 1) * p / 100
    low = int(idx)
    high = min(low + 1, n - 1)
    frac = idx - low
    return sorted_data[low] + frac * (sorted_data[high] - sorted_data[low])


async def _one(retriever: RetrieverV2, query: str, k: int) -> tuple[bool, float]:
    """Wrap the sync retriever in run_in_executor so we get parallel exec."""
    loop = asyncio.get_running_loop()
    t0 = time.perf_counter()
    try:
        await loop.run_in_executor(None, lambda: retriever.search(query, k=k))
        return True, (time.perf_counter() - t0) * 1000
    except Exception:
        return False, (time.perf_counter() - t0) * 1000


async def run_load(*, n: int = 100, k: int = 5, concurrency: int = 25) -> dict:
    """Fire `n` queries with bounded concurrency. Returns aggregate stats."""
    retriever = RetrieverV2()
    sem = asyncio.Semaphore(concurrency)

    async def bounded(q):
        async with sem:
            return await _one(retriever, q, k)

    queries = (SAMPLE_QUERIES * ((n // len(SAMPLE_QUERIES)) + 1))[:n]
    t0 = time.perf_counter()
    results = await asyncio.gather(*[bounded(q) for q in queries])
    wall = (time.perf_counter() - t0) * 1000

    oks = [ms for ok, ms in results if ok]
    errs = [ms for ok, ms in results if not ok]
    oks.sort()

    return {
        "n": n,
        "ok": len(oks),
        "err": len(errs),
        "mean_ms": round(statistics.mean(oks), 2) if oks else 0,
        "p50_ms": round(_percentile(oks, 50), 2),
        "p95_ms": round(_percentile(oks, 95), 2),
        "p99_ms": round(_percentile(oks, 99), 2),
        "wall_clock_ms": round(wall, 2),
        "throughput_qps": round(len(oks) / (wall / 1000), 2) if wall > 0 else 0,
    }


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    stats = asyncio.run(run_load(n=n))
    print(f"n={stats['n']}  ok={stats['ok']}  err={stats['err']}  "
          f"mean={stats['mean_ms']}ms  p50={stats['p50_ms']}ms  "
          f"p95={stats['p95_ms']}ms  p99={stats['p99_ms']}ms  "
          f"throughput={stats['throughput_qps']} qps")


if __name__ == "__main__":
    main()
