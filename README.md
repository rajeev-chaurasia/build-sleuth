# BuildSleuth

**AI proposes, deterministic checks verify.**

BuildSleuth ingests a failing GitHub Actions run, classifies the failure (code change, flaky test, infrastructure, or pipeline config), localizes the root cause at file level, and proposes a fix as a draft PR. Every model output is a validated proposal that must pass deterministic gates before anything is promoted.

The differentiator is the eval harness. Explaining a failed build is a commodity feature now; measuring how often the explanation is right, at what cost, and catching it when it gets worse is not. Published CI-triage benchmarks are scarce, so this project builds and publishes its own.

## Status

Classification works end to end against real models. Localization and fix generation are next.

Current scorecard, 48 cases, every model scored through the same code path:

| model | coverage | accuracy | macro F1 | cost weighted error | subcategory | hit@1 | usd per triage |
| --- | --- | --- | --- | --- | --- | --- | --- |
| gemini-3.1-flash-lite | 37/48 | 0.865 | 0.761 | 0.230 | 0.730 | 0.500 | 0.0006 |
| majority baseline | 48/48 | 0.792 | 0.221 | 0.250 | 0.271 | n/a | 0.0000 |
| regex baseline | 48/48 | 0.714 | 0.208 | 0.619 | 0.190 | n/a | 0.0000 |

**The model row is not directly comparable to the baselines.** It answered 37 of 48 cases before the day's free-tier quota ran out, and metrics computed over the cases a model managed always flatter it. Read the coverage column first; that is what it is there for.

Cost is at the provider's list price. The runs themselves were free-tier and billed nothing.

Read that table carefully, because it is the argument for having an eval harness at all:

- **Accuracy barely separates the model from a classifier that thinks nothing.** The majority baseline answers `code_change` every time and scores 0.792, because 38 of 48 cases are that class. Accuracy on this data is close to a measure of the class distribution.
- **Macro F1 separates them decisively**, 0.761 against 0.221, because it refuses to let the majority class carry the score.
- **Coverage decides whether any of it counts.** The model's numbers cover 37 cases; the baselines' cover 48. A model that answers only the cases it finds easy would post the best numbers in the table.

One metric alone would tell you the wrong story three different ways.

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

- 48 cases, target is 80. Better than six, still small enough that a handful of cases moves a number.
- **The dataset has outgrown the free tier.** Forty eight cases cost roughly ninety model calls with localization on, and the day's quota ran out mid-run. Full-suite runs now need batching across days, a paid key, or a local model. The rate limiter refuses a run it has counted itself out of, but it cannot see quota an account spent elsewhere.
- The class balance is still skewed: 38 of 48 cases are `code_change`, 4 flaky, 3 infrastructure, 3 pipeline config. That is roughly what mining public repositories yields, and it is why macro F1 rather than accuracy is the headline.
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
