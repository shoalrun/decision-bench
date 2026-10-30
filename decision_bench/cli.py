import argparse
import datetime
import json
import os
from pathlib import Path
import platform
import random
import sys
import subprocess
import time

from .core import baseline, digest, load_json, normalize, request_for, validate_dataset
from .metrics import markdown, summarize
from .transport import call_remote, http_json, preflight


def save(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def verify_oracle(path, data):
    if data.get("schema_version") not in (2, 3, 4):
        return
    script_name = {2: "known_answers.mjs", 3: "expanded_cases.mjs", 4: "presentation_cases.mjs"}[data["schema_version"]]
    script = Path(__file__).resolve().parents[1] / "scripts" / script_name
    result = subprocess.run(["node", str(script), "verify", str(Path(path).resolve())],
                            text=True, capture_output=True, timeout=30)
    if result.returncode:
        # No source or exception bodies from executable fixture data in CLI output.
        raise ValueError("Executable answer verification failed; run node scripts/{} verify DATASET for details".format(script_name))
    print(result.stdout.strip())


def catalog(args):
    raw = load_json(args.from_file) if args.from_file else http_json(
        "https://openrouter.ai/api/v1/models?output_modalities=decisions", allow_query=True)
    models = [m for m in raw["data"] if "decisions" in m.get("architecture", {}).get("output_modalities", [])]
    if args.match:
        models = [m for m in models if args.match.lower() in m["id"].lower()]
    if args.out:
        path = Path(args.out)
        if path.exists():
            raise ValueError("Catalog output already exists")
        save(path, {"fetched_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "models": models})
    if args.config_out:
        path = Path(args.config_out)
        if path.exists():
            raise ValueError("Model config output already exists")
        entries = [{"name": kind, "adapter": kind} for kind in ("uniform", "lexical")]
        entries.extend({"name": m["id"], "adapter": "systemone",
                        "model": m.get("canonical_slug") or m["id"],
                        "url": "https://openrouter.ai/api/alpha/decisions",
                        "key_env": "OPENROUTER_API_KEY", "auth_scheme": "Bearer"}
                       for m in models if not m.get("alias_target"))
        save(path, {"models": entries})
    for m in models:
        print("{}\t{}\t{}".format(m["id"], m.get("canonical_slug", ""), m.get("context_length", "?")))
    print("{} decision models; discovery made no inference requests.".format(len(models)), file=sys.stderr)


def run(args):
    data = validate_dataset(load_json(args.dataset))
    verify_oracle(args.dataset, data)
    config = load_json(args.config)["models"]
    names = args.models.split(",")
    if len(set(names)) != len(names):
        raise ValueError("Duplicate requested model names")
    if len({m["name"] for m in config}) != len(config):
        raise ValueError("Config model names must be unique")
    models = []
    for name in names:
        matches = [m for m in config if m["name"] == name]
        if not matches:
            raise ValueError("Unknown model name: " + name)
        model = matches[0]
        if any(k not in {"name", "adapter", "model", "url", "key_env", "auth_scheme"} for k in model):
            raise ValueError("Unknown model config fields; store credentials in environment variables")
        preflight(model)
        models.append(model)
    cases = [c for c in data["cases"] if c["split"] == args.split]
    if args.limit:
        cases = cases[:args.limit]
    if not cases:
        raise ValueError("No matching cases")
    # Randomized but repeatable interleaving across providers reduces order bias.
    jobs = [(m, c) for c in cases for m in models]
    random.Random(args.seed).shuffle(jobs)
    remote_count = sum(m["adapter"] not in ("uniform", "lexical") for m, c in jobs)
    if remote_count > args.max_requests:
        raise ValueError("Plan needs {} remote calls, exceeding --max-requests {}. Use --limit or raise the cap explicitly.".format(remote_count, args.max_requests))
    code_sources = {p.name: p.read_text() for p in Path(__file__).parent.glob("*.py")}
    code_sources["scripts/known_answers.mjs"] = (Path(__file__).resolve().parents[1] / "scripts" / "known_answers.mjs").read_text()
    if data["schema_version"] in (3, 4):
        code_sources["scripts/expanded_cases.mjs"] = (Path(__file__).resolve().parents[1] / "scripts" / "expanded_cases.mjs").read_text()
    if data["schema_version"] == 4:
        code_sources["scripts/presentation_cases.mjs"] = (Path(__file__).resolve().parents[1] / "scripts" / "presentation_cases.mjs").read_text()
    code_hash = digest(code_sources)
    plan = {"dataset_hash": digest(data), "models": models, "case_ids": [c["id"] for c in cases],
            "dataset_schema": data["schema_version"], "label_origin": data.get("label_origin"),
            "scope": data.get("scope"),
            "split": args.split, "seed": args.seed, "timeout": args.timeout, "code_hash": code_hash,
            "request_hashes": {c["id"]: digest(request_for(data, c)) for c in cases}}
    fingerprint = digest(plan)
    out = Path(args.out)
    if args.resume:
        metadata = load_json(out / "manifest.json")
        if metadata["fingerprint"] != fingerprint:
            raise ValueError("Resume rejected: dataset, models, prompts, code, or run settings changed")
    else:
        out.mkdir(parents=True, exist_ok=False)
        metadata = {**plan, "fingerprint": fingerprint, "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "python": platform.python_version(), "platform": platform.system(),
                    "planned_results": len(jobs), "planned_remote_requests": remote_count}
        save(out / "manifest.json", metadata)
    rows = []
    results = out / "results.jsonl"
    if args.resume and results.exists():
        with results.open() as handle:
            for line in handle:
                rows.append(json.loads(line))
    done = {(r["model_name"], r["case_id"]) for r in rows}
    if len(done) != len(rows):
        raise ValueError("Duplicate result rows")
    expected_jobs = {(m["name"], c["id"]) for m, c in jobs}
    if not done <= expected_jobs:
        raise ValueError("Unexpected results in run")
    print("{} cases × {} models; {} remote requests planned; {} results already recorded.".format(len(cases), len(models), remote_count, len(rows)))
    with results.open("a", encoding="utf-8") as handle:
        for model, case in jobs:
            if (model["name"], case["id"]) in done:
                continue
            request = request_for(data, case)
            row = {"model_name": model["name"], "requested_model": model.get("model", model["adapter"]),
                   "adapter": model["adapter"], "case_id": case["id"], "task": case["task"],
                   "group": case["group"], "expected": case["expected"], "request_hash": digest(request)}
            if data["schema_version"] == 4:
                row.update(presentation=case["presentation"], base_case_id=case["base_case_id"])
            started = time.perf_counter()
            try:
                response = baseline(request, model["adapter"]) if model["adapter"] in ("uniform", "lexical") else call_remote(model, request, args.timeout)
                row.update(normalize(response, request))
                row.update(status="ok", resolved_model=response.get("model"), raw_response=response)
            except Exception as error:
                row.update(status="error", error_type=type(error).__name__)
                # Do not persist arbitrary exception messages or HTTP bodies.
                if isinstance(error, RuntimeError) and str(error).startswith("HTTP "):
                    row["http_status"] = str(error)
            row["latency_ms"] = (time.perf_counter() - started) * 1000
            handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
            rows.append(row)
            print("[{}/{}] {} {} {}".format(len(rows), len(jobs), model["name"], case["id"], row["status"]), flush=True)
    summary = summarize(rows)
    save(out / "summary.json", summary)
    (out / "report.md").write_text(markdown(summary, metadata), encoding="utf-8")
    metadata["completed_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    metadata["recorded_results"] = len(rows)
    save(out / "manifest.json", metadata)
    print("Report: " + str(out / "report.md"))
    return 2 if any(r["status"] == "error" for r in rows) else 0


def positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("Must be positive")
    return number


def main():
    parser = argparse.ArgumentParser(description="Compare decision models against executable known answers")
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="Validate hashes, labels, and topic split isolation")
    validate.add_argument("--dataset", default="data/shoalrun-known-v1.json")
    listing = commands.add_parser("catalog", help="List live OpenRouter decision models; no inference")
    listing.add_argument("--out")
    listing.add_argument("--match", help="Filter catalog IDs by substring")
    listing.add_argument("--config-out", help="Write pinned non-alias entries as a runnable model config")
    listing.add_argument("--from-file", help="Use a saved raw OpenRouter models response instead of fetching")
    running = commands.add_parser("run", help="Run selected models; defaults to offline baselines")
    running.add_argument("--dataset", default="data/shoalrun-known-v1.json")
    running.add_argument("--config", default="models.example.json")
    running.add_argument("--models", default="uniform")
    running.add_argument("--split", choices=("dev", "test"), default="dev")
    running.add_argument("--out", required=True)
    running.add_argument("--limit", type=positive_int)
    running.add_argument("--max-requests", type=positive_int, default=100)
    running.add_argument("--timeout", type=positive_int, default=30)
    running.add_argument("--seed", type=int, default=42)
    running.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "validate":
            data = validate_dataset(load_json(args.dataset))
            verify_oracle(args.dataset, data)
            sources = ("{} base cases with three matched presentations".format(len(data["base_fixture"]["cases"])) if data["schema_version"] == 4 else
                       "{} source modules".format(len(data["references"])) if data["schema_version"] == 3 else
                       "1 source contract" if data["schema_version"] == 2 else "{} passages".format(len(data["passages"])))
            print("Valid: {}, {} cases; sha256 {}".format(sources, len(data["cases"]), digest(data)))
            return
        if args.command == "catalog":
            catalog(args)
            return
        sys.exit(run(args))
    except (ValueError, OSError, KeyError, RuntimeError) as error:
        print("Error: " + str(error), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
