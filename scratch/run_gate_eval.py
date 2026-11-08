import time
import os
import sys
import json
import statistics

sys.path.insert(0, os.path.abspath("."))

from tests.test_amigo_capabilities import TEST_QUERIES
from tests.held_out_eval_set import HELD_OUT_EVAL_DATA, DEV_EVAL_DATA, SEALED_EVAL_DATA
from amigo.core import llm_agent

print("==================================================")
print("   RUNNING STEP 2.3 REGRESSION GATE EVALUATION    ")
print("==================================================")

with open("scratch/baseline_results.json", "r", encoding="utf-8") as f:
    baseline = json.load(f)

# Build baseline pass map: id -> passed
base_orig_map = {item["query"]: item["passed"] for item in baseline["orig_queries"]["items"]}
base_held_map = {item["id"]: item["passed"] for item in baseline["held_out"]["items"]}

# 1. Warmup
_ = llm_agent.get_agent_action("what time is it")

# 2. Original TEST_QUERIES
print("\n[1/3] Evaluating Original TEST_QUERIES (33 items)...")
orig_correct = 0
orig_diffs = []
for q, expected in TEST_QUERIES:
    actions = llm_agent.get_agent_action(q)
    got = actions[0]["tool"] if actions else "none"
    passed = (got == expected)
    if passed:
        orig_correct += 1

    prev_passed = base_orig_map.get(q, False)
    if prev_passed and not passed:
        orig_diffs.append({"type": "REGRESSION", "query": q, "expected": expected, "got": got})
    elif not prev_passed and passed:
        orig_diffs.append({"type": "IMPROVEMENT", "query": q, "expected": expected, "got": got})

orig_acc = (orig_correct / len(TEST_QUERIES)) * 100
print(f"Original TEST_QUERIES Accuracy: {orig_correct}/{len(TEST_QUERIES)} ({orig_acc:.1f}%)")
print(f"Original Per-Item Diffs: {len(orig_diffs)}")
for d in orig_diffs:
    print(f"  {d['type']}: '{d['query']}' -> {d['got']} (expected {d['expected']})")

# 3. Held-Out Dataset (200 items)
print(f"\n[2/3] Evaluating Held-Out Dataset ({len(HELD_OUT_EVAL_DATA)} items)...")
held_correct = 0
dev_correct = 0
sealed_correct = 0
held_diffs = []
cat_stats = {}

for idx, item in enumerate(HELD_OUT_EVAL_DATA):
    q = item["query"]
    exp = item["expected_tool"]
    cat = item["category"]
    hist = item.get("history")
    is_dev = (item in DEV_EVAL_DATA)

    actions = llm_agent.get_agent_action(q, conversation_history=hist)
    got = actions[0]["tool"] if actions else "none"
    passed = (got == exp)

    if passed:
        held_correct += 1
        if is_dev:
            dev_correct += 1
        else:
            sealed_correct += 1

    if cat not in cat_stats:
        cat_stats[cat] = {"correct": 0, "total": 0}
    cat_stats[cat]["total"] += 1
    if passed:
        cat_stats[cat]["correct"] += 1

    prev_passed = base_held_map.get(idx, False)
    if prev_passed and not passed:
        held_diffs.append({"type": "REGRESSION", "id": idx, "is_dev": is_dev, "query": q if is_dev else "[SEALED]", "category": cat, "expected": exp, "got": got})
    elif not prev_passed and passed:
        held_diffs.append({"type": "IMPROVEMENT", "id": idx, "is_dev": is_dev, "query": q if is_dev else "[SEALED]", "category": cat, "expected": exp, "got": got})

held_acc = (held_correct / len(HELD_OUT_EVAL_DATA)) * 100
dev_acc = (dev_correct / len(DEV_EVAL_DATA)) * 100
sealed_acc = (sealed_correct / len(SEALED_EVAL_DATA)) * 100

print(f"Held-Out Overall Accuracy: {held_correct}/{len(HELD_OUT_EVAL_DATA)} ({held_acc:.1f}%)")
print(f"  - DEV Split Accuracy   : {dev_correct}/{len(DEV_EVAL_DATA)} ({dev_acc:.1f}%)")
print(f"  - SEALED Split Accuracy: {sealed_correct}/{len(SEALED_EVAL_DATA)} ({sealed_acc:.1f}%)")
print(f"Held-Out Per-Item Diffs: {len(held_diffs)}")
for d in held_diffs:
    if d["is_dev"]:
        print(f"  {d['type']}: [DEV id={d['id']}, {d['category']}] '{d['query']}' -> {d['got']} (expected {d['expected']})")
    else:
        print(f"  {d['type']}: [SEALED id={d['id']}, {d['category']}] -> {d['got']} (expected {d['expected']})")

# 4. Latency 3-run distribution
print("\n[3/3] Measuring 3-run latency on representative sample...")
latency_sample = [(q, None) for q, _ in TEST_QUERIES[:15]] + [(item["query"], item.get("history")) for item in HELD_OUT_EVAL_DATA[:15]]
multi_run_latencies = []
for q, h in latency_sample:
    for _ in range(3):
        t0 = time.perf_counter()
        _ = llm_agent.get_agent_action(q, conversation_history=h)
        multi_run_latencies.append((time.perf_counter() - t0) * 1000)

multi_run_latencies.sort()
held_p50 = statistics.median(multi_run_latencies)
held_p95 = multi_run_latencies[int(len(multi_run_latencies) * 0.95)]
held_mean = sum(multi_run_latencies) / len(multi_run_latencies)

base_p50 = baseline["latency"]["median_routing_latency_ms"]
base_p95 = baseline["latency"]["p95_routing_latency_ms"]
lat_delta_pct = ((held_p50 - base_p50) / base_p50) * 100

print(f"Routing Latency -> Median: {held_p50:.1f} ms (base: {base_p50:.1f} ms, delta: {lat_delta_pct:+.1f}%) | p95: {held_p95:.1f} ms (base: {base_p95:.1f} ms)")

# Check Regression Gate criteria
regressions = [d for d in (orig_diffs + held_diffs) if d["type"] == "REGRESSION"]
gate_passed = (len(regressions) == 0) and (held_acc >= baseline["held_out"]["accuracy_pct"]) and (lat_delta_pct <= 10.0)

print("\n==================================================")
if gate_passed:
    print(">>> REGRESSION GATE: PASSED (Zero regressions, accuracy preserved, latency within +10%)")
else:
    print(">>> REGRESSION GATE: FAILED")
    print(f"  Regressions: {len(regressions)}")
    print(f"  Accuracy: {held_acc:.1f}% vs baseline {baseline['held_out']['accuracy_pct']:.1f}%")
    print(f"  Latency Delta: {lat_delta_pct:+.1f}%")
print("==================================================")

gate_results = {
    "step": "2.3",
    "gate_passed": gate_passed,
    "orig_queries": {
        "accuracy_pct": round(orig_acc, 2),
        "baseline_accuracy_pct": baseline["orig_queries"]["accuracy_pct"],
        "diffs": orig_diffs
    },
    "held_out": {
        "accuracy_pct": round(held_acc, 2),
        "baseline_accuracy_pct": baseline["held_out"]["accuracy_pct"],
        "dev_accuracy_pct": round(dev_acc, 2),
        "sealed_accuracy_pct": round(sealed_acc, 2),
        "category_stats": cat_stats,
        "diffs": held_diffs,
        "regressions": regressions
    },
    "latency": {
        "median_ms": round(held_p50, 1),
        "baseline_median_ms": base_p50,
        "delta_pct": round(lat_delta_pct, 2),
        "p95_ms": round(held_p95, 1),
        "baseline_p95_ms": base_p95
    }
}

with open("scratch/step2_3_eval_results.json", "w", encoding="utf-8") as f:
    json.dump(gate_results, f, indent=2)

