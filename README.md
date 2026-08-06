# BuildSleuth

**AI proposes, deterministic checks verify.**

BuildSleuth ingests a failing GitHub Actions run, classifies the failure (code change, flaky test, infrastructure, or pipeline config), localizes the root cause at file level, and proposes a fix as a draft PR. Every model output is a validated proposal that must pass deterministic gates before anything is promoted.

The differentiator is the eval harness. Explaining a failed build is a commodity feature now; measuring how often the explanation is right, at what cost, and catching it when it gets worse is not. Published CI-triage benchmarks are scarce, so this project builds and publishes its own.

## Status

Classification works end to end against real models. Localization and fix generation are next.

Current scorecard, 6 cases, every model scored through the same code path:

| model | coverage | accuracy | macro F1 | cost weighted error | subcategory | usd per triage | seconds |
| --- | --- | --- | --- | --- | --- | --- | --- |
| nemotron-3-ultra-550b | 6/6 | **0.833** | **0.472** | **0.250** | **0.833** | 0.0043 | 287.5 |
| gemini-3.1-flash-lite | 6/6 | 0.667 | 0.438 | 0.500 | 0.500 | 0.0003 | 8.9 |
| regex baseline | 6/6 | 0.667 | 0.200 | 0.417 | 0.000 | 0.0000 | 0.0 |
| nemotron-3-super-120b | 6/6 | 0.333 | 0.333 | 0.833 | 0.333 | 0.0003 | 25.4 |
| gpt-oss-20b | 5/6 | not comparable | | | | 0.0001 | 231.1 |

Cost is at the provider's list price; the actual runs were free-tier and billed nothing. Thirty two times slower for the best answers is a real tradeoff, not a rounding error, and which end of it you want depends on whether a human is waiting.

Read that table carefully, because it is the argument for having an eval harness at all:

- **Accuracy says all three are identical.** They are not. Four of six cases are `code_change`, so a classifier that answers `code_change` every time scores 0.667 while understanding nothing.
- **Macro F1 separates them**, because it refuses to let the majority class carry the score. The model more than doubles the floor.
- **Cost weighted error says the model is worse**, and that is not a bug in the metric. The baseline never guesses anything but `code_change`, so it never makes an expensive mistake. The model misread one code failure as infrastructure, which is the kind of error that sends someone hunting a runner outage that never happened.

One metric alone would have told you the wrong story three different ways.

**Bigger is not reliably better either.** The 550B model leads on every quality axis, but the 120B from the same family scores half the accuracy of a small Flash model and posts the worst cost weighted error measured, reaching for `pipeline_config` on two failures that were plainly code defects. Parameter count predicts neither result.

**The harness caught a bug that looked like a bad model.** The 550B first scored 3 of 6 and was reported unrankable. The cause was not the model: it is a reasoning model, its hidden thinking tokens are billed against the output budget, and a budget sized for a short verdict left it nothing to answer with. Raising that one number moved it from unrankable to best in class, unchanged in every other respect. Output budget is now a property of the model rather than a constant, and an empty answer after reasoning says so instead of failing with something cryptic.

Compare models yourself, on every axis at once:

```bash
uv run python -m evals.compare_models --models gemini-3.1-flash-lite,baseline-regex
```

A model that answers fewer cases is reported as unrankable rather than being quietly scored on the subset it managed.

## How the labels were checked

Every case was labelled twice more by independent annotators who saw only the log and diff, never the recorded label and never each other's work. Agreement is recorded per case, and the disagreements were the useful part:

| axis | unanimous |
| --- | --- |
| failure class | 6 of 6 |
| subcategory | 5 of 6 |
| related to diff | 3 of 6 |

Both annotators refused to judge whether a failure followed the diff on cases that carry no diff, and they were right to: the agent is shown no diff either, so scoring it there measures guessing. That field is now nullable and unanswerable cases are excluded from the metric rather than given an answer inferred from a branch name. One label was corrected outright as a result.

Both also hit the same two taxonomy gaps, independently, so `code_change` gained `policy_gate` and `pipeline_config` gained `build_step_misconfig`, and the two cases forced into a poor fit moved to them. A taxonomy that annotators cannot apply consistently is a broken measuring instrument, and disagreement is how you find out.

## A single run is not a measurement

Two runs of the same model, same prompt, same six cases, at temperature zero, gave macro F1 of 0.714 and 0.450. Nothing differed but model nondeterminism.

That 0.26 swing was larger than the 0.02 regression tolerance the gate shipped with, which means the gate would have fired on noise and taught everyone to ignore it. Tolerances are now set above the measured spread, and `python -m evals.trials --model M --trials 3` reports mean, range and standard deviation so the numbers can be retuned from evidence instead of taste.

The real fix is more cases: with six, one case flipping moves accuracy by 17 points. The tolerances stay loose until the dataset is large enough for them to tighten honestly.

Honest caveats, kept current:

- 6 cases so far, target is 80. Numbers this small are directional, not conclusive, and the trial spread above is the proof.
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
