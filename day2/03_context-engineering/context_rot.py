"""
Day 2 · Session 3 — Context Engineering driver (reduced scope).

Reproduces a slim version of Chroma's context-rot experiments
(https://research.trychroma.com/context-rot) against Claude Sonnet 4.6:

  A. Repeated Words Faithfulness
     - 3 word counts × 5 positions = 15 prompts
     - Per-row: Levenshtein vs gold, modified-word-present, position-accuracy

  B. Needle-in-a-Haystack (NIAH) with semantic distractors
     - 3 input lengths × 3 needle depths = 9 prompts
     - Judged by Haiku 4.5 LLM-as-judge

Output: text-mode tables. No matplotlib, no pandas. Estimated cost ≤ $2.

Usage:
    source .venv/bin/activate && source ~/.basecamp_anthropic_key
    python context_rot.py            # both experiments
    python context_rot.py rw         # repeated words only
    python context_rot.py niah       # NIAH only
"""
from __future__ import annotations

import concurrent.futures
import os, random, sys, time
from dataclasses import dataclass

import anthropic
import Levenshtein

MODEL = "claude-sonnet-4-6"
JUDGE_MODEL = "claude-haiku-4-5"

client = anthropic.Anthropic(timeout=300.0)


def call_model(prompt: str, model: str = MODEL, max_tokens: int = 1000,
               system: str | None = None, thinking: dict | None = None) -> str:
    kwargs = {"model": model, "max_tokens": max_tokens,
              "messages": [{"role": "user", "content": prompt}]}
    if system:
        kwargs["system"] = system
    if thinking:
        kwargs["thinking"] = thinking
        # When extended thinking is on, max_tokens must include thinking budget.
        kwargs["max_tokens"] = max(max_tokens, thinking.get("budget_tokens", 0) + max_tokens)
    resp = client.messages.create(**kwargs)
    for b in resp.content:
        if b.type == "text":
            return b.text
    return ""


def call_batch(prompts: list[dict], model: str = MODEL, max_concurrent: int = 8,
               thinking: dict | None = None) -> list[str]:
    results: list[str | None] = [None] * len(prompts)

    def _call(idx, item):
        out = call_model(item["prompt"], model=model,
                         max_tokens=item.get("max_tokens", 1000),
                         thinking=thinking)
        return idx, out

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_concurrent) as ex:
        futs = [ex.submit(_call, i, item) for i, item in enumerate(prompts)]
        done = 0
        for f in concurrent.futures.as_completed(futs):
            idx, text = f.result()
            results[idx] = text
            done += 1
            print(f"    [{done}/{len(prompts)}] complete", end="\r", flush=True)
    print()
    return [r or "" for r in results]


def count_tokens(text: str, model: str = MODEL) -> int:
    return client.messages.count_tokens(
        model=model, messages=[{"role": "user", "content": text}]
    ).input_tokens


# ============================================================================
# A · Repeated Words Faithfulness
# ============================================================================

COMMON_WORD = "apple"
MODIFIED_WORD = "apples"
RW_WORD_COUNTS = [25, 100, 500]
RW_POSITIONS_PER = 5


def generate_rw_prompts() -> list[dict]:
    rows = []
    for num_words in RW_WORD_COUNTS:
        # 5 positions evenly spaced through the sequence
        if num_words <= RW_POSITIONS_PER:
            indices = list(range(num_words))
        else:
            step = max(1, num_words // RW_POSITIONS_PER)
            indices = list(range(0, num_words, step))[:RW_POSITIONS_PER]
        for idx in indices:
            gold = " ".join(MODIFIED_WORD if j == idx else COMMON_WORD
                             for j in range(num_words))
            prompt = f"Simply replicate the following text, output the exact same text: {gold}"
            rows.append({
                "id": f"{num_words}_{idx}",
                "prompt": prompt,
                "gold": gold,
                "num_words": num_words,
                "index": idx,
                "max_tokens": min(num_words * 4, 8000),
            })
    return rows


def normalized_levenshtein(gold: str, pred: str) -> float:
    if not gold or not pred:
        return 0.0
    d = Levenshtein.distance(gold, pred)
    return 1 - d / max(len(gold), len(pred))


def modified_word_present(row: dict, output: str) -> bool:
    if not output:
        return False
    marker = (" " + MODIFIED_WORD) if row["index"] == row["num_words"] - 1 \
        else (MODIFIED_WORD + " ")
    return marker in output


def correct_position(row: dict, output: str) -> bool:
    if not output or not modified_word_present(row, output):
        return False
    marker = (" " + MODIFIED_WORD) if row["index"] == row["num_words"] - 1 \
        else (MODIFIED_WORD + " ")
    try:
        return row["gold"].index(marker) == output.index(marker)
    except ValueError:
        return False


def _score_rw_pass(prompts, outputs):
    per_count: dict[int, list[dict]] = {}
    for row, out in zip(prompts, outputs):
        scored = {
            "id": row["id"], "num_words": row["num_words"], "index": row["index"],
            "lev": normalized_levenshtein(row["gold"], out),
            "present": modified_word_present(row, out),
            "position_ok": correct_position(row, out),
        }
        per_count.setdefault(row["num_words"], []).append(scored)
    return per_count


def _summarize_rw(per_count: dict, label: str) -> dict:
    print(f"\n  {label}")
    print(f"  {'word_count':>10} {'n':>3} {'Lev':>6} {'mod_present':>12} {'position_ok':>12}")
    print(f"  {'-'*10} {'-'*3} {'-'*6} {'-'*12} {'-'*12}")
    totals = {"lev": 0.0, "present": 0, "position_ok": 0, "n": 0}
    summary_by_count = {}
    for n in sorted(per_count):
        rs = per_count[n]
        lev = sum(r["lev"] for r in rs) / len(rs)
        pres = sum(r["present"] for r in rs) / len(rs)
        pos = sum(r["position_ok"] for r in rs) / len(rs)
        summary_by_count[n] = {"lev": lev, "present": pres, "position_ok": pos, "n": len(rs)}
        print(f"  {n:>10} {len(rs):>3} {lev:>6.3f} {pres*100:>11.0f}% {pos*100:>11.0f}%")
        totals["lev"] += sum(r["lev"] for r in rs)
        totals["present"] += sum(r["present"] for r in rs)
        totals["position_ok"] += sum(r["position_ok"] for r in rs)
        totals["n"] += len(rs)
    overall_lev = totals["lev"] / totals["n"]
    overall_pres = totals["present"] / totals["n"]
    overall_pos = totals["position_ok"] / totals["n"]
    print(f"\n  Overall ({totals['n']} prompts):  Lev={overall_lev:.4f}  "
          f"mod_present={overall_pres*100:.1f}%  position_ok={overall_pos*100:.1f}%")
    return {"by_count": summary_by_count, "lev": overall_lev,
            "present": overall_pres, "position_ok": overall_pos, "n": totals["n"]}


def run_repeated_words(with_thinking: bool = False, compare_thinking: bool = False) -> None:
    print()
    print("=" * 72)
    print("  A · Repeated-Words Faithfulness — Claude Sonnet 4.6")
    print("=" * 72)
    prompts = generate_rw_prompts()
    print(f"\n  {len(prompts)} prompts across word counts {RW_WORD_COUNTS}.")

    if compare_thinking:
        # Run both: baseline + extended-thinking, then diff the per-count
        # metrics so the rot/anti-rot effect is visible side-by-side.
        print("\n  --- baseline (no thinking) ---")
        out_base = call_batch(prompts, model=MODEL, max_concurrent=5)
        per_base = _score_rw_pass(prompts, out_base)
        s_base = _summarize_rw(per_base, "Baseline:")

        print("\n  --- with extended thinking (budget_tokens=2048) ---")
        out_think = call_batch(prompts, model=MODEL, max_concurrent=5,
                                thinking={"type": "enabled", "budget_tokens": 2048})
        per_think = _score_rw_pass(prompts, out_think)
        s_think = _summarize_rw(per_think, "With extended thinking:")

        print(f"\n  Δ thinking vs baseline")
        print(f"  {'word_count':>10}  {'Δ Lev':>7}  {'Δ mod_present':>14}  {'Δ position_ok':>14}")
        print(f"  {'-'*10}  {'-'*7}  {'-'*14}  {'-'*14}")
        for n in sorted(per_base):
            b = s_base["by_count"][n]
            t = s_think["by_count"][n]
            print(f"  {n:>10}  {(t['lev']-b['lev'])*100:>+6.2f}p  "
                  f"{(t['present']-b['present'])*100:>+13.1f}p  "
                  f"{(t['position_ok']-b['position_ok'])*100:>+13.1f}p")
        return

    thinking = {"type": "enabled", "budget_tokens": 2048} if with_thinking else None
    label_suffix = " (with extended thinking)" if with_thinking else ""
    outputs = call_batch(prompts, model=MODEL, max_concurrent=5, thinking=thinking)
    per_count = _score_rw_pass(prompts, outputs)
    _summarize_rw(per_count, f"Results{label_suffix}:")


# ============================================================================
# B · Needle-in-a-Haystack with semantic distractors
# ============================================================================

NIAH_LENGTHS = [1000, 8000, 20000]
NIAH_DEPTHS = [0, 50, 100]  # percent

# Source paragraphs to weave a synthetic haystack
TOPIC_PARAGRAPHS = [
    "The history of computing is filled with unexpected turns. Early pioneers like Ada Lovelace imagined machines that could compose music and process symbols, long before transistors existed. The gap between vision and implementation has always been central to progress in technology.",
    "Scientific methodology evolved gradually over centuries. From the empirical observations of Aristotle to the controlled experiments of the Enlightenment, each era contributed tools for understanding the natural world. The key insight was that nature could be interrogated systematically.",
    "Philosophy has long debated the nature of knowledge itself. Epistemologists distinguish between knowing that something is true and knowing how to do something. This distinction matters in artificial intelligence, where declarative and procedural knowledge serve different purposes.",
    "Urban planning in the twentieth century underwent dramatic shifts. The modernist vision of separated zones for living, working, and recreation gave way to mixed-use developments that emphasized walkability and community interaction over automotive convenience.",
    "Ecological systems demonstrate remarkable resilience through redundancy and diversity. When one species declines, others often fill its niche, maintaining ecosystem function. This principle has inspired approaches to designing robust engineering systems and organizations.",
    "The development of writing systems transformed human civilization. From cuneiform to alphabets, each innovation in recording language expanded the scope of collective memory and enabled new forms of social organization, law, and commerce across distances and generations.",
    "Mathematical proof provides a unique form of certainty unavailable in empirical sciences. Once proven, a theorem holds universally and permanently. Yet the process of discovering proofs often involves intuition, guessing, and aesthetic judgment before rigorous formalization.",
    "The psychology of decision-making reveals systematic biases that affect even experts. Anchoring, framing effects, and the availability heuristic show that human reasoning departs systematically from idealized rationality, with implications for both individual choice and institutional design.",
    "Modern materials science emerged from the convergence of physics, chemistry, and engineering. Understanding the relationship between atomic structure and macroscopic properties allowed scientists to design materials with specific behaviors, from superconductors to biocompatible polymers.",
    "Languages display patterns that reveal deep structures of human cognition. The shared features across unrelated tongues — recursion, grammatical relationships, semantic categories — suggest universal cognitive constraints, while their diversity reflects historical contingencies and cultural adaptation.",
]

# A "needle" plus three semantic distractors that share keywords with the needle.
NEEDLE = "The best place to eat the freshest mango in Singapore is Punggol Eatery Hub on the third floor."
DISTRACTORS = [
    "Singapore's best durian stall is found near Geylang, especially on weekends.",
    "Punggol Waterway Park is known for jogging trails, not for food specifically.",
    "Mango lassi shops cluster in Little India, particularly around Tekka Centre.",
]
NIAH_QUESTION = "Where is the best place to eat the freshest mango in Singapore?"
NIAH_GOLD = "Punggol Eatery Hub (on the third floor)"


def build_haystack(target_tokens: int) -> str:
    """Build a haystack ~target_tokens long from TOPIC_PARAGRAPHS, padded to target."""
    rng = random.Random(target_tokens)
    pool = list(TOPIC_PARAGRAPHS)
    rng.shuffle(pool)
    text_parts: list[str] = []
    running_tokens = 0
    i = 0
    while running_tokens < target_tokens:
        para = pool[i % len(pool)]
        text_parts.append(para)
        # ~rough token estimate: 1 token ≈ 4 chars in English
        running_tokens += len(para) // 4
        i += 1
    return "\n\n".join(text_parts)


def insert_at_depth(haystack: str, item: str, depth_percent: int) -> str:
    if depth_percent <= 0:
        return item + "\n\n" + haystack
    if depth_percent >= 100:
        return haystack + "\n\n" + item
    paras = haystack.split("\n\n")
    insert_idx = int(len(paras) * depth_percent / 100)
    paras.insert(insert_idx, item)
    return "\n\n".join(paras)


def insert_needle_and_distractors(haystack: str, depth_percent: int) -> str:
    # Put distractors at fixed positions (10%, 35%, 75%) and the real needle at depth_percent.
    rng = random.Random(depth_percent)
    distractor_positions = [10, 35, 75]
    shuffled = list(DISTRACTORS)
    rng.shuffle(shuffled)
    for d, pos in zip(shuffled, distractor_positions):
        if pos == depth_percent:
            pos = (pos + 5) % 100
        haystack = insert_at_depth(haystack, d, pos)
    return insert_at_depth(haystack, NEEDLE, depth_percent)


def make_niah_prompt(haystack_with_needle: str, question: str) -> str:
    return (
        "Read the following document carefully and answer the question at the end. "
        "Quote the relevant sentence verbatim, then give a one-sentence answer.\n\n"
        "===== DOCUMENT START =====\n"
        f"{haystack_with_needle}\n"
        "===== DOCUMENT END =====\n\n"
        f"Question: {question}"
    )


def llm_judge(question: str, gold: str, output: str) -> tuple[bool, str]:
    prompt = (
        "You are an eval grader. Given a question, the CORRECT answer, and a response, "
        "decide if the response factually contains the correct answer.\n\n"
        f"Question: {question}\n\nCORRECT answer: {gold}\n\nResponse to judge:\n{output}\n\n"
        "Respond on the first line with PASS or FAIL, then on the next line a one-sentence reason."
    )
    text = call_model(prompt, model=JUDGE_MODEL, max_tokens=150).strip()
    first = text.split("\n", 1)[0].upper()
    reason = text.split("\n", 1)[1].strip() if "\n" in text else text
    return ("PASS" in first), reason


def run_niah() -> None:
    print()
    print("=" * 72)
    print("  B · Needle-in-a-Haystack with semantic distractors — Claude Sonnet 4.6")
    print("=" * 72)
    print(f"\n  Needle:       {NEEDLE}")
    print(f"  Question:     {NIAH_QUESTION}")
    print(f"  Gold:         {NIAH_GOLD}")
    print(f"  Distractors:  {len(DISTRACTORS)} semantic-keyword distractors at 10/35/75% depth")
    print()

    prompts: list[dict] = []
    meta: list[tuple[int, int]] = []
    for length in NIAH_LENGTHS:
        base = build_haystack(length)
        for depth in NIAH_DEPTHS:
            hay = insert_needle_and_distractors(base, depth)
            prompts.append({"prompt": make_niah_prompt(hay, NIAH_QUESTION), "max_tokens": 300})
            meta.append((length, depth))

    print(f"  {len(prompts)} prompts across {len(NIAH_LENGTHS)} lengths × {len(NIAH_DEPTHS)} depths.")
    outputs = call_batch(prompts, model=MODEL, max_concurrent=4)

    # Score with judge
    print("\n  Judging outputs with Haiku 4.5…")
    results = []
    for (length, depth), out in zip(meta, outputs):
        passed, reason = llm_judge(NIAH_QUESTION, NIAH_GOLD, out)
        results.append({"length": length, "depth": depth, "passed": passed,
                        "reason": reason, "output": out[:120]})

    # Heatmap as a table (rows=depth, cols=length)
    print()
    header = f"  {'depth\\len':>10} " + " ".join(f"{l:>8}" for l in NIAH_LENGTHS)
    print(header)
    print("  " + "-" * (len(header) - 2))
    for d in NIAH_DEPTHS:
        row = [r for r in results if r["depth"] == d]
        cells = []
        for l in NIAH_LENGTHS:
            cell = next((r for r in row if r["length"] == l), None)
            cells.append("  PASS " if (cell and cell["passed"]) else "  fail ")
        print(f"  {d:>9}% " + " ".join(f"{c:>8}" for c in cells))

    passed_count = sum(r["passed"] for r in results)
    print(f"\n  Overall: {passed_count}/{len(results)} passed ({passed_count/len(results)*100:.0f}%)")

    # Show which cells failed + why
    failures = [r for r in results if not r["passed"]]
    if failures:
        print(f"\n  Failures ({len(failures)}):")
        for f in failures:
            print(f"    length={f['length']:>5} depth={f['depth']:>3}%  judge: {f['reason'][:100]}")
            print(f"      output: {f['output']}")


# ============================================================================
# Driver
# ============================================================================

def main() -> None:
    stage = (sys.argv[1].lower() if len(sys.argv) > 1 else "all")
    if stage == "rw":
        run_repeated_words()
    elif stage == "rw-thinking":
        run_repeated_words(with_thinking=True)
    elif stage == "rw-compare":
        # Run BOTH baseline and extended-thinking, side-by-side delta table.
        run_repeated_words(compare_thinking=True)
    elif stage == "niah":
        run_niah()
    elif stage == "all":
        run_repeated_words()
        run_niah()


if __name__ == "__main__":
    main()
