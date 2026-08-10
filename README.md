# BuildSleuth

**AI proposes, deterministic checks verify.**

BuildSleuth reads a failing GitHub Actions run, decides what kind of failure it is, finds the file responsible, and opens a draft pull request with a fix. Every model output is a proposal that has to pass deterministic gates before anything is promoted.

Explaining a failed build is a commodity feature. Measuring how often the explanation is right, at what cost, and catching it when it gets worse is not, so this project ships its own benchmark and publishes what the benchmark says.

## It works on real failures

[**Draft pull request #2**](https://github.com/rajeev-chaurasia/build-sleuth/pull/2) was opened by BuildSleuth against this repository from a genuinely failing build. It is a draft, labelled `ai-generated`, names the model that wrote it, quotes the log lines behind the verdict, and states what was checked mechanically and what was not.

The first failure it ever triaged was not the one staged for it. GitHub Actions went down mid-demo with `Failed to resolve action download info. Error: Service Unavailable`. It classified the failure `infra_environment / external_service` at full confidence and **refused to write a patch**: *"Rerun, pin, or quarantine instead."*

## Scorecard

71 cases: 48 failures mined and labelled from ten public repositories, plus 23 imported as executable artifacts. Every model runs through the same code path, over the whole dataset.

| model | coverage | accuracy | macro F1 | cost weighted error | subcategory | hit@1 | usd per triage |
| --- | --- | --- | --- | --- | --- | --- | --- |
| gemini-3.1-flash-lite | 71/71 | 0.915 | **0.771** | 0.141 | 0.648 | 0.509 | 0.0008 |
| majority baseline | 71/71 | 0.859 | 0.231 | 0.169 | 0.507 | n/a | 0.0000 |
| regex baseline | 71/71 | 0.775 | 0.218 | 0.423 | 0.338 | n/a | 0.0000 |

Three columns, three different stories:

- **Accuracy barely separates a working model from one that thinks nothing.** The majority baseline answers `code_change` unconditionally and scores 0.859, because that is what most of these cases are. On this data accuracy mostly measures the class distribution.
- **Macro F1 separates them decisively**, 0.771 against 0.231, by refusing to let the majority class carry the score. Six accuracy points and fifty four macro F1 points describe the same pair of models.
- **Coverage decides whether either counts.** A model that answers only the cases it finds easy beats an honest one on every other column, so coverage is gated first and a lost verdict counts as a regression.

## Fix quality, measured by execution

Twenty three cases carry a container holding the repository at the failing commit plus the script the original job ran, so a proposed patch can be applied and that job rerun. No judgement call, no model grading another model.

### Is the case able to measure anything?

Before any patch is scored, each case is verified twice with no model involved: once with the maintainer's own fix, which must reach the top rung, and once with that fix deliberately damaged, which must not.

**20 of 23 cases pass that control.** The other three are excluded and named in [`results/verifier-control.json`](results/verifier-control.json): their reference fix does not make the build pass, all three failing at dependency install or package download inside the image. Scoring them would blame the model for a case the harness cannot run.

### The funnel

Measured on those 20 cases with `gemini-3.1-flash-lite`. The model is shown the file it asked to edit, read out of the container, and every patch is applied with `git apply`.

| stage | cases | share of attempted |
| --- | --- | --- |
| patch attempted | 20 | 1.00 |
| applies cleanly | 5 | 0.25 |
| failing test passes | 2 | 0.10 |
| nothing else broke | 2 | 0.10 |

Three quarters are rejected before anything runs, and the split is the useful part:

| outcome | cases |
| --- | --- |
| context did not match the file | 5 |
| patched a file that was not the culprit | 5 |
| malformed diff | 5 |
| applied, did not fix the failure | 3 |
| fixed the build and broke nothing | 2 |

Localization was on target for 14 of 20. So the model usually finds the right file, is handed that file's exact contents, and still writes a diff git will not take. The failures divide almost evenly three ways, which is why one rate is the wrong summary: aiming at the wrong file, mis-copying context, and emitting malformed output need three different fixes.

An LLM judge would not produce this table. Reading the fifteen rejected patches it would have seen a confident explanation and a plausible diff on most of them.

## What the measurement catches

The harness is built to catch results that look reasonable and are not. Four it has caught:

- **A gate a broken model could pass.** A model crashing on 60 of 100 cases and getting the rest right scored better on every metric than one that answered them all. Coverage is now gated before anything else is read.
- **A model written off for a harness bug.** A 550B model scored 3 of 6 and was reported unrankable. Its hidden reasoning tokens are billed against the output budget, and a budget sized for a short verdict left it nothing to answer with. One constant changed and it went from unrankable to best in class.
- **A fifth of the benchmark discarded silently.** A per-minute rate limit and a spent daily quota return the same wording from the provider, and both were treated as fatal. The tell was that the discarded cases were identical from run to run, which a real quota cannot produce. Coverage went from 48/71 to 71/71.
- **A verifier that accepted patches git rejects.** Two changes that were each correct and each tested combined to disable the check that would have caught either. A patch referencing a line present in no file applied cleanly on 16 of 22 cases. Every apply rate measured through it was inflated.

Longer accounts of each, and why the method is shaped the way it is, are in [the eval methodology](docs/eval-methodology.md).

## How it works

```
run url -> ingest -> condense -> classify -> localize -> fix -> repair -> policy -> verify -> draft PR
           [det]     [det]       [model]     [model]     [model] [det]   [det]    [det]     [det, guarded]
```

Three model calls with deterministic code on both sides. Ingest snapshots a run so triage is offline and cases stay replayable after GitHub deletes the logs at ninety days. Condensation reduces logs, which run from 39 to 36,000 lines here, to a few kilobytes around the error. Localization is skipped for flakes and infrastructure failures, which have no culprit file, because a guess there sends somebody to read a file that was never at fault.

`verify` has four rungs: the patch applies, it lints, the failing test passes, nothing else broke. For executable cases the whole original job is rerun, so a pass covers the rest of the suite by construction.

**The write path is guarded.** The allowlist is empty by default, so a fresh checkout cannot open a pull request anywhere. Patch policy rejects anything too large to review, anything carrying what looks like a credential, and any edit to the workflow files unless the failure was in the pipeline itself: a code bug must not be fixed by changing the check that caught it.

The full design, with a diagram and the decisions that cost something, is in [the architecture doc](docs/architecture.md).

## Quick start

```bash
uv sync
uv run buildsleuth fetch https://github.com/OWNER/REPO/actions/runs/RUN_ID
uv run buildsleuth condense snapshots/OWNER-REPO-RUN_ID/log.txt
uv run python -m evals.run_eval                                # regex baseline, no key needed
uv run python -m evals.run_eval --model gemini-3.1-flash-lite  # needs a free Gemini key
uv run python -m evals.compare_models --models a,b,c           # every axis at once
uv run python -m evals.trials --model M --trials 3             # run to run noise
```

Verification starts containers holding an entire CI environment, several gigabytes each, so those run on disposable machines:

```bash
gh workflow run eval-full.yml        # score a model over the whole dataset
gh workflow run fix-funnel.yml       # run every proposed patch and see how far it gets
gh workflow run import-bugswarm.yml  # add executable cases, one artifact per runner
```

Credentials go in `.env`, which is gitignored:

```
BUILDSLEUTH_GITHUB_TOKEN=...     # fine grained, Actions: read
BUILDSLEUTH_GEMINI_API_KEY=...   # free tier from aistudio.google.com
BUILDSLEUTH_PR_ALLOWLIST=        # empty means no repository may be written to
```

The baselines need no key at all, so part of the scorecard reproduces before signing up for anything. Traces are hand-written OpenTelemetry with `gen_ai` attributes and go to any OTLP backend; `docker/phoenix` starts one.

## How the labels were checked

Every mined case was labelled by two annotators working blind: they saw the log and the diff, never the recorded label, the mining heuristic's guess, or each other's work. Only cases where both independently agreed entered the dataset.

| axis | agreement across 44 mined cases |
| --- | --- |
| failure class | 43 of 44 |
| class, subcategory and relatedness together | 42 of 44 |

Disagreements were held for a person rather than settled by a tiebreaker, because a case two annotators read differently is either genuinely ambiguous or a hole in the taxonomy. That second possibility produced three subcategories: `policy_gate`, `build_step_misconfig` and `runtime_error`.

**The honest limit:** those annotators are instances of the same model family and share blind spots, so high agreement between them is real signal but not two independent experts agreeing. Six cases carry a human sign-off, recorded in a separate field so the two are never conflated.

## Caveats

- **The class balance is heavily skewed:** 61 `code_change`, 4 flaky, 3 pipeline config, 3 infrastructure. That is roughly what mining public repositories yields, and it is why macro F1 rather than accuracy is the headline. The three small classes are each measured on a handful of cases.
- **The 23 executable cases are all `code_change`,** so they widen what execution can verify without widening class coverage.
- **The funnel rests on 20 cases.** Twenty supports "most patches are rejected before execution", which is a large effect. It does not support ranking models on fix quality, and the two successes are an anecdote with error bars rather than a 10 percent rate to quote.
- **The model choice is not settled on quality.** `gemini-3.1-flash-lite` is the default because it completes a full pass on a free tier, not because it won a fair comparison against larger models at equal coverage.
- Regression tolerances are deliberately loose, because measured run-to-run noise is larger than the drift worth catching. They tighten when the dataset does.

## Documentation

- [Architecture](docs/architecture.md), the pipeline diagram and the decisions that cost something
- [Taxonomy](docs/taxonomy.md), the failure classes, their literature grounding and open edges
- [Eval methodology](docs/eval-methodology.md), how each metric is computed and what it does not mean
- [Dataset](dataset/README.md), how cases were mined, labelled and verified

Every number above is backed by a file in `results/`, regenerated by a command rather than edited by hand: the model and baseline scorecards, `fix-funnel.json` with the reason each patch stopped where it did, and `verifier-control.json` with which cases can measure a fix.

## License

MIT
