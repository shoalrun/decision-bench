# Running and extending evaluations

Use this guide with `AGENTS.md`. It is a handoff for agents maintaining the
workbench, not instructions for agents to participate in an evaluation.

## Start with the question

Determine whether the user wants to compare new models on a fixed suite, broaden
task coverage, test a prompt/adapter change, or measure repeatability. Reuse
accepted scope and authorization. Record the selected models, fixture, split,
request cap, output directory, and any spend limit before remote execution.

The initial fixture has 96 cases: 48 development and 48 test cases across three retrieval-configuration functions. The expanded fixture has 400 additional cases: 200 development and 200 test cases across five synonym and reader-filter functions. Their test results
have been observed and recorded in the docs source. Adding more copies of its
templates does not establish broad decision-making performance.

## Implementation map

| Location | Responsibility |
|---|---|
| `decision_bench/cli.py` | Selection, request caps, seeded order, manifests, resume, execution |
| `decision_bench/core.py` and `expanded.py` | Dataset checks, oracle/input binding, label-free requests, response normalization |
| `decision_bench/transport.py` | System One and chat-completions HTTP adapters |
| `decision_bench/metrics.py` | Deterministic scores and generated run reports |
| `scripts/known_answers.mjs` and `expanded_cases.mjs` | Capture and replay of executable known answers |
| `scripts/curl_batch.py` | Optional fixed external-curl batch transport |
| `data/` | Immutable fixtures and source provenance |
| `models.example.json` | Generic decision/chat endpoint template; credential environment-variable names only |
| `models/*.example.json` | Optional provider-specific configurations |
| `docs/content/reports/` | Dated evaluation reports |
| `docs/content/reports.mdx` | Reports index |
| `docs/content/methodology.mdx` | Shared method and metric definitions |
| `runs/` | Ignored local manifests, raw responses, and generated reports |

Schema 2 supports three retrieval functions; schema 3 supports five functions from the synonym and reader-filter modules. Both replay paths explicitly allow named exports and task types. They are not generic oracles for arbitrary functions. Extending them may require updating replay, validation, request construction, and tests together.

## Compare new models

Use Python 3.9+ and the Node version recorded in the fixture (currently
v22.23.3). Schema 3 also records and verifies the default Intl locale. Run from this checkout; no Python package installation is required.

```sh
python3 -m unittest discover -s tests -v
python3 -m decision_bench validate
```

Copy `models.example.json` to the ignored `models.local.json` and configure the selected endpoint’s model ID, URL, authorization scheme, and credential environment-variable name. Use the `systemone` adapter for typed decision endpoints or `chat` for compatible JSON-mode chat-completions endpoints. Different wire formats or authentication headers require an adapter. Inspect the provider’s available IDs, limits, and pricing. Prefer pinned releases; if the user wants latest, retain that choice and record the returned ID. Never invent a resolved version.

Load the authorized key through the process environment without printing it. Select entries explicitly. For one configured remote endpoint:

```sh
python3 -m decision_bench run \
  --config models.local.json --models uniform,decision-model \
  --limit 8 --max-requests 8 --out runs/comparison-smoke
```

Use `chat-model` for the chat template entry. For a full split, remove `--limit 8` and set the cap to the number of cases multiplied by the number of selected remote models. The optional provider-specific templates are `models/openrouter.example.json` and `models/baseten.example.json`.

Optional OpenRouter discovery makes no inference calls and is not needed for other endpoints:

```sh
mkdir -p runs/discovery
python3 -m decision_bench catalog \
  --out runs/discovery/catalog.json --config-out runs/discovery/models.json
```

Use a fresh output directory if these files exist; never overwrite catalog evidence. To use generated entries, pass `--config runs/discovery/models.json` and explicitly select their names. The `catalog` command does not discover other providers.

Four remote models across all 48 development cases need a cap of 192 requests.
Use `--config` for a different model config, `--dataset` for a different fixture,
and `--split test` only after settings are fixed. The cap limits requests, not
currency. Do not infer a dollar guarantee from it.

Every model receives the same request for a case, with no conversation history,
repository access, tools, or answer-bearing metadata. The runner verifies all
known answers before inference. Stop and fix malformed request/response handling
before interpreting scores; never silently repair provider distributions.

## Add cases or new tasks

Prefer fresh behavior and additional source functions over volume from nearly
identical templates. Useful targets include boundary values, interactions between
rules, valid unusual inputs, conflicting overrides, missing versus null fields,
and cases where plausible domain knowledge differs from the exact code.

For an expansion, aim for roughly 200–400 fresh cases across four to six additional
functions when suitable executable sources exist. This is a coverage goal, not a
claim that generated cases are independent observations. Other projects are
welcome sources; preserve licenses and attribution.

1. Choose an actual executable source and a precise question about its behavior.
   A local validator accepting an object does not establish that inference works.
2. Freeze the source revision, source bytes/hashes, runtime, input serialization,
   concrete arguments/options, outputs or exceptions, and label derivation.
3. Execute every candidate choice and assert one correct answer where the task
   requires one. Distinguish an expected validator exception from a failed API call.
4. Bind recorded oracle calls to model-visible inputs/options. Replay those calls
   and derive labels again; a self-consistent fabricated result log is insufficient.
5. Keep related mutations, source examples, and close variants in the same split.
   Check for duplicate calls across splits. Reserve fresh test families before
   tuning; a test inspected during tuning is no longer a fresh held-out test.
6. Write a new versioned fixture. Do not overwrite or relabel a completed fixture.
7. Add tests for replay, input tampering, forged results, ambiguous choices, and
   gold-metadata leakage. Test the real input boundary rather than echoing the
   implementation's expected result in a mock.

The current capture tool can capture its supported functions from a clean source
checkout, and refuses to overwrite a fixture:

```sh
node scripts/known_answers.mjs capture /path/to/shoalrun data/shoalrun-known-v2.json
node scripts/known_answers.mjs verify data/shoalrun-known-v2.json
python3 -m decision_bench validate --dataset data/shoalrun-known-v2.json
```

Adding other exports or modules needs explicit replay support. Avoid stale
compiled artifacts and hidden dependencies. New runtime semantics require a new
fixture and a documented way to replay both existing and new versions.

## Run and inspect results

Use a new output directory for every run. `--resume` requires matching fixtures,
models, prompts, code, seed, and timeout. It keeps completed errors; retry them in
a separate run. Do not overwrite, delete, or restamp measurements.

Inspect `manifest.json`, `results.jsonl`, `summary.json`, and `report.md`. Confirm
completion, planned/recorded counts, dataset identity, returned model IDs, valid
response counts, and errors before comparing accuracy. Examine misses against the
executable oracle. Preserve evidence that challenges a favorable conclusion.

Accuracy includes failed responses as wrong; probability metrics use valid rows.
Brier uses a summed multiclass definition. Threshold confidence is selected-label
probability, not the provider's separate confidence statistic. Compare paired
outcomes on common valid cases while keeping error rates visible. Avoid declaring
significance from correlated generated cases or small differences.

For repeatability, run a fixed subset two or three times with recorded request
orders and fixed model versions. Report changing answers and latency variation;
repeats are not new independent cases. Latency includes client/network overhead.
Costs reported by providers are usage figures, not a reconciled invoice.

If Python networking is restricted, diagnose it without exposing credentials. The
optional curl batch requires explicit `--models` for preparation and supports typed decision endpoints plus offline baselines (not the chat adapter). It prepares fixed requests, collects transport responses, and
uses the same normalizer/scorers; it is not an agent-driven evaluation. Keep its
transient credential config private and remove it after execution or cancellation.
If neither transport works, report that no valid inference comparison was made.
Do not convert connection failures into claims about model quality.

## Write a report and update docs

Use `docs/content/add-report.mdx` as the report-authoring guide. Each dated report
needs its purpose, run-specific setup, model versions, dataset identity, result
counts, question templates/catalog, errors, limitations, and next steps. Link to
the shared methodology instead of repeating it. Describe any deviations explicitly.

Keep source data immutable. If the user revises an unpublished initial report,
include expanded evidence with clear denominators and dataset boundaries. Once
published, add a new dated evaluation for changed models, cases, or methods;
correct factual errors transparently rather than silently replacing results.

Update the Reports index, navigation, exact question catalog, and overview as
appropriate. Keep public prose standalone and factual, without earlier-draft
narratives, private notes, or agent conversations. Shoalrun is a dataset source,
not the workbench's purpose. Define unfamiliar evaluation terms on first use.

Install documentation dependencies from the pinned public registry release:

```sh
npm ci
npm run docs:validate
```

Draft snapshot validation must include the entire linked draft set. Select all
required IDs when nothing changed since a baseline; otherwise draft navigation
can target pages omitted from the capture. Inspect HTML and Markdown, verify the
actual result numbers and all listed questions, and reuse the validated snapshot
for preview. Respect requests to stop the server.

## Publication handoff

Review the exact Git file list and artifact contents, not just `.gitignore`.
Exclude `.env` files, credentials, personal paths, raw response dumps, reviewer
notes, dependencies, caches, and generated output. Public case catalogs may include
synthetic arguments and expected answers; public reports should contain deliberate
summaries or sanitized evidence. Preserve required source licenses/notices.

After content review, mark approved docs public and run `npm run docs:build`.
Verify `docs/site-dist/decision-bench` contains the complete public site and direct
Markdown files. GitHub Pages serves this subtree at `/decision-bench`; it does not
provide Markdown content negotiation at an HTML URL.

The Pages workflow is manual-only and runs no evaluations. Commits, pushes, Pages
configuration, and workflow dispatch follow the user's authorization for the
current action. Do not repeatedly seek permission already granted. Report the
actual commit/deployment status and URL, including unresolved checks.

## Expanded comparison

`data/shoalrun-expanded-v1.json` contains 400 additional cases: 200 development and 200 test cases across five functions. `models/expanded-2026-10-30.json` pins the model versions used in the configuration comparison. Four remote models across both splits require 1,600 calls in total; offline uniform results add no API calls.

Verify with `node scripts/expanded_cases.mjs verify data/shoalrun-expanded-v1.json` and `python3 -m decision_bench validate --dataset data/shoalrun-expanded-v1.json`. Run each split with the pinned config, the same model entry names, `--max-requests 800`, and fresh output directories. Obtain results from completed runs before writing the report; never infer model performance from the offline baseline.

## Analyze task and complexity buckets

The expanded suite has a separate registration in `analysis/expanded-buckets-v1.json`, assigned from source rules and input families before expanded model outputs were inspected. It contains 80 low-, 200 medium-, and 120 high-complexity cases, plus task, function, family, and overlapping edge-condition tags. These are authored structural estimates, not executable gold labels or measured model difficulty.

Keep tags outside the fixture and request builder. Do not alter prompts or inputs during a run. Register classifications before reading model scores, and never define hard cases as the ones a particular model missed. Material reclassification needs a new version with its basis made explicit.

After both runs complete:

```sh
python3 scripts/bucket_results.py \
  --runs runs/expanded-dev runs/expanded-test \
  --out runs/expanded-analysis
```

The analyzer checks fixture/request identity and completion, then reports each split separately by complexity, task, function, full family, and edge condition. Compare accuracy, measured latency, reported cost coverage, and confidence thresholds. Binary and four-choice tasks have different guessing baselines; the report includes a chance-adjusted score. Edge buckets overlap. Source length, function, and complexity are correlated, so a bucket difference alone does not establish a causal effect of complexity or generalize to other datasets.

## Compare reference presentations

`data/shoalrun-presentations-v1.json` pairs every expanded base case with three references: full source module, focused JavaScript, and a reviewed written rule contract. It contains 1,200 presentations of 400 unique cases, with 600 presentation cases per split. Inputs, questions, choices, expected answers, and split remain fixed. The request builder changes only the reference body.

Capture uses Acorn from the pinned documentation dependency tree to select functions and their helpers. Runtime verification needs only the recorded Node version: it replays the base source and checks focused/full equality on every input. Written rules are authored and reviewed against source, not proved equivalent by executing prose. Keep that distinction explicit.

```sh
node scripts/presentation_cases.mjs verify data/shoalrun-presentations-v1.json
python3 -m decision_bench validate --dataset data/shoalrun-presentations-v1.json
```

A full four-model comparison across both splits requires 4,800 calls, with a cap of 2,400 per split. Reuse the pinned model configuration and new output directories. Jobs interleave the three presentations in reproducible order. Analyze per-view accuracy and paired changes on the same base cases, not a combined count treated as independent observations.

This is a practical presentation/context study on observed cases, not fresh blind generalization. Focused code removes TypeScript annotations as well as unrelated declarations. Written rules differ in length and explicitness, so a measured improvement cannot be attributed solely to natural language or shorter context. Record provider token usage, returned model versions, and timing when interpreting results.

Analyze completed presentation runs without inference:

```sh
python3 scripts/presentation_results.py \
  --runs runs/presentations-dev runs/presentations-test \
  --out runs/presentation-analysis
```

Use a new analysis directory if that name already exists. The analyzer checks completion, exact model/case coverage, request hashes, and matched base-case identity. It reports per-format, task, structural complexity, family, cost/token coverage, and paired improvements/regressions. Public aggregates live in `results/2026-10-30/`; raw rows remain ignored. The schema3 source-only and schema4 full-source conditions use the same source text but different instructions and passage IDs; they are not identical-prompt repeatability runs. Compare formats within schema4. Binary decisions use a fixed 0.5 cutoff; poor decisions do not prove the underlying probabilities contain no useful signal. Any calibration or routing policy selected from these results needs fresh validation.


## Liquid addition to the 30 October report

At the user’s explicit request, Liquid d1.1 was added to the existing published report using the same frozen workloads. The original four models’ aggregate files remain unchanged. `models/liquid-2026-10-30.json` pins the added release; `results/2026-10-30/liquid.json` stores its separate model-scoped evidence and transport metadata. The addition covers 96 retrieval cases, 400 search/reader cases, and 1,200 presentations: 1,696 remote calls, excluding 22 preliminary calls.

The report uses Liquid’s complete Python rerun from `runs/liquid-python-{retrieval,expanded,presentations}-{dev,test}`. It matches the original four models’ Python 3.9.6/Darwin runtime, seed42, timeout30, and HTTP transport source. The prior Liquid curl evidence is preserved as `results/2026-10-30/liquid-curl.json`, excluded from current report tables/counts/costs. Never select the better response per case or pool the two runs. The current comparison has 8,480 calls/$0.410591359; archived curl adds 1,696 calls/$0.03820394, with preliminary checks excluded from both. Different execution times and provider/network load still affect latency; do not infer that the client caused differences between the two Liquid runs.

The external-curl manifest includes selected `case_ids` and `request_hashes`, so the presentation analyzer can verify complete model/case identity. Use a new output directory per dataset/split and analyze the new model’s dev/test runs separately from old runs before combining public report tables.
