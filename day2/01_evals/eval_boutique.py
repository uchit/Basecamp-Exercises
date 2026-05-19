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
JUDGE_MODEL = "claude-haiku-4-5"

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

def run_agent(query, *, tool_specs, system_prompt, tool_registry, eval_mode=True, max_turns=10):
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
        return {"messages": messages, "usage": {"input_tokens": in_tokens, "output_tokens": out_tokens}}
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


def run_task(task, *, tool_specs, system_prompt, tool_registry):
    t0 = time.time()
    try:
        raw = run_agent(task["query"], tool_specs=tool_specs, system_prompt=system_prompt,
                        tool_registry=tool_registry)
    except Exception:
        return {"task_id": task["id"], "passed": False, "grades": [],
                "error": traceback.format_exc()[:300], "elapsed": time.time() - t0}

    elapsed = time.time() - t0
    parsed = parse_transcript(raw["messages"])
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
        "tool_calls": [c["name"] for c in parsed["tool_calls"]],
        "final_text": parsed["final_text"],
    }


def run_eval(label, *, tool_specs, system_prompt, tool_registry, parallel=4):
    print(f"\n{'=' * 72}")
    print(f"  {label}")
    print(f"{'=' * 72}")
    results = []
    with ThreadPoolExecutor(max_workers=parallel) as pool:
        futures = {pool.submit(run_task, t, tool_specs=tool_specs, system_prompt=system_prompt,
                               tool_registry=tool_registry): t for t in TASKS}
        for fut in as_completed(futures):
            results.append(fut.result())
    order = {t["id"]: i for i, t in enumerate(TASKS)}
    results.sort(key=lambda r: order[r["task_id"]])

    passed = sum(1 for r in results if r["passed"])
    pct = round(100 * passed / len(results))
    print(f"\n  Score: {passed}/{len(results)}  ({pct}%)\n")
    for r in results:
        mark = "PASS" if r["passed"] else "FAIL"
        print(f"  [{mark}] {r['task_id']:<22} [{r['elapsed']:>4.1f}s, tools={','.join(r['tool_calls']) or '—'}]  {r['description']}")
        for g in r["grades"]:
            sign = "+" if g["score"] == 1.0 else "-"
            print(f"        {sign} {g['type']:<18} {g['reason'][:120]}")
        if r.get("error"):
            print(f"        ! {r['error'][:200]}")
    return results


def diff_results(a, b, label_a, label_b):
    by = {r["task_id"]: r for r in a}
    print(f"\n  {'-' * 60}")
    print(f"  Per-task delta: {label_a}  →  {label_b}")
    print(f"  {'-' * 60}")
    flips_up = flips_down = 0
    for rb in b:
        ra = by.get(rb["task_id"])
        if not ra or ra["passed"] == rb["passed"]:
            continue
        arrow = "↑" if rb["passed"] else "↓"
        if rb["passed"]:
            flips_up += 1
        else:
            flips_down += 1
        print(f"  {arrow} {rb['task_id']:<22}  {ra['passed']!s:>5} → {rb['passed']!s:<5}  {rb['description']}")
    print(f"\n  Δ improvements: +{flips_up}   regressions: -{flips_down}")


# ----------------------------------------------------------------------------
# Driver
# ----------------------------------------------------------------------------

def main():
    stage = sys.argv[1].lower() if len(sys.argv) > 1 else "all"

    baseline = improved = None

    if stage in ("baseline", "all"):
        baseline = run_eval(
            "BASELINE — broken boutique agent",
            tool_specs=[GET_PRODUCT_SPEC_BAD, CALCULATE_SPEC_BAD],
            system_prompt=SYSTEM_PROMPT_BAD,
            tool_registry={"get_product": get_product_bad, "calculate": calculate},
        )

    if stage in ("improved", "all"):
        improved = run_eval(
            "IMPROVED — system prompt + tool specs + graceful errors",
            tool_specs=[GET_PRODUCT_SPEC_GOOD, CALCULATE_SPEC_GOOD],
            system_prompt=SYSTEM_PROMPT_GOOD,
            tool_registry={"get_product": get_product_good, "calculate": calculate},
        )

    if baseline and improved:
        diff_results(baseline, improved, "baseline", "improved")


if __name__ == "__main__":
    main()
