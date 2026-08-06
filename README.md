# BuildSleuth

**AI proposes, deterministic checks verify.**

BuildSleuth ingests a failing GitHub Actions run, classifies the failure (code change, flaky test, infrastructure, or pipeline config), localizes the root cause at file level, and proposes a fix as a draft PR. Every model output is a validated proposal that must pass deterministic gates before anything is promoted.

The differentiator is the eval harness. Explaining a failed build is a commodity feature now; measuring how often the explanation is right, at what cost, and catching it when it gets worse is not. Published CI-triage benchmarks are scarce, so this project builds and publishes its own.

## Status

Under active development. Classification runs against a non-LLM baseline today; the model layer lands next.

Current scorecard, regex baseline, 6 cases:

| metric | value |
| --- | --- |
| accuracy | 0.667 |
| macro F1 (all 4 classes) | 0.200 |
| subcategory accuracy | 0.000 |
| cost per triage | $0.00 (no model calls) |

The gap between accuracy and macro F1 is the point: the baseline predicts `code_change` for everything, so it looks passable on accuracy while failing both minority classes outright. That is the floor a model has to beat.

Honest caveats, kept current:

- 6 cases so far, target is 80. Numbers on this few cases are directional, not conclusive.
- No case has been human-verified yet, so none are eligible for the pull request gate. Labels were derived from log evidence and each one records why.
- Localization and fix generation are not implemented, so those rows are absent rather than zero.

## How it works

```
run-url -> ingest -> condense -> classify -> localize -> fix -> verify -> draft PR
           offline    24x-122x     model      model      model   sandbox   guarded
           snapshot   reduction
```

Ingestion snapshots a run so triage runs offline and eval cases stay replayable after GitHub expires the logs. Condensation greps error patterns and emits context windows, falling back to the log tail, which reduces real logs from 40KB-240KB down to 2KB-9KB.

## Quick start

```bash
uv sync
uv run buildsleuth fetch https://github.com/OWNER/REPO/actions/runs/RUN_ID
uv run buildsleuth condense snapshots/OWNER-REPO-RUN_ID/log.txt
uv run python -m evals.run_eval
```

Fetching needs a GitHub token in `BUILDSLEUTH_GITHUB_TOKEN` (fine-grained, `Actions: read`).

## Eval design

Scorecards are JSON, committed under `results/`, and pin the four things that produced every number: code revision, model, prompt hash, and dataset hash. A pull request touching agent code, prompts, or the dataset reruns the suite and posts a scorecard diff; the check fails when a metric drifts past tolerance.

Two design choices worth calling out:

- **Misreading a real bug as a flake costs triple.** Cost-weighted error uses an asymmetric penalty because silently hiding a regression is worse than investigating a flake for nothing.
- **Coverage is gated.** A model that crashes on half the dataset would otherwise be scored only on the cases it answered, which raises every other metric. Coverage is compared first, and losing a report entirely counts as a regression.

Taxonomy and metric definitions live in [dataset/README.md](dataset/README.md) with their literature citations.

## License

MIT
