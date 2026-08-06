# BuildSleuth

**AI proposes, deterministic checks verify.**

BuildSleuth reads a failing GitHub Actions run, decides what kind of failure it is, finds the file responsible, and opens a draft pull request with a fix. Every model output is a proposal that must pass deterministic gates before anything is promoted.

Explaining a failed build is a commodity feature now. Measuring how often the explanation is right, at what cost, and catching it when it gets worse is not, so this project publishes its own benchmark.

## It works on real failures

[**Draft pull request #2**](https://github.com/rajeev-chaurasia/build-sleuth/pull/2) was opened by BuildSleuth against this repository, from a genuinely failing build. It is a draft, labelled `ai-generated`, names the model that wrote it, quotes the log lines behind the verdict, and says what was checked mechanically and what was not.

The first failure it ever triaged was not the one staged for it. GitHub Actions went down mid-demo with `Failed to resolve action download info. Error: Service Unavailable`. The agent classified it `infra_environment / external_service` at full confidence and **refused to write a patch**: *"Rerun, pin, or quarantine instead."* Nobody could have staged that.

## Scorecard

48 curated failures from ten public repositories. Every model scored through the same code path.

| model | coverage | accuracy | macro F1 | cost weighted error | subcategory | hit@1 | usd per triage |
| --- | --- | --- | --- | --- | --- | --- | --- |
| gemini-3.1-flash-lite | 37/48 | 0.865 | **0.761** | 0.230 | 0.730 | 0.500 | 0.0006 |
| majority baseline | 48/48 | 0.792 | 0.221 | 0.250 | 0.271 | n/a | 0.0000 |
| regex baseline | 48/48 | 0.714 | 0.208 | 0.619 | 0.190 | n/a | 0.0000 |

**Read the coverage column first.** The model answered 37 of 48 before the day's free-tier quota ran out, so its row is not directly comparable to the baselines. Metrics computed over the cases a model managed always flatter it. Cost is at list price; the runs were free-tier and billed nothing.

That table is the argument for the whole project:

- **Accuracy barely separates a working model from one that thinks nothing.** The majority baseline answers `code_change` unconditionally and scores 0.792, because that is what 38 of 48 cases are. On this data accuracy mostly measures the class distribution.
- **Macro F1 separates them decisively**, 0.761 against 0.221, by refusing to let the majority class carry the score.
- **Coverage decides whether any of it counts.** A model that answers only the easy cases would top every other column.

One metric would have told you the wrong story three different ways.

## What the harness caught

Each of these was a real defect found by running the thing rather than reasoning about it.

**A gate that a broken model could pass.** A model crashing on 60 of 100 cases and getting the remaining 40 right scored better on every metric, and the regression gate approved it. Coverage is now gated first, and losing a report entirely counts as a regression rather than an absence of news.

**Bigger is not reliably better.** A 550B model leads on every quality axis. The 120B from the same family scores half the accuracy of a small Flash model and posts the worst cost-weighted error measured. Parameter count predicted neither.

**A model written off for a bug in the harness.** That 550B first scored 3 of 6 and was reported unrankable. It is a reasoning model: its hidden thinking tokens are billed against the output budget, and a budget sized for a short verdict left it nothing to answer with. One number changed, and it went from unrankable to best in class.

**Retries that burned the quota they were waiting on.** A 429 can mean "slow down" or "your allowance is gone". Retrying the second kind spent three more requests against the exhausted quota, turning one failed case into four wasted calls.

**Diffs that disagreed with their own logs.** A pull request diff follows the branch tip, so curated cases were paired with code that did not exist when the log was written. One contained the very fix the log complained was missing.

**Correct patches rejected as corrupt.** A model named exactly the right change and git refused it, because the trailing newline it requires was missing. Without normalization most correct patches would be discarded as wrong.

**A benchmark my own mining had skewed.** Filtering to pull request events produced 42 cases with zero infrastructure failures, where guessing `code_change` scored 0.857. Re-mining across all event types found the missing class.

## How it works

```
run url -> ingest -> condense -> classify -> localize -> fix -> policy -> verify -> draft PR
           [det]     [det]       [model]     [model]     [model] [det]    [det]     [det, guarded]
```

Ingest snapshots a run so triage is offline and eval cases stay replayable after GitHub deletes the logs at ninety days. Condensation reduces real logs, which run from 39 to 36,000 lines here, to a few kilobytes around the error. Localization is skipped entirely for flakes and infrastructure failures, which have no culprit file; a guess there sends somebody to read a file that was never at fault.

**The write path is guarded.** The allowlist is empty by default, so a fresh checkout cannot open a pull request anywhere. It refused this repository until it was explicitly opted in. Patch policy rejects anything too large to review, anything carrying what looks like a credential, and any edit to the workflow files unless the failure was in the pipeline itself: a code bug must not be fixed by changing the checks that caught it.

## Quick start

```bash
uv sync
uv run buildsleuth fetch https://github.com/OWNER/REPO/actions/runs/RUN_ID
uv run buildsleuth condense snapshots/OWNER-REPO-RUN_ID/log.txt
uv run python -m evals.run_eval                                # regex baseline, no key needed
uv run python -m evals.run_eval --model gemini-3.1-flash-lite  # needs a free Gemini key
uv run python -m evals.compare_models --models a,b,c           # every axis at once
uv run python -m evals.trials --model M --trials 3             # measure the run to run noise
```

Credentials go in `.env`, which is gitignored:

```
BUILDSLEUTH_GITHUB_TOKEN=...     # fine grained, Actions: read
BUILDSLEUTH_GEMINI_API_KEY=...   # free tier from aistudio.google.com
BUILDSLEUTH_PR_ALLOWLIST=        # empty means no repository may be written to
```

Traces are hand-written OpenTelemetry with `gen_ai` attributes and go to any OTLP backend. `docker/phoenix` starts one with a single command.

## How the labels were checked

Every case was labelled by two annotators working blind: they saw the log and the diff, never the recorded label, the mining heuristic's guess, or each other's work. Only cases where both independently agreed entered the dataset.

| axis | agreement across 44 mined cases |
| --- | --- |
| failure class | 43 of 44 |
| class, subcategory and relatedness together | 42 of 44 |

Disagreements were held back for a person rather than settled by a tiebreaker, because a case two annotators read differently is either genuinely ambiguous or a hole in the taxonomy. That second possibility produced three new subcategories: `policy_gate`, `build_step_misconfig`, and `runtime_error`, each added after annotators independently forced the same failure into the same wrong box.

The annotators also refused to judge whether a failure followed the diff on cases carrying no diff, and they were right: the agent is shown no diff either, so scoring it there measures guessing. That field is nullable now.

**The honest limit:** these annotators are instances of the same model family, with correlated blind spots. High agreement between them is a real signal, but it is not two independent human experts agreeing. Six cases carry a human sign-off, recorded in a separate field so the two are never conflated.

## Caveats, kept current

- 48 cases, target is 80. Small enough that a handful of cases moves a number.
- Class balance is skewed: 38 `code_change`, 4 flaky, 3 infrastructure, 3 pipeline config. That is roughly what mining public repositories yields, and it is why macro F1 rather than accuracy is the headline.
- **The dataset has outgrown the free tier.** A full run is about ninety model calls and the daily quota ran out mid-run. Full-suite runs need batching, a paid key, or a local model.
- **The model choice is not settled on quality.** `gemini-3.1-flash-lite` is the default because it completed a run, not because it won a fair comparison. `gemini-3.5-flash` looked stronger on the cases it finished before running out of quota, which is exactly the partial number this harness exists to distrust.
- Regression tolerances are deliberately loose, because measured run-to-run noise is larger than the drift worth catching. They tighten when the dataset does.

## Documentation

- [Architecture](docs/architecture.md), and the decisions that cost something
- [Taxonomy](docs/taxonomy.md), with its literature grounding and open edges
- [Eval methodology](docs/eval-methodology.md), including what the numbers do not mean
- [Dataset](dataset/README.md), how cases were mined and labelled

## License

MIT
