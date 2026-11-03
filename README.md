# Decision Bench

A workbench for comparing decision models against known answers.

**The included evaluations test source-code behavior, not documentation search or question answering.** Models predict what frozen Shoalrun functions accept, select, or return for concrete inputs. They receive the code or written rules directly, with no repository or documentation-site access. Executing the frozen code establishes the known answers.

No agents participate in a run. There is no planner, generated prompt, adaptive case selection, model judge, or grading feedback. The program loads a frozen fixture, verifies its expected answers by executing pinned source, sends fixed label-free requests, and compares returned decisions with the answer key.

This is a separate repository. It does not modify Shoalrun or require its checkout for evaluation.

## Why Shoalrun

I wanted a decision-model benchmarking workbench that I could own, experiment with, and change as needed. This repo lives in the Shoalrun organization because I maintain Shoalrun and can use its code to create datasets with known answers. Shoalrun supplies the test data; the workbench is intended to support datasets from other projects too.

## Quick start

Use Python 3.9+ and Node **v22.23.3**, the runtime recorded for the current oracle fixture. No third-party runtime packages are required. Run from this checkout:

```sh
python3 -m decision_bench validate
python3 -m unittest discover -s tests -v
python3 -m decision_bench run --out runs/uniform
```

Default validation replays all 96 retrieval answers; selecting another fixture replays its answers before inference. A mismatched source hash, visible input, recorded oracle result, label, or Node version fails verification. An offline uniform baseline is selected by default; it makes no model calls.

Each run writes `manifest.json`, `results.jsonl`, `summary.json`, and `report.md`. Run directories and credentials stay out of Git. Existing output directories cannot be overwritten.

## What has a known answer?

The default fixture, `data/shoalrun-known-v1.json`, contains:

| Task | Cases | How the answer is established |
|---|---:|---|
| Config acceptance | 48 | Execute `validateRetrieval(input)` and record return versus throw. |
| Choose the accepted config | 24 | Execute all four options; assert exactly one returns without throwing. |
| Select provider/model | 24 | Execute `embeddingProviderConfig` or `queryProviderConfig`; compare the returned provider/model pair with the options. |

An additional **400 search and reader-filter cases** exercise synonym validation, phrase matching, query decomposition, reader selection, and reader defaults. The presentation fixture repeats those same 400 cases with full source, focused code, and written rules. Together the datasets contain **496 unique cases**; repeated formats are not new independent questions.

Every case retains the exact function inputs and execution result or exception. The entire source implementation is frozen with its repository revision and SHA-256 hash. The verifier derives calls from the **model-visible inputs/options**, checks that they match the captured calls, re-executes them, and re-derives the expected label. A self-consistent but fabricated result log is not sufficient.

The questions ask about these specific local functions. They do **not** ask whether the whole project builds or inference will succeed. For example, `validateRetrieval` checks a positive safe-integer dimension, but does not enforce every provider's allowed dimensions or verify that a model exists. Provider-selection tasks check the returned provider/model pair, not every optional field.

## Keeping answers out of model requests

The runner builds outgoing requests from an explicit allowlist:

- The fixed question.
- The source implementation, intentionally supplied as an open-book rule contract.
- The config/function arguments and, for choice tasks, candidate answers.

Expected labels, recorded execution results/errors, rationales, review notes, split names, case IDs, and prior grading feedback are excluded. Each remote invocation is a fresh request. The harness gives evaluated endpoints no repository access or tool access.

Tests poison gold/review fields with sentinel values and check that outgoing requests remain unchanged. Exact-match grading occurs locally after the provider responds. An ordinary chat model can be an evaluated baseline; it never judges another model's answer.

Reading the supplied source is intended, just as reading supplied documentation would be. This does not rule out public-source pretraining or provider-side behavior. The deterministic guarantee covers case selection, request construction, answer verification, and scoring—not a provider's responses or network timing.

## Configure an endpoint

The runner supports configurable HTTP endpoints. Copy `models.example.json` to the ignored `models.local.json`, then replace the placeholder model IDs, URLs, authorization schemes, and credential environment-variable names for the entries you want to use. Credentials stay in your shell environment; the CLI does not load `.env` files or search other repositories for keys.

A typed decision endpoint entry has these fields inside the config’s `models` array:

```json
{
  "name": "decision-model",
  "adapter": "systemone",
  "model": "YOUR_DECISION_MODEL_ID",
  "url": "https://your-provider.example/v1/systemone",
  "key_env": "DECISION_API_KEY",
  "auth_scheme": "Bearer"
}
```

The `systemone` adapter sends `{model, state, questions}` and expects `{answers, model?, usage?}`. It uses `Authorization: <auth_scheme> <key>`, with `Bearer` as the default scheme. Different request envelopes, authentication headers, or response shapes need an adapter. Anonymous endpoints can omit `key_env`. HTTPS is required except for loopback HTTP endpoints. Redirects are refused.

For a conventional LLM baseline, select the template’s `chat-model` entry with `"adapter": "chat"`. Its endpoint must support chat-completions requests, JSON mode, temperature 0, and a 1,024-token output cap. It receives the same label-free decision request and returns probabilities in JSON. These are self-reported estimates, not next-token probabilities; assess their calibration separately. The chat model is under test and never judges another model.

After configuring the endpoint and its environment variable:

```sh
python3 -m decision_bench run \
  --config models.local.json --models uniform,decision-model \
  --limit 8 --max-requests 8 --out runs/endpoint-smoke
```

Use `chat-model` instead for the chat endpoint, or explicitly select multiple configured names. Unselected entries are never called. The command above makes at most eight remote calls. The cap limits calls, not dollars; there are no hidden retries or automatic fallbacks. Requests run sequentially with a default 30-second timeout.

## Provider examples and report reproduction

Provider examples are separate from the general template:

| Config | Purpose |
|---|---|
| `models/openrouter.example.json` | Baseten Joey 1, Baseten Mammoth 1, Baseten Elephant 1, and TypeSafe Jev latest via OpenRouter; `OPENROUTER_API_KEY`. |
| `models/baseten.example.json` | Direct Baseten Joey 1; `BASETEN_API_KEY`. |
| `models/expanded-2026-10-30.json` | Fixed OpenRouter releases used in the dated report. |
| `models/liquid-2026-10-30.json` | Fixed LiquidAI d1.1 release for the report’s model addition. |

For the OpenRouter example:

```sh
python3 -m decision_bench run \
  --config models/openrouter.example.json \
  --models uniform,joey-openrouter,mammoth-openrouter,elephant-openrouter,jev-latest \
  --limit 8 --max-requests 32 --out runs/openrouter-smoke
```

This makes at most 32 remote calls. For all 48 default development cases, remove `--limit 8` and use `--max-requests 192`. Add `--dataset` to select another fixture. The 400-case search/reader fixture has 200 cases per split, requiring 800 calls per split with four remote models; its three-format comparison requires 2,400 per split.

The example’s `jev-latest` entry follows OpenRouter’s `~typesafe/jev-latest` alias. Requested IDs and returned model IDs are recorded. Use a fixed release for comparisons over time; a deterministic runner cannot freeze a changing service.

The reported live comparison used the System One adapter with Baseten Joey 1, Baseten Mammoth 1, Baseten Elephant 1, and TypeSafe Jev 1.14 through OpenRouter. Direct Baseten and live chat inference were not part of it.

## Optional OpenRouter model discovery

The `catalog` command is specific to OpenRouter. Other compatible endpoints can be configured manually; no catalog request is required. Discovery makes no inference calls:

```sh
python3 -m decision_bench catalog \
  --out catalog.local.json --config-out models.local.json
```

Filter with `--match baseten/`. Select generated entry names explicitly with `run --config models.local.json --models 'uniform,NAME_1,NAME_2' ...`. Use new output names if they already exist. Discovery never opts models into inference.

If Python network access is restricted, capture that catalog using `curl` and process it offline:

```sh
curl --fail --silent --show-error \
  'https://openrouter.ai/api/v1/models?output_modalities=decisions' \
  -o /tmp/decision-models.json
python3 -m decision_bench catalog --from-file /tmp/decision-models.json \
  --config-out models.local.json
```

Inference requires access to the selected endpoint from the configured transport.

## Scoring and reproducibility

Reports include accuracy with errors counted as wrong, valid-response accuracy, per-task scores, summed multiclass Brier score (0–2), NLL, 10-bin ECE, confidence-threshold coverage/accuracy, paired outcomes, and sequential client-wall-time p50/p95. Probability metrics and latency percentiles use valid responses only; errors remain visible separately. The same responses always produce the same scores.

Selected-label probability is used for confidence thresholds, not provider-specific confidence statistics. Choice distributions must contain exactly the allowed labels, finite values in [0,1], and sum to one within 0.001. The selected label must have maximal probability. Outputs are checked rather than repaired or silently renormalized. NLL uses a probability floor of 1e-15.

`uniform` assigns equal probabilities: 50% per yes/no answer or 25% per four-choice answer. Ties select “yes” or the first option in request order, so its accuracy need not match random guessing. The optional `lexical` baseline uses token overlap and heuristic probabilities; it does not execute validation rules.

Manifests record dataset/request hashes, code hash, selected models, seed, timeout, environment, and planned calls. Request order is shuffled with a fixed seed across models. Successful raw responses, returned model IDs, and provider usage are retained in ignored run output. Costs are not inferred when provider usage is missing. Timings include network/JSON processing and are not provider inference latency.

Repeat the same run command with `--resume` to retain completed rows and continue. The runner rejects changed datasets, prompts, models, code, seed, or timeout. Completed error rows are retained; retry them in a new output directory. A malformed trailing JSONL record requires manual review. Completed runs with errors exit 2; setup failures exit 1.

## Split and scope

There are two sets of 48 cases. Development cases are used to check the setup; held-out test cases are reserved for evaluation after prompts and settings are fixed. Development covers provider values, environment-variable names, unknown fields, and embedding overrides. Test covers dimensions, URL rules, model/query text, and query overrides. Rule families remain in one split, with no exact oracle calls crossing splits. Both splits intentionally receive the same source implementation; this is not an unseen-code or blind evaluation.

Cases share templates and rules, so they are correlated. This is a focused, code-grounded config-decision benchmark, not a general model leaderboard or a test of documentation retrieval. Threshold estimates from small samples are unstable. Tune using dev only, freeze the run settings, then select test explicitly with `--split test`. New labels, sources, or cases require a new fixture version.

## Capture and independently verify answers

The current fixture can be replayed without any Shoalrun checkout:

```sh
node scripts/known_answers.mjs verify data/shoalrun-known-v1.json
```

To capture a new immutable version from a clean Shoalrun source checkout:

```sh
node scripts/known_answers.mjs capture /path/to/shoalrun data/shoalrun-known-v2.json
```

Capture reads the exact TypeScript source and executes it using Node's built-in type stripping. It does not use potentially stale compiled output, write to Shoalrun, call external providers, or ask any agent for labels. It records the exact Node version and refuses to overwrite a fixture. Verification requires that recorded version. Source and fixture changes should be reviewed before adopting a new dataset.

## Licensing and provider references

Harness code and copied Shoalrun source/excerpts are Apache-2.0; see `LICENSE` and `NOTICE`.

- [Baseten Joey 1 API example](https://www.baseten.co/library/joey-1/)
- [OpenRouter model catalog contract](https://github.com/OpenRouterTeam/skills/blob/main/skills/openrouter-decisions/references/models.md)
- [OpenRouter Decisions HTTP contract](https://github.com/OpenRouterTeam/skills/blob/main/skills/openrouter-decisions/scripts/lib.ts)

No upstream harness code was copied. This repository is not affiliated with Baseten, OpenRouter, or TypeSafe.

## Documentation site

Shoalrun documentation source is in `docs/`, configured for
`https://shoalrun.github.io/decision-bench/`. Its CLI is pinned to the public npm
release in `package-lock.json`; no framework checkout or private registry key is
needed. The public repo remote is `https://github.com/shoalrun/decision-bench.git`.

```sh
npm ci
npm run docs:validate
npm run docs:build
npm run docs:serve
```

New pages default to draft; normal builds omit drafts. Use Shoalrun authoring
snapshot validation for local draft previews. After review,
set the approved pages to public, build and inspect the result, and select
**GitHub Actions** as the repository's Pages publishing source. The manual-only
**Publish documentation** workflow uploads the `docs/site-dist/decision-bench`
public subtree. It runs no model evaluations and needs no provider credentials.
Pushes do not trigger publication.

GitHub Pages serves direct `.md` twins and discovery files, but cannot negotiate
Markdown from an HTML URL using the `Accept` header. Local runs, reviewer notes,
credentials, dependencies, and generated sites are ignored by Git.

## Agent handoff

Agents maintaining or extending evaluations should start with [AGENTS.md](AGENTS.md) and [EVALUATIONS.md](EVALUATIONS.md). The handoff covers adding models and executable datasets, authorized run limits, answer isolation, reproducibility, reporting, and publication.
