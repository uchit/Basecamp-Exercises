"""
Day 2 · Session 2 — Inference Optimization driver.

Implements the six TODOs from Inference_Optimization.py:

  1. compute_otps(ttft, total_time, output_tokens)
  2. calculate_cost(model, input_tokens, output_tokens)
  3. CALCULATOR_TOOL schema
  4. measure_tool_use_latency — 2-step round trip
  5. cached_request — single-turn cache cold vs warm
  6. chat() — multi-turn cache_control progression

Runs three experiments (3 runs/model to keep cost ~$0.30):

  A. Streaming benchmark: Haiku vs Sonnet vs Opus, TTFT/TTC/OTPS/cost
  B. Tool use overhead: no_tool vs with_tool
  C. Caching: single-turn cold vs warm, then 5-turn cache progression

Usage:
    source .venv/bin/activate && source ~/.basecamp_anthropic_key
    python inference.py            # all three experiments
    python inference.py models     # just (A)
    python inference.py tools      # just (B)
    python inference.py cache      # just (C)
"""
from __future__ import annotations

import os, sys, time, statistics
from dataclasses import dataclass, field
from typing import List, Optional

import anthropic
from tabulate import tabulate

# Pricing per million tokens (input / output). Use the actual model IDs the API
# returns; pricing matches the workshop notebook's PRICING table updated to the
# 4.x family.
PRICING = {
    "claude-haiku-4-5":  {"input": 0.25, "output": 1.25},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "claude-opus-4-7":   {"input": 15.00, "output": 75.00},
}

MODEL_HAIKU  = "claude-haiku-4-5"
MODEL_SONNET = "claude-sonnet-4-6"
MODEL_OPUS   = "claude-opus-4-7"
DEFAULT_MODEL = MODEL_SONNET

RUNS_PER_MODEL = 3  # keep cost down; workshop notebook uses 5

client = anthropic.Anthropic()


@dataclass
class BenchmarkResult:
    ttft: float
    total_time: float
    input_tokens: int
    output_tokens: int
    model: str
    test_name: str
    otps: float = 0.0
    cost: float = 0.0


@dataclass
class BenchmarkSuite:
    results: List[BenchmarkResult] = field(default_factory=list)

    def add(self, r: BenchmarkResult) -> None:
        self.results.append(r)

    def clear(self) -> None:
        self.results.clear()

    def summary(self, group_by: str = "test_name") -> str:
        if not self.results:
            return "(no results)"
        groups: dict[str, list[BenchmarkResult]] = {}
        for r in self.results:
            groups.setdefault(getattr(r, group_by), []).append(r)
        rows = []
        for name, group in groups.items():
            rows.append([
                name,
                len(group),
                f"{statistics.mean(r.ttft * 1000 for r in group):.0f}",
                f"{statistics.mean(r.total_time * 1000 for r in group):.0f}",
                f"{statistics.mean(r.otps for r in group):.1f}",
                f"${sum(r.cost for r in group) * 1000:.4f}",
            ])
        return tabulate(rows, headers=["Test", "Runs", "TTFT(ms)", "TTC(ms)", "OTPS", "$/1K calls"], tablefmt="github")


# ----------------------------------------------------------------------------
# TODO 1 — compute_otps
# ----------------------------------------------------------------------------

def compute_otps(ttft: float, total_time: float, output_tokens: int) -> tuple[float, float]:
    """Tokens-per-second based on generation time (TTC minus TTFT). Returns (otps, gen_time)."""
    gen_time = max(total_time - ttft, 1e-6)
    otps = output_tokens / gen_time if output_tokens else 0.0
    return otps, gen_time


# ----------------------------------------------------------------------------
# TODO 2 — calculate_cost
# ----------------------------------------------------------------------------

def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> tuple[float, float, float]:
    """Returns (input_cost, output_cost, total_cost) in dollars."""
    prices = PRICING.get(model, {"input": 3.00, "output": 15.00})
    in_cost = input_tokens * prices["input"] / 1_000_000
    out_cost = output_tokens * prices["output"] / 1_000_000
    return in_cost, out_cost, in_cost + out_cost


# ----------------------------------------------------------------------------
# Streaming helper + measurement
# ----------------------------------------------------------------------------

def _stream_request(prompt: str, model: str = DEFAULT_MODEL, max_tokens: int = 256):
    ttft = None
    start = time.perf_counter()
    with client.messages.stream(
        model=model, max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        for event in stream:
            if ttft is None and event.type == "content_block_start":
                ttft = time.perf_counter() - start
        response = stream.get_final_message()
    total_time = time.perf_counter() - start
    return ttft or total_time, total_time, response


def measure_streaming_latency(prompt: str, model: str = DEFAULT_MODEL,
                              max_tokens: int = 256, test_name: str = "streaming") -> BenchmarkResult:
    ttft, total_time, response = _stream_request(prompt, model, max_tokens)
    usage = response.usage
    _, _, cost = calculate_cost(model, usage.input_tokens, usage.output_tokens)
    otps, _ = compute_otps(ttft, total_time, usage.output_tokens)
    return BenchmarkResult(
        ttft=ttft, total_time=total_time,
        input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
        model=model, test_name=test_name, otps=otps, cost=cost,
    )


# ----------------------------------------------------------------------------
# TODO 3 — CALCULATOR_TOOL  + TODO 4 — measure_tool_use_latency
# ----------------------------------------------------------------------------

CALCULATOR_TOOL = {
    "name": "calculator",
    "description": "Perform a basic arithmetic operation on two numbers.",
    "input_schema": {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["add", "subtract", "multiply", "divide"],
                "description": "Which arithmetic operation to apply.",
            },
            "operands": {
                "type": "array",
                "items": {"type": "number"},
                "minItems": 2, "maxItems": 2,
                "description": "Exactly two numeric operands.",
            },
        },
        "required": ["operation", "operands"],
    },
}


def execute_calculator(operation: str, operands: list) -> float:
    a, b = operands[0], operands[1]
    return {"add": a + b, "subtract": a - b, "multiply": a * b, "divide": a / b}[operation]


def measure_tool_use_latency(prompt: str, model: str = DEFAULT_MODEL, max_tokens: int = 256):
    start = time.perf_counter()
    first = client.messages.create(
        model=model, max_tokens=max_tokens, tools=[CALCULATOR_TOOL],
        messages=[{"role": "user", "content": prompt}],
    )
    ttft = time.perf_counter() - start

    tool_use_block = next((b for b in first.content if b.type == "tool_use"), None)
    if tool_use_block is None:
        total_time = time.perf_counter() - start
        return ttft, total_time, "(no tool used)", first.usage.input_tokens, first.usage.output_tokens

    result = execute_calculator(**tool_use_block.input)
    second = client.messages.create(
        model=model, max_tokens=max_tokens, tools=[CALCULATOR_TOOL],
        messages=[
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": first.content},
            {"role": "user", "content": [{
                "type": "tool_result",
                "tool_use_id": tool_use_block.id,
                "content": str(result),
            }]},
        ],
    )
    total_time = time.perf_counter() - start
    text = "".join(b.text for b in second.content if hasattr(b, "text"))
    in_tok = first.usage.input_tokens + second.usage.input_tokens
    out_tok = first.usage.output_tokens + second.usage.output_tokens
    return ttft, total_time, text, in_tok, out_tok


# ----------------------------------------------------------------------------
# TODO 5 — cached_request (single-turn cache)
# ----------------------------------------------------------------------------

CACHE_SYSTEM_TEXT = (
    "You are an expert API documentation assistant. You help developers understand REST API design, "
    "authentication patterns, security best practices, rate limiting, pagination, error handling, "
    "versioning strategies, webhook design, and performance optimization. "
    "Always provide concrete examples with HTTP methods and status codes.\n"
) * 20  # roughly 1.5K tokens — above the 1024-token cache floor


def cached_request(question: str, model: str = MODEL_SONNET):
    system_block = [{
        "type": "text",
        "text": CACHE_SYSTEM_TEXT,
        "cache_control": {"type": "ephemeral"},
    }]
    start = time.perf_counter()
    response = client.messages.create(
        model=model, max_tokens=256, system=system_block,
        messages=[{"role": "user", "content": question}],
    )
    elapsed = time.perf_counter() - start
    return response, elapsed


# ----------------------------------------------------------------------------
# TODO 6 — chat() with multi-turn cache_control
# ----------------------------------------------------------------------------

MULTITURN_SYSTEM = [{
    "type": "text",
    "text": (
        "You are a helpful API design consultant. You specialize in REST API design, "
        "authentication patterns, rate limiting, pagination, error handling, versioning "
        "strategies, webhook design, and performance optimization. Always provide concrete "
        "examples with HTTP methods, status codes, request/response schemas, and curl commands.\n"
    ) * 20,
    "cache_control": {"type": "ephemeral"},
}]


def chat(messages: list, new_question: str, model: str = MODEL_SONNET):
    # Strip any prior cache_control by flattening list content back to plain strings.
    for m in messages:
        if m["role"] == "assistant" and isinstance(m["content"], list):
            m["content"] = m["content"][0].get("text", "")

    # Mark the most recent assistant turn as a cache breakpoint, so the API
    # caches the entire conversation up through that turn.
    if messages and messages[-1]["role"] == "assistant":
        prior = messages[-1]["content"]
        messages[-1]["content"] = [{
            "type": "text",
            "text": prior if isinstance(prior, str) else prior[0]["text"],
            "cache_control": {"type": "ephemeral"},
        }]

    messages.append({"role": "user", "content": new_question})
    start = time.perf_counter()
    response = client.messages.create(
        model=model, max_tokens=300, system=MULTITURN_SYSTEM, messages=messages,
    )
    elapsed = time.perf_counter() - start
    answer = response.content[0].text
    messages.append({"role": "assistant", "content": answer})
    return answer, elapsed, response.usage


# ----------------------------------------------------------------------------
# Experiment runners
# ----------------------------------------------------------------------------

def hr(title: str) -> None:
    print()
    print("=" * 72)
    print(f"  {title}")
    print("=" * 72)


def experiment_a_models() -> None:
    hr("A · Streaming benchmark: Haiku vs Sonnet vs Opus")
    suite = BenchmarkSuite()
    prompt = "What is machine learning? Answer in 2 sentences."
    for model, label in [(MODEL_HAIKU, "haiku"), (MODEL_SONNET, "sonnet"), (MODEL_OPUS, "opus")]:
        print(f"\n  Benchmarking {label} ({RUNS_PER_MODEL} runs)…")
        for i in range(RUNS_PER_MODEL):
            try:
                r = measure_streaming_latency(prompt, model=model, test_name=label)
                suite.add(r)
                print(f"    run {i+1}: TTFT={r.ttft*1000:>5.0f}ms  TTC={r.total_time*1000:>5.0f}ms  "
                      f"OTPS={r.otps:>5.1f}  cost=${r.cost:.6f}")
            except anthropic.APIStatusError as e:
                print(f"    run {i+1}: SKIPPED ({e.status_code} {e.message[:60]})")
    print()
    print(suite.summary())


def experiment_b_tools() -> None:
    hr("B · Tool-use overhead: no_tool vs with_tool (same arithmetic question)")
    suite = BenchmarkSuite()

    print("\n  no_tool (model does its own math):")
    for i in range(RUNS_PER_MODEL):
        r = measure_streaming_latency(
            "What is forty-two times seventeen? Show your work.",
            test_name="no_tool",
        )
        suite.add(r)
        print(f"    run {i+1}: TTFT={r.ttft*1000:>5.0f}ms  TTC={r.total_time*1000:>5.0f}ms  cost=${r.cost:.6f}")

    print("\n  with_tool (calculator round-trip):")
    for i in range(RUNS_PER_MODEL):
        ttft, total_time, text, in_tok, out_tok = measure_tool_use_latency(
            "What is 42 * 17? Use the calculator."
        )
        _, _, cost = calculate_cost(DEFAULT_MODEL, in_tok, out_tok)
        otps, _ = compute_otps(ttft, total_time, out_tok)
        suite.add(BenchmarkResult(
            ttft=ttft, total_time=total_time,
            input_tokens=in_tok, output_tokens=out_tok,
            model=DEFAULT_MODEL, test_name="with_tool",
            otps=otps, cost=cost,
        ))
        print(f"    run {i+1}: TTFT={ttft*1000:>5.0f}ms  TTC={total_time*1000:>5.0f}ms  cost=${cost:.6f}")
    print()
    print(suite.summary())


def experiment_c_cache() -> None:
    hr("C · Caching — single-turn cold vs warm")
    r1, t1 = cached_request("What is REST?")
    print(f"\n  Cold call ({t1*1000:.0f}ms)")
    print(f"    cache_creation_input_tokens: {r1.usage.cache_creation_input_tokens or 0}")
    print(f"    cache_read_input_tokens:     {r1.usage.cache_read_input_tokens or 0}")
    print(f"    plain input_tokens:          {r1.usage.input_tokens}")

    r2, t2 = cached_request("What is OAuth?")
    print(f"\n  Warm call ({t2*1000:.0f}ms — same system prompt, different question)")
    print(f"    cache_creation_input_tokens: {r2.usage.cache_creation_input_tokens or 0}")
    print(f"    cache_read_input_tokens:     {r2.usage.cache_read_input_tokens or 0}")
    print(f"    plain input_tokens:          {r2.usage.input_tokens}")

    delta = t1 - t2
    print(f"\n  Δ wall-clock: cold − warm = {delta*1000:+.0f}ms")

    hr("C′ · Caching — 5-turn conversation, breakpoint advances every turn")
    conv: list = []
    questions = [
        "Design a REST API for a todo app. Include all endpoints.",
        "Now add authentication. What changes?",
        "Add rate limiting. How should the headers look?",
        "Now add team support — users can share todo lists.",
        "Summarize the full API design so far.",
    ]
    for i, q in enumerate(questions, 1):
        _, elapsed, usage = chat(conv, q)
        cached_tok = usage.cache_read_input_tokens or 0
        created_tok = usage.cache_creation_input_tokens or 0
        plain_tok = usage.input_tokens
        print(f"  turn {i}: {elapsed*1000:>5.0f}ms  | cached_read={cached_tok:>5}  "
              f"cache_created={created_tok:>5}  plain_in={plain_tok:>5}")


def main() -> None:
    stage = (sys.argv[1].lower() if len(sys.argv) > 1 else "all")
    if stage in ("models", "all"):
        experiment_a_models()
    if stage in ("tools", "all"):
        experiment_b_tools()
    if stage in ("cache", "all"):
        experiment_c_cache()


if __name__ == "__main__":
    main()
