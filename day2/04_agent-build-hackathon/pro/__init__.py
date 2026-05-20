"""Hackathon Pro — category-leading RFP agent.

Architecture:
  parse → BM25 retrieve → Claude rerank → structured draft → critique → revise
  → citation verify → cross-answer review → composite score → HTML viewer

Wow factors:
  - Zero JSON parse failures (forced structured output via tool_choice)
  - Evidence quotes per claim (verbatim source excerpts surfaced in audit trail)
  - Automated citation verifier (every numeric + currency claim grounded)
  - Reflexion: critique-then-revise loop with multi-criterion checklist
  - Apple-grade HTML viewer with confidence heatmap + audit trail per answer
  - A/B comparator vs baseline with side-by-side HTML report
  - Composite quality score (source coverage / confidence index / eval pass /
    reviewer clean) defensible to a CFO
  - Per-stage telemetry: timings, tokens, cost
  - Retry/backoff on 429/529, graceful degradation on KB-miss
"""

__version__ = "1.0.0"
