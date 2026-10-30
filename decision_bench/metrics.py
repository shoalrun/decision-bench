"""Scores include failures in accuracy; probability metrics use valid rows only."""
import math
import statistics


def percentile(values, p):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(p * len(ordered)) - 1)]


def score(rows):
    valid = [r for r in rows if r["status"] == "ok"]
    n = len(rows)
    if not n:
        return {"n": 0}
    correct = [r["prediction"] == r["expected"] for r in valid]
    losses, briers = [], []
    for r in valid:
        probs = r["probabilities"]
        losses.append(-math.log(max(1e-15, probs[r["expected"]])))
        # Multiclass Brier: sum over labels, range [0, 2], including binary.
        briers.append(sum((p - int(label == r["expected"])) ** 2 for label, p in probs.items()))
    ece = 0
    for i in range(10):
        bucket = [r for r in valid if min(9, int(r["confidence"] * 10)) == i]
        if bucket:
            acc = statistics.mean(r["prediction"] == r["expected"] for r in bucket)
            ece += len(bucket) / len(valid) * abs(acc - statistics.mean(r["confidence"] for r in bucket))
    thresholds = []
    for threshold in (0, .5, .7, .8, .9, .95):
        accepted = [r for r in valid if r["confidence"] >= threshold]
        thresholds.append({"threshold": threshold, "accepted": len(accepted),
                           "coverage": len(accepted) / n,
                           "accuracy": statistics.mean(r["prediction"] == r["expected"] for r in accepted) if accepted else None})
    timings = [r["latency_ms"] for r in valid]
    return {"n": n, "valid": len(valid), "errors": n - len(valid),
            "accuracy": sum(correct) / n, "valid_accuracy": statistics.mean(correct) if valid else None,
            "brier": statistics.mean(briers) if valid else None,
            "nll": statistics.mean(losses) if valid else None, "ece_10_bins": ece if valid else None,
            "p50_ms": percentile(timings, .5), "p95_ms": percentile(timings, .95),
            "thresholds": thresholds}


def summarize(rows):
    models = sorted(set(r["model_name"] for r in rows))
    summaries = {}
    for model in models:
        selected = [r for r in rows if r["model_name"] == model]
        tasks = {t: score([r for r in selected if r["task"] == t]) for t in sorted(set(r["task"] for r in selected))}
        summaries[model] = {"overall": score(selected), "tasks": tasks,
                            "macro_task_accuracy": statistics.mean(v["accuracy"] for v in tasks.values())}
        views = sorted({r["presentation"] for r in selected if "presentation" in r})
        if views:
            summaries[model]["presentations"] = {view: score([r for r in selected if r.get("presentation") == view]) for view in views}
    paired = []
    for i, a in enumerate(models):
        left = {r["case_id"]: r for r in rows if r["model_name"] == a and r["status"] == "ok"}
        for b in models[i+1:]:
            right = {r["case_id"]: r for r in rows if r["model_name"] == b and r["status"] == "ok"}
            common = sorted(left.keys() & right.keys())
            wins = losses = ties = 0
            for key in common:
                x, y = left[key], right[key]
                ac, bc = x["prediction"] == x["expected"], y["prediction"] == y["expected"]
                wins += ac and not bc
                losses += bc and not ac
                ties += ac == bc
            paired.append({"a": a, "b": b, "common_valid": len(common), "a_wins": wins, "b_wins": losses, "ties": ties})
    return {"models": summaries, "paired": paired}


def fmt(value, percent=False):
    if value is None:
        return "—"
    return "{:.1%}".format(value) if percent else "{:.3f}".format(value)


def markdown(summary, metadata):
    description = (
        "Known answers captured by executing pinned Shoalrun functions. No agent or model judges the responses. "
        "This measures exact local config-function behavior, not full CLI acceptance or successful inference. "
        "Mutation families and helper families are held out, while source rules are shared evidence."
        if metadata.get("dataset_schema") in (2, 3, 4) else
        "Authored exploratory benchmark; not an independent model ranking. Paraphrases and task variants share topics and are correlated. "
        "Tune on dev only; test results are descriptive until expanded with unseen, independently labeled cases.")
    lines = ["# Shoalrun decision benchmark", "", "Split: **{}**. Dataset: `{}`.".format(metadata["split"], metadata["dataset_hash"]),
             "", description,
             "", "Accuracy counts errors as wrong. Brier, NLL, ECE and latency use valid responses only. "
             "Brier is summed across labels (0–2); compare within a task. Lower loss is better. "
             "Confidence means selected-label probability, not the provider's confidence statistic.", "",
             "| Model | N | Errors | Accuracy | Brier | NLL | ECE | p50 ms | p95 ms |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for name, entry in summary["models"].items():
        s = entry["overall"]
        lines.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
            name.replace("|", "\\|"), s["n"], s["errors"], fmt(s["accuracy"], True), fmt(s["brier"]),
            fmt(s["nll"]), fmt(s["ece_10_bins"]), fmt(s["p50_ms"]), fmt(s["p95_ms"])))
    for name, entry in summary["models"].items():
        lines.extend(["", "## " + name, "", "| Task | N | Errors | Accuracy | Brier | NLL |", "|---|---:|---:|---:|---:|---:|"])
        for task, s in entry["tasks"].items():
            lines.append("| {} | {} | {} | {} | {} | {} |".format(task, s["n"], s["errors"], fmt(s["accuracy"], True), fmt(s["brier"]), fmt(s["nll"])))
        if entry.get("presentations"):
            lines.extend(["", "Presentation conditions repeat matched cases; compare these separately rather than treating the combined count as independent cases.", "",
                          "| Presentation | N | Errors | Accuracy | p50 ms |", "|---|---:|---:|---:|---:|"])
            for view, s in entry["presentations"].items():
                lines.append("| {} | {} | {} | {} | {} |".format(view, s["n"], s["errors"], fmt(s["accuracy"], True), fmt(s["p50_ms"])))
        lines.extend(["", "| Probability threshold | Accepted / all cases | Coverage | Accepted accuracy |", "|---|---:|---:|---:|"])
        for t in entry["overall"]["thresholds"]:
            lines.append("| {} | {} / {} | {} | {} |".format(t["threshold"], t["accepted"], entry["overall"]["n"], fmt(t["coverage"], True), fmt(t["accuracy"], True)))
    lines.extend(["", "## Paired outcomes", "", "Only common valid cases are paired. Errors remain visible above.", "",
                  "| A | B | Common valid | A alone correct | B alone correct | Same correctness |", "|---|---|---:|---:|---:|---:|"])
    for p in summary["paired"]:
        lines.append("| {a} | {b} | {common_valid} | {a_wins} | {b_wins} | {ties} |".format(**p))
    lines.extend(["", "Latency is sequential client wall time, including network and response parsing, without retries or warmup exclusion. "
                  "It is not vendor inference latency. Baseline timings are local. Chat probabilities are self-reported; lexical scores are heuristic. "
                  "Costs are not inferred from missing usage or catalog prices. Raw provider usage is retained in results.jsonl.", ""])
    return "\n".join(lines)
