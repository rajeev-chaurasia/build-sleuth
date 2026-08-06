# BuildSleuth

**AI proposes, deterministic checks verify.**

BuildSleuth ingests a failing GitHub Actions run, classifies the failure (code change, flaky test, infrastructure, or pipeline config), localizes the root cause at file level, and proposes a fix as a draft PR. Every model output is a validated proposal that must pass deterministic gates before anything is promoted.

The differentiator is the eval harness. Explaining a failed build is a commodity feature now; measuring how often the explanation is right, at what cost, and catching it when it gets worse is not. Published CI-triage benchmarks are scarce, so this project builds and publishes its own.

## Status

Classification works end to end against real models. Localization and fix generation are next.

Current scorecard, 6 cases, every model scored through the same code path:

| model | coverage | accuracy | macro F1 | cost weighted error | subcategory | usd per triage |
| --- | --- | --- | --- | --- | --- | --- |
| gemini-3.1-flash-lite | 6/6 | 0.667 | **0.438** | 0.500 | **0.500** | 0.0003 |
| regex baseline | 6/6 | 0.667 | 0.200 | **0.417** | 0.000 | 0.0000 |
| majority baseline | 6/6 | 0.667 | 0.200 | **0.417** | 0.000 | 0.0000 |

Cost is at the provider's list price; the actual runs were free-tier and billed nothing.

Read that table carefully, because it is the argument for having an eval harness at all:

- **Accuracy says all three are identical.** They are not. Four of six cases are `code_change`, so a classifier that answers `code_change` every time scores 0.667 while understanding nothing.
- **Macro F1 separates them**, because it refuses to let the majority class carry the score. The model more than doubles the floor.
- **Cost weighted error says the model is worse**, and that is not a bug in the metric. The baseline never guesses anything but `code_change`, so it never makes an expensive mistake. The model misread one code failure as infrastructure, which is the kind of error that sends someone hunting a runner outage that never happened.

One metric alone would have told you the wrong story three different ways.

Compare models yourself, on every axis at once:

```bash
uv run python -m evals.compare_models --models gemini-3.1-flash-lite,baseline-regex
```

A model that answers fewer cases is reported as unrankable rather than being quietly scored on the subset it managed.

Honest caveats, kept current:

- 6 cases so far, target is 80. Numbers this small are directional, not conclusive.
- No case has been human-verified yet, so none are eligible for the pull request gate. Labels were derived from log evidence and each case records the line that decided it.
- **The model choice is not settled on quality.** `gemini-3.1-flash-lite` is the default because it is the only model that has answered all six cases. `gemini-3.5-flash` looked stronger on the four it finished before running out of free quota, but four of six is exactly the partial number this harness exists to distrust. That comparison is open.
- `gemini-3.6-flash` is in the registry but has no free tier: an unbilled key gets a 429 on the first request.

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
uv run python -m evals.run_eval                                  # regex baseline, no key needed
uv run python -m evals.run_eval --model gemini-3.1-flash-lite    # needs a free Gemini key
```

Put credentials in `.env`, which is gitignored:

```
BUILDSLEUTH_GITHUB_TOKEN=...     # fine-grained, Actions: read
BUILDSLEUTH_GEMINI_API_KEY=...   # free tier from aistudio.google.com
```

Traces go to any OTLP backend. `docker/phoenix` starts one with a single command.

## Eval design

Scorecards are JSON, committed under `results/`, and pin the four things that produced every number: code revision, model, prompt hash, and dataset hash. A pull request touching agent code, prompts, or the dataset reruns the suite and posts a scorecard diff; the check fails when a metric drifts past tolerance.

Two design choices worth calling out:

- **Misreading a real bug as a flake costs triple.** Cost-weighted error uses an asymmetric penalty because silently hiding a regression is worse than investigating a flake for nothing.
- **Coverage is gated.** A model that crashes on half the dataset would otherwise be scored only on the cases it answered, which raises every other metric. Coverage is compared first, and losing a report entirely counts as a regression.

Taxonomy and metric definitions live in [dataset/README.md](dataset/README.md) with their literature citations.

## License

MIT
