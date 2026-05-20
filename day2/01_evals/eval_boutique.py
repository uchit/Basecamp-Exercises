"""
Day 2 · Session 1 — Building an Eval (boutique shopping assistant).

Builds an eval suite around the deliberately-broken boutique agent in
Building_an_Eval.py, runs the 6 reference tasks against the broken agent,
applies the 3 fixes (system prompt, tool specs that list the catalog,
graceful KeyError handling), re-runs, and prints an A/B diff.

Usage:
    source .venv/bin/activate
    source ~/.basecamp_anthropic_key
    python eval_boutique.py            # baseline + improved
    python eval_boutique.py baseline   # baseline only
    python eval_boutique.py improved   # improved only
"""
from __future__ import annotations

import json, os, re, sys, time, traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy

from anthropic import Anthropic
from anthropic.types import TextBlock, ToolUseBlock

MODEL = "claude-haiku-4-5"
# Judge runs on a stronger model than the agent to avoid same-model grader
# bias (a Haiku grading Haiku output overestimates pass rate).
JUDGE_MODEL = "claude-sonnet-4-6"

# Pricing per million tokens (input / output) for the cost layer.
PRICING = {
    "claude-haiku-4-5":  {"input": 0.25, "output": 1.25},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "claude-opus-4-7":   {"input": 15.00, "output": 75.00},
}

def cost_for(model: str, input_tokens: int, output_tokens: int) -> float:
    p = PRICING.get(model, {"input": 3.00, "output": 15.00})
    return input_tokens * p["input"] / 1_000_000 + output_tokens * p["output"] / 1_000_000

client = Anthropic()


# ----------------------------------------------------------------------------
# Catalog + tool implementations. We expose two variants so we can A/B.
# ----------------------------------------------------------------------------

CATALOG = {
    "jeans": 49.99, "shirt": 29.99, "dress": 59.99, "jacket": 89.99,
    "sneakers": 74.99, "hat": 19.99, "socks": 9.99, "hoodie": 44.99,
    "shorts": 34.99, "t-shirt": 24.99, "sweater": 54.99, "belt": 24.99,
}

def get_product_bad(product: str):
    # Raw KeyError on miss — the broken implementation.
    return CATALOG[product]

def get_product_good(product: str):
    if product in CATALOG:
        return CATALOG[product]
    available = ", ".join(sorted(CATALOG.keys()))
    return f"Product '{product}' not found. Available products: {available}"

def calculate(op: str, input1: float, input2: float):
    if op == "+": return input1 + input2
    if op == "-": return input1 - input2
    if op == "*": return input1 * input2
    if op == "/": return input1 / input2
    if op == "**": return input1 ** input2
    return f"Unknown op: {op}"


# Tool specs — bad (one-word descriptions) vs good (catalog listed, op enum).

GET_PRODUCT_SPEC_BAD = {
    "name": "get_product",
    "description": "get_product",
    "input_schema": {
        "type": "object",
        "properties": {"product": {"type": "string", "description": "product"}},
        "required": ["product"],
    },
}

CALCULATE_SPEC_BAD = {
    "name": "calculate",
    "description": "calculator",
    "input_schema": {
        "type": "object",
        "properties": {
            "op": {"type": "string", "description": "operator"},
            "input1": {"type": "number", "description": "input1"},
            "input2": {"type": "number", "description": "input2"},
        },
        "required": ["op", "input1", "input2"],
    },
}

GET_PRODUCT_SPEC_GOOD = {
    "name": "get_product",
    "description": (
        "Look up the price of a product from the store catalog. "
        "Returns the price as a number. If the product is not in the catalog, "
        "returns a helpful message listing all available products."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "product": {
                "type": "string",
                "description": (
                    "Product name, lowercase. Available products: "
                    "belt, dress, hat, hoodie, jacket, jeans, shirt, "
                    "shorts, sneakers, socks, sweater, t-shirt."
                ),
            },
        },
        "required": ["product"],
    },
}

CALCULATE_SPEC_GOOD = {
    "name": "calculate",
    "description": "Perform a math operation on two numbers. Use this for any arithmetic instead of doing mental math.",
    "input_schema": {
        "type": "object",
        "properties": {
            "op": {"type": "string", "description": "The math operator to apply.", "enum": ["+", "-", "*", "/", "**"]},
            "input1": {"type": "number", "description": "The first operand."},
            "input2": {"type": "number", "description": "The second operand."},
        },
        "required": ["op", "input1", "input2"],
    },
}


SYSTEM_PROMPT_BAD = "You are a helpful assistant."

SYSTEM_PROMPT_GOOD = (
    "You are Boutique, a shopping assistant. You help customers find products, "
    "check prices, and calculate totals. ALWAYS use the get_product tool to look up "
    "prices — never guess from memory. If a product isn't found, suggest similar "
    "items from the catalog. NEVER do mental math; always call the calculate tool."
)


# ----------------------------------------------------------------------------
# Agent: run_agent(query, *, tool_specs, system_prompt, tool_registry)
# ----------------------------------------------------------------------------

def run_agent(query, *, tool_specs, system_prompt, tool_registry, eval_mode=True, max_turns=10, model=MODEL):
    messages = [{"role": "user", "content": query}]
    in_tokens = out_tokens = 0
    response = None
    for _ in range(max_turns):
        response = client.messages.create(
            model=MODEL,
            system=system_prompt,
            max_tokens=1024,
            tools=tool_specs,
            messages=messages,
        )
        in_tokens += response.usage.input_tokens
        out_tokens += response.usage.output_tokens
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            break

        tool_calls = [b for b in response.content if isinstance(b, ToolUseBlock)]
        if not tool_calls:
            break

        tool_results = []
        for call in tool_calls:
            try:
                out = str(tool_registry[call.name](**call.input))
            except Exception as e:
                out = f"Error: {e}"
            tool_results.append({"type": "tool_result", "tool_use_id": call.id, "content": out})
        messages.append({"role": "user", "content": tool_results})

    if eval_mode:
        return {
            "messages": messages,
            "usage": {"input_tokens": in_tokens, "output_tokens": out_tokens},
            "model": model,
        }
    return "\n".join(b.text for b in response.content if isinstance(b, TextBlock))


# ----------------------------------------------------------------------------
# Graders
# ----------------------------------------------------------------------------

def grade_response_contains(result, check, context=None):
    if check.lower() in result["final_text"].lower():
        return {"score": 1.0, "reason": f"found '{check}'"}
    return {"score": 0.0, "reason": f"'{check}' not in: {result['final_text'][:160]}"}

def grade_response_numeric(result, check, context=None):
    if isinstance(check, (int, float)):
        value, tol = float(check), 0.01
    else:
        value, tol = float(check["value"]), float(check.get("tolerance", 0.01))
    for num_str in re.findall(r"-?[\d,]+\.?\d*", result["final_text"]):
        try:
            n = float(num_str.replace(",", ""))
            if abs(n - value) <= tol:
                return {"score": 1.0, "reason": f"found {n} (expected {value} ± {tol})"}
        except ValueError:
            continue
    return {"score": 0.0, "reason": f"expected {value} ± {tol}, response had no match"}

def grade_tool_use(result, check, context=None):
    tool_name = check["tool_name"]
    expected = check.get("arguments")
    for call in result["tool_calls"]:
        if call["name"] != tool_name:
            continue
        if expected is None:
            return {"score": 1.0, "reason": f"'{tool_name}' was called"}
        args = call.get("arguments", {})
        ok = all(
            (isinstance(v, str) and isinstance(args.get(k), str) and v.lower() == args[k].lower())
            or args.get(k) == v
            for k, v in expected.items()
        )
        if ok:
            return {"score": 1.0, "reason": f"'{tool_name}' called with {expected}"}
    actual = [{"name": c["name"], "args": c.get("arguments", {})} for c in result["tool_calls"]]
    msg = f"'{tool_name}' not called with {expected}" if expected else f"'{tool_name}' never called"
    return {"score": 0.0, "reason": f"{msg}. Saw: {actual}"}


JUDGE_SYSTEM = """You are an eval grader. You receive:
- The original user query
- An AI agent's response
- A criterion to evaluate

Judge whether the response meets the criterion. Focus only on that criterion.
First line: PASS or FAIL.
Second line: one-sentence reason."""

def grade_llm_judge(result, check, context=None):
    query = (context or {}).get("query", "unknown")
    prompt = f"Original query: {query}\n\nAgent's response: {result['final_text']}\n\nCriterion: {check}"
    try:
        resp = client.messages.create(
            model=JUDGE_MODEL,
            max_tokens=150,
            temperature=0.0,
            system=JUDGE_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        first = text.split("\n", 1)[0].upper()
        reason = text.split("\n", 1)[1].strip() if "\n" in text else text
        if "PASS" in first:
            return {"score": 1.0, "reason": f"judge: {reason}"}
        if "FAIL" in first:
            return {"score": 0.0, "reason": f"judge: {reason}"}
        return {"score": 0.0, "reason": f"unparseable judge: {text[:160]}"}
    except Exception as e:
        return {"score": 0.0, "reason": f"judge error: {e}"}


GRADERS = {
    "response_contains": grade_response_contains,
    "response_numeric": grade_response_numeric,
    "tool_use": grade_tool_use,
    "llm_judge": grade_llm_judge,
}


# ----------------------------------------------------------------------------
# Tasks — 6 reference tasks covering: direct lookup, hyphen edge case,
# synonym/not-in-catalog, multi-tool, percentage calc, open-ended (LLM judge).
# ----------------------------------------------------------------------------

TASKS = [
    {
        "id": "price_jeans",
        "description": "Direct price lookup",
        "query": "How much do jeans cost?",
        "category": "product_lookup",
        "graders": [
            {"type": "response_contains", "checks": ["49.99"]},
            {"type": "tool_use", "checks": [{"tool_name": "get_product", "arguments": {"product": "jeans"}}]},
        ],
    },
    {
        "id": "price_tshirt",
        "description": "Hyphen edge case (t-shirt vs tshirt)",
        "query": "Price of a t-shirt?",
        "category": "product_lookup",
        "graders": [
            {"type": "response_contains", "checks": ["24.99"]},
            {"type": "tool_use", "checks": [{"tool_name": "get_product", "arguments": {"product": "t-shirt"}}]},
        ],
    },
    {
        "id": "price_shoes_synonym",
        "description": "Synonym not in catalog (shoes → sneakers)",
        "query": "How much for shoes?",
        "category": "product_lookup",
        "graders": [
            {"type": "tool_use", "checks": [{"tool_name": "get_product"}]},
            {"type": "response_contains", "checks": ["sneakers"]},
        ],
    },
    {
        "id": "total_shirts_belts",
        "description": "Multi-tool: 3 shirts + 2 belts",
        "query": "3 shirts and 2 belts, what's my total?",
        "category": "multi_tool",
        "graders": [
            {"type": "response_numeric", "checks": [{"value": 139.95, "tolerance": 0.10}]},
            {"type": "tool_use", "checks": [
                {"tool_name": "get_product"},
                {"tool_name": "calculate", "arguments": {"op": "*"}},
                {"tool_name": "calculate", "arguments": {"op": "+"}},
            ]},
        ],
    },
    {
        "id": "discount_jacket",
        "description": "20% off a jacket (89.99 → 71.99)",
        "query": "What's 20% off a jacket?",
        "category": "calculation",
        "graders": [
            {"type": "response_numeric", "checks": [{"value": 71.99, "tolerance": 0.10}]},
            {"type": "tool_use", "checks": [
                {"tool_name": "get_product"},
                {"tool_name": "calculate"},
            ]},
        ],
    },
    {
        "id": "what_do_you_sell",
        "description": "Open-ended (LLM judge)",
        "query": "What do you sell?",
        "category": "capabilities",
        "graders": [
            {"type": "llm_judge", "checks": [
                "Response describes or lists at least some of the available products",
                "Response is helpful and shopping-relevant (not dismissive or off-topic)",
            ]},
        ],
    },
]


# ----------------------------------------------------------------------------
# Eval runner
# ----------------------------------------------------------------------------

def parse_transcript(messages):
    final_text, tool_calls = "", []
    for msg in messages:
        if msg["role"] != "assistant":
            continue
        for block in msg["content"]:
            if isinstance(block, TextBlock):
                final_text = block.text
            elif isinstance(block, ToolUseBlock):
                tool_calls.append({"name": block.name, "arguments": block.input, "id": block.id})
    return {"final_text": final_text, "tool_calls": tool_calls}


def run_task(task, *, tool_specs, system_prompt, tool_registry, model=MODEL):
    t0 = time.time()
    try:
        raw = run_agent(task["query"], tool_specs=tool_specs, system_prompt=system_prompt,
                        tool_registry=tool_registry, model=model)
    except Exception:
        return {"task_id": task["id"], "passed": False, "grades": [],
                "error": traceback.format_exc()[:300],
                "elapsed": time.time() - t0, "cost": 0.0,
                "input_tokens": 0, "output_tokens": 0}

    elapsed = time.time() - t0
    parsed = parse_transcript(raw["messages"])
    usage = raw.get("usage", {})
    in_tok = usage.get("input_tokens", 0)
    out_tok = usage.get("output_tokens", 0)
    grades = []
    ctx = {"query": task["query"], "task_id": task["id"]}
    for grader in task["graders"]:
        fn = GRADERS.get(grader["type"])
        if not fn:
            grades.append({"type": grader["type"], "score": 0.0, "reason": "unknown grader"})
            continue
        for check in grader.get("checks", []):
            g = fn(parsed, check, ctx)
            grades.append({"type": grader["type"], "check": check, "score": g["score"], "reason": g["reason"]})
    passed = bool(grades) and all(g["score"] == 1.0 for g in grades)
    return {
        "task_id": task["id"],
        "description": task["description"],
        "passed": passed,
        "grades": grades,
        "elapsed": round(elapsed, 2),
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "cost": round(cost_for(model, in_tok, out_tok), 6),
        "tool_calls": [c["name"] for c in parsed["tool_calls"]],
        "final_text": parsed["final_text"],
    }


def run_eval(label, *, tool_specs, system_prompt, tool_registry, parallel=4, num_runs=5, model=MODEL):
    """Run every task `num_runs` times, aggregate, and report mean ± std + cost.

    With num_runs=1 the legacy behaviour is preserved; with num_runs=5 (default)
    every task gets a sample size large enough to compute variance — required
    for any honest 'baseline vs improved' claim.
    """
    import statistics
    print(f"\n{'=' * 72}")
    print(f"  {label}   ·   N={num_runs} runs/task   ·   judge={JUDGE_MODEL}")
    print(f"{'=' * 72}")

    # Build the workload: each task × num_runs.
    workload = [(t, run_idx) for t in TASKS for run_idx in range(num_runs)]
    raw_results = []
    with ThreadPoolExecutor(max_workers=parallel) as pool:
        futures = {pool.submit(run_task, t, tool_specs=tool_specs, system_prompt=system_prompt,
                               tool_registry=tool_registry, model=model): (t, ri) for t, ri in workload}
        for fut in as_completed(futures):
            raw_results.append(fut.result())

    # Group by task_id and aggregate.
    by_task: dict[str, list[dict]] = {}
    for r in raw_results:
        by_task.setdefault(r["task_id"], []).append(r)

    order = {t["id"]: i for i, t in enumerate(TASKS)}
    aggregated = []
    for tid in sorted(by_task, key=lambda x: order.get(x, 999)):
        runs = by_task[tid]
        passes = sum(1 for r in runs if r["passed"])
        elapsed = [r["elapsed"] for r in runs]
        agg = {
            "task_id": tid,
            "description": runs[0]["description"],
            "passes_n": passes,
            "total_n": len(runs),
            "pass_rate": passes / len(runs),
            "elapsed_mean": statistics.mean(elapsed),
            "elapsed_stdev": statistics.stdev(elapsed) if len(elapsed) > 1 else 0.0,
            "total_cost": sum(r["cost"] for r in runs),
            "total_input_tokens": sum(r["input_tokens"] for r in runs),
            "total_output_tokens": sum(r["output_tokens"] for r in runs),
            "runs": runs,
        }
        aggregated.append(agg)

    full_pass = sum(1 for a in aggregated if a["passes_n"] == a["total_n"])
    flaky = sum(1 for a in aggregated if 0 < a["passes_n"] < a["total_n"])
    always_fail = sum(1 for a in aggregated if a["passes_n"] == 0)
    total_cost = sum(a["total_cost"] for a in aggregated)

    print(f"\n  Tasks:       {len(aggregated)}")
    print(f"  Always pass: {full_pass}")
    print(f"  Flaky:       {flaky}  (pass on some runs, fail on others)")
    print(f"  Always fail: {always_fail}")
    print(f"  Total cost:  ${total_cost:.4f}  ({len(workload)} agent calls + {sum(1 for r in raw_results if any(g['type']=='llm_judge' for g in r['grades']))} judge calls)")
    print()
    for a in aggregated:
        mark = "PASS" if a["passes_n"] == a["total_n"] else ("FLAKY" if a["passes_n"] > 0 else "FAIL")
        bar = "█" * a["passes_n"] + "·" * (a["total_n"] - a["passes_n"])
        print(f"  [{mark:>5}] {a['task_id']:<22} {bar}  {a['passes_n']}/{a['total_n']}  "
              f"({a['elapsed_mean']:.1f}±{a['elapsed_stdev']:.1f}s, "
              f"${a['total_cost']:.4f})  {a['description']}")

    return aggregated


def diff_results(a, b, label_a, label_b):
    by = {r["task_id"]: r for r in a}
    print(f"\n  {'-' * 60}")
    print(f"  Per-task delta: {label_a}  →  {label_b}")
    print(f"  {'-' * 60}")
    flips_up = flips_down = 0
    for rb in b:
        ra = by.get(rb["task_id"])
        if not ra:
            continue
        ra_rate = ra.get("pass_rate", 1.0 if ra.get("passed") else 0.0)
        rb_rate = rb.get("pass_rate", 1.0 if rb.get("passed") else 0.0)
        if ra_rate == rb_rate:
            continue
        arrow = "↑" if rb_rate > ra_rate else "↓"
        if rb_rate > ra_rate:
            flips_up += 1
        else:
            flips_down += 1
        ra_str = f"{ra.get('passes_n','?')}/{ra.get('total_n','?')}" if "passes_n" in ra else str(ra.get("passed"))
        rb_str = f"{rb.get('passes_n','?')}/{rb.get('total_n','?')}" if "passes_n" in rb else str(rb.get("passed"))
        print(f"  {arrow} {rb['task_id']:<22}  {ra_str:>7} → {rb_str:<7}  {rb['description']}")
    print(f"\n  Δ improvements: +{flips_up}   regressions: -{flips_down}")


# ----------------------------------------------------------------------------
# Driver
# ----------------------------------------------------------------------------

def main():
    # CLI: `python eval_boutique.py [stage] [--n=5]`
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    stage = (args[0].lower() if args else "all")
    num_runs = 5
    for a in sys.argv[1:]:
        if a.startswith("--n="):
            num_runs = int(a.split("=", 1)[1])

    baseline = improved = None

    if stage in ("baseline", "all"):
        baseline = run_eval(
            "BASELINE — broken boutique agent",
            tool_specs=[GET_PRODUCT_SPEC_BAD, CALCULATE_SPEC_BAD],
            system_prompt=SYSTEM_PROMPT_BAD,
            tool_registry={"get_product": get_product_bad, "calculate": calculate},
            num_runs=num_runs,
        )

    if stage in ("improved", "all"):
        improved = run_eval(
            "IMPROVED — system prompt + tool specs + graceful errors",
            tool_specs=[GET_PRODUCT_SPEC_GOOD, CALCULATE_SPEC_GOOD],
            system_prompt=SYSTEM_PROMPT_GOOD,
            tool_registry={"get_product": get_product_good, "calculate": calculate},
            num_runs=num_runs,
        )

    if baseline and improved:
        diff_results(baseline, improved, "baseline", "improved")


if __name__ == "__main__":
    main()
