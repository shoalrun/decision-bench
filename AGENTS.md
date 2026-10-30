# Decision Bench agent instructions

Decision Bench is a general workbench for comparing decision models against known
answers. Shoalrun is a convenient source of owned, modifiable test data; this is
not a feature of the Shoalrun framework. Support other datasets when useful.

Read [EVALUATIONS.md](EVALUATIONS.md) before adding cases, changing adapters,
running comparisons, or writing reports. It describes the implementation,
commands, evidence requirements, and reporting workflow.

## Evaluation rules

- Runs are deterministic programs, with no agent planning, runtime case
  generation, adaptive prompts, or LLM judges. Agents can build and inspect the
  harness; they do not establish the answers or grade model responses.
- Expected answers must come from executable, replayable evidence. Verify the
  oracle against exactly the inputs and choices sent to models. Scope claims to
  the behavior actually checked, not broader product or provider validity.
- Keep answer keys, oracle results, review notes, histories, and grading feedback
  out of requests. Use the allowlisted request builder and test leakage with
  poisoned metadata and altered inputs.
- Preserve frozen fixtures, source hashes, recorded model versions, run evidence,
  and report provenance. New cases, source revisions, or labels require a new
  fixture version. Keep completed runs intact; never overwrite a result to retry.
- Separate development and held-out cases by meaningful families. Development
  cases check the setup; held-out cases measure performance after prompts and
  settings are fixed. Published or already inspected test results are regression
  evidence, not a fresh blind test for subsequent tuning.
- Changing a dataset, prompt, runtime, adapter, or model can invalidate direct
  comparisons. Record the change and rerun the intended comparison consistently.

## Execution and publication

- Honor the user's existing authorization and request/spend limits; do not ask
  again for work already authorized. Establish missing scope before paid calls.
  Tests, setup, documentation builds, and hosted workflows make no paid calls.
- Never display credentials or place them in commands, fixtures, reports, or Git.
  Read authorized credentials through the environment. Do not copy credentials
  from another checkout into this repository.
- This is a public repository. Inspect the exact publication file list. Keep raw
  responses, local runs, reviewer conversations, personal paths, dependencies,
  caches, and generated sites out of Git. Commit synthetic cases, reproducible
  source, attribution, and deliberately sanitized results only.
- Preserve unrelated edits. Do not modify the Shoalrun framework or its separate
  docs/deployment merely to run this workbench.
- Follow user authorization for commits, pushes, Pages settings, and deployment.
  Preparing a report does not itself publish it. Keep the Pages workflow
  manual-only; do not add automatic model calls or hosted jobs.

## Required checks

After relevant changes, run the offline tests and executable fixture validation:

```sh
python3 -m unittest discover -s tests -v
python3 -m decision_bench validate --dataset data/shoalrun-known-v1.json
```

Verify any new fixture with its own replay command as well. Validate changed docs
and inspect HTML and Markdown. Declare the entire connected set when previewing
linked draft pages. Use the pinned public Shoalrun package and lockfile, not a
framework checkout dependency. State clearly which providers were tested live.

## Report writing

Write each report as a standalone evaluation, without drafting history or agent
conversations. Keep its setup specific to the run and link to the shared
methodology. Define unfamiliar terms on first use, list the exact questions or
link the full case catalog, report errors and denominators, and limit conclusions
to the workload measured. An unpublished report may be revised at the user's
request; retain the underlying measurements and distinguish datasets or runs.
