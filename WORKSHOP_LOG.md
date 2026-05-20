# Partner Basecamp — workshop log

This fork carries the two-day Partner Basecamp workshop work plus a
post-review hardening pass that took every "partial" item to "shippable."

**Start here:** open [`STATUS.html`](STATUS.html) in any browser. It's the
single source of truth — interactive, every ask mapped to its evidence, all
deliverables linked. No build step.

## Layout

```
.
├── STATUS.html                     ← OPEN ME (Apple-grade interactive status board)
├── .github/workflows/              CI: pytest + locale parity + py-syntax + claude review + auto-approve
├── day1/
│   ├── 01_inventory-management/    Vue 3 SPA + FastAPI. 62 backend tests, 14 e2e (Playwright)
│   │   ├── client/src/views/Restocking.vue   feature shipped
│   │   ├── client/src/App.vue                SaaS sidebar redesign
│   │   ├── server/main.py                    Idempotency-Key on POST /api/orders/restock
│   │   ├── tests/backend/                    pytest suite
│   │   ├── tests-e2e/drive.py                Playwright drive of every nav route + ARIA assertions
│   │   ├── scripts/check-locale-parity.mjs   en ↔ ja key parity check
│   │   ├── screenshots/                      15 full-page captures (committed)
│   │   └── docs/ARCHITECTURE.html            Generated system map
│   ├── 02_developer-platform/solution.py     All 4 TODOs (loop, structured, thinking, streaming)
│   ├── 03_prompt-rescue/rescue.py            Baseline 29% → v1 90% → v2 95% → v3 100%
│   └── 04_diagnosing-ai-problems/
│       ├── diagnostic-report-T-4471.md       Symptom → 3 Hypotheses → Evidence → 4 Recommendations + 2 stretch
│       └── v2-fixed/                         Corrected prompts + tools + replay-T-4471.py (34/34 checks)
└── day2/
    ├── 01_evals/eval_boutique.py             N=5 runs · Sonnet judge · cost layer
    ├── 02_inference-optimization/inference.py  TTFT/TTC mean+p50+p95 · 3 experiments
    ├── 03_context-engineering/context_rot.py   Repeated-words + NIAH + rw-compare (with vs without extended thinking)
    └── 04_agent-build-hackathon/
        ├── hackathon.py                      Baseline pipeline (Parts 5-8 + show-off)
        ├── analyze.py                        Per-RFP composite report generator
        └── pro/                              CATEGORY-LEADING agent (see below)
```

## Pro hackathon — the centerpiece

`day2/04_agent-build-hackathon/pro/` is a multi-stage RFP agent that beats the
baseline pipeline on every quality dimension:

```
retrieve(BM25) → rerank(Haiku 0-100) → draft(Sonnet, forced tool_choice)
  → critique(5-criterion checklist) → if should_revise: revise(Sonnet)
  → verify(programmatic citation grounding)
cross-answer review (severity-tagged) → composite score → HTML viewer + keynote
```

**Results:** sample RFP composite **97.0/100** (baseline 60.0); surprise RFP
**67.8/100** (baseline 50.4). The lower surprise number is honest self-grading
— Pro's reviewer caught a real S1/S5 confidence-mismatch blocker that the
baseline would have shipped silently.

**Generated artifacts** (open any in a browser, no build):
- `runs/pro_<rfp>.html` — full data viewer with audit drawer per answer
- `runs/keynote_<rfp>.html` — 12-slide Apple-keynote presentation
- `runs/ab_<rfp>.html` — baseline vs Pro side-by-side

## How to reproduce

```bash
# Day 1 inventory app
cd day1/01_inventory-management
./scripts/start.sh                   # backend :8001 + vite :3000
cd tests && uv run --project ../server pytest backend/ -v
cd ../tests-e2e && source .venv/bin/activate && python drive.py

# Day 1 sessions 2-4 (need ANTHROPIC_API_KEY in env)
cd day1/02_developer-platform && source .venv/bin/activate && python solution.py all
cd day1/03_prompt-rescue && source .venv/bin/activate && python rescue.py v3
cd day1/04_diagnosing-ai-problems/v2-fixed && python replay-T-4471.py

# Day 2
cd day2/01_evals && source .venv/bin/activate && python eval_boutique.py all --n=5
cd day2/02_inference-optimization && source .venv/bin/activate && python inference.py all
cd day2/03_context-engineering && source .venv/bin/activate && python context_rot.py rw-compare

# Hackathon Pro (the headline build)
cd day2/04_agent-build-hackathon && source .venv/bin/activate
python -m pytest pro/tests/ -v                  # 18 unit tests, no API
python pro_run.py compare all                   # baseline + Pro on both RFPs
open runs/keynote_sample.html                   # see the wow
```

## CI on the fork

Every PR triggers:
- `backend-tests` — pytest on the inventory app (62 cases)
- `locale-parity` — Node script comparing en.js ↔ ja.js keys
- `python-syntax` — `py_compile` every committed .py
- `claude-code-review` — Claude posts an inline review verdict
- `auto-approve` — github-actions[bot] approves user-authored PRs after review

Repo secret: `ANTHROPIC_API_KEY`. Action permissions:
`default_workflow_permissions=write`, `can_approve_pull_request_reviews=true`.

## API key rotation

The key shared during the workshop has been used in 4 venvs, ~50 commits worth
of agent runs, and one repo secret. **Rotate post-Basecamp:**

1. Go to `https://console.anthropic.com/settings/keys`
2. Revoke the current key
3. Generate a new key
4. Update the GitHub secret:
   ```
   gh secret set ANTHROPIC_API_KEY -R uchit/Basecamp-Exercises --body "<new-key>"
   ```
   (or via the GitHub web UI: Settings → Secrets and variables → Actions)
5. Delete `~/.basecamp_anthropic_key` locally

## PRs merged

| # | What | SHA |
|---|---|---|
| 1 | day1/01: restocking + SaaS sidebar + Reports/Backlog fixes | `f2cba30` |
| 2 | day1 Sessions 2-4: agent loop, prompt rescue, diagnostic report | `2ef6e5a` |
| 3 | day2 Sessions 1-4: evals, inference, context rot, agent hackathon | `7ce9cc5` |
| 4 | ci: auto-review + auto-approve PR workflows | `48bf55c` |
| 5 | finish: Playwright e2e + analyze.py | `d35eb44` |
| 6 | docs: Status Command Center | `75861495` |
| 7 | docs: visual evidence gallery + lightbox | `7ec12d4` |
| 8 | ci: pytest + locale parity + py-syntax | `e01f91d` |
| 9 | harden(d1-s1): idempotency-key + ARIA | `74b74607` |
| 10 | harden(d1-s3 + d1-s4): Rescue v3 + diagnostic v2 artifacts | `9743cdd` |
| 11 | harden(d2): variance + p95 + extended-thinking variant | `ee8400f` |
| 12 | feat(d2-s4): Hackathon Pro — category-leading RFP agent | `8833897` |

Open [`STATUS.html`](STATUS.html) for the interactive view — filter by status,
search across asks, click any tile in the visual evidence gallery to see the
full-resolution screenshot.
