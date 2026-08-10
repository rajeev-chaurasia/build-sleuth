# BuildSleuth

**AI proposes, deterministic checks verify.**

BuildSleuth reads a failing GitHub Actions run, decides what kind of failure it is, finds the file responsible, and opens a draft pull request with a fix. Every model output is a proposal that must pass deterministic gates before anything is promoted.

Explaining a failed build is a commodity feature now. Measuring how often the explanation is right, at what cost, and catching it when it gets worse is not, so this project publishes its own benchmark.

## It works on real failures

[**Draft pull request #2**](https://github.com/rajeev-chaurasia/build-sleuth/pull/2) was opened by BuildSleuth against this repository, from a genuinely failing build. It is a draft, labelled `ai-generated`, names the model that wrote it, quotes the log lines behind the verdict, and says what was checked mechanically and what was not.

The first failure it ever triaged was not the one staged for it. GitHub Actions went down mid-demo with `Failed to resolve action download info. Error: Service Unavailable`. The agent classified it `infra_environment / external_service` at full confidence and **refused to write a patch**: *"Rerun, pin, or quarantine instead."* Nobody could have staged that.

## Scorecard

71 cases: 48 failures mined and labelled from ten public repositories, plus 23 imported as executable artifacts. The table below is classification; the fix funnel further down is scored on the executable subset. Every model runs through the same code path, over the whole dataset.

| model | coverage | accuracy | macro F1 | cost weighted error | subcategory | hit@1 | usd per triage |
| --- | --- | --- | --- | --- | --- | --- | --- |
| gemini-3.1-flash-lite | 71/71 | 0.915 | **0.771** | 0.141 | 0.648 | 0.509 | 0.0008 |
| majority baseline | 71/71 | 0.859 | 0.231 | 0.169 | 0.507 | n/a | 0.0000 |
| regex baseline | 71/71 | 0.775 | 0.218 | 0.423 | 0.338 | n/a | 0.0000 |

Every row is a complete pass, so they are directly comparable. Cost is at list price; the run was free-tier and billed nothing.

That table is the argument for the whole project:

- **Accuracy barely separates a working model from one that thinks nothing.** The majority baseline answers `code_change` unconditionally and scores 0.859, because that is what most of these cases are. On this data accuracy mostly measures the class distribution.
- **Macro F1 separates them decisively**, 0.771 against 0.231, by refusing to let the majority class carry the score. The gap between the two columns is the point: 6 accuracy points and 54 macro F1 points describe the same pair of models.
- **Coverage decides whether any of it counts.** Getting these three rows to a full pass took finding a bug that had been quietly discarding a fifth of the dataset, described below.

One metric would have told you the wrong story three different ways.

## What the harness caught

Each of these was a real defect found by running the thing rather than reasoning about it.

**A gate that a broken model could pass.** A model crashing on 60 of 100 cases and getting the remaining 40 right scored better on every metric, and the regression gate approved it. Coverage is now gated first, and losing a report entirely counts as a regression rather than an absence of news.

**Bigger is not reliably better.** A 550B model leads on every quality axis. The 120B from the same family scores half the accuracy of a small Flash model and posts the worst cost-weighted error measured. Parameter count predicted neither.

**A model written off for a bug in the harness.** That 550B first scored 3 of 6 and was reported unrankable. It is a reasoning model: its hidden thinking tokens are billed against the output budget, and a budget sized for a short verdict left it nothing to answer with. One number changed, and it went from unrankable to best in class.

**Retries that burned the quota they were waiting on.** A 429 can mean "slow down" or "your allowance is gone". Retrying the second kind spent three more requests against the exhausted quota, turning one failed case into four wasted calls.

**A fifth of the benchmark discarded by the fix for the line above.** Telling those two 429s apart is harder than it looks: Gemini returns the same "exceeded your current quota" wording for a per-minute limit as for a spent day, and only the quota id distinguishes them. Both were treated as fatal, so a limit that clears in twenty six seconds was throwing the case away.

It cost 13 of 61 cases on every full run, and the coverage caveat that sat on this scorecard for weeks was reporting it. The tell was that the missing cases were **identical across runs**: a genuinely spent daily quota cannot produce the same boundary twice, and the cases after the gap answered fine. Deterministic request pacing, deterministic loss. Coverage went from 48/61 to 71/71.

The lesson generalises past this bug: a metric that looks like a limitation of the world was a defect in the harness, and only reporting coverage as a first-class number made it visible at all.

**Diffs that disagreed with their own logs.** A pull request diff follows the branch tip, so curated cases were paired with code that did not exist when the log was written. One contained the very fix the log complained was missing.

**Correct patches rejected as corrupt.** A model named exactly the right change and git refused it, because the trailing newline it requires was missing. Without normalization most correct patches would be discarded as wrong.

**Ground truth that looked fine and described a virtualenv.** The artifact extractor took the first directory in the image, which is the build cache, so three imported cases arrived with a "maintainer fix" that was a 479KB diff of a rebuilt Python environment. Later it resolved the owner directory instead of the checkout, and every culprit path came out as `numpy/numpy/core/arrayprint.py`, which scores a correct answer as a miss. Both passed silently. Resolving to the cache is now an error rather than a case.

**Benchmark cases that cannot measure anything.** Running each maintainer fix through the verifier before trusting it showed that four of thirteen do not pass their own fix. Had they stayed in, the reported rate would have been computed over cases where no patch could ever have succeeded.

**A fix rate that was measuring my own harness.** The funnel's first numbers came from a fix stage handed an empty set of file contents, so the model was writing a unified diff for source it had never read and its context lines could only match by accident. Supplying the file upstream at the same commit was no better, because these artifacts are patched for reproducibility and upstream disagrees with the image. The file now comes out of the artifact itself, which is the copy the patch is applied to.

**A verifier that accepted patches git rejects, and a control that caught it.** Two changes made to fix real bugs combined into a worse one. The apply step gained `patch -l` as a fallback, which matches context fuzzily, and the pipeline gained a repair pass that recomputes hunk headers. The control damages a patch deliberately and requires it to fail, and the repair was quietly undoing that damage while the fuzzy fallback accepted what survived.

A patch referencing a line that exists in no file applied cleanly, and the build passed, on 16 of 22 cases. Every apply rate measured through it was inflated. Corrupting a context line instead, which no repair can reconstruct, and applying only with `git apply` restored the property: **20 of 23 cases now discriminate, against 3 before, and no case accepts the damaged patch.**

This is the strongest argument in the project for checking the checker. Both underlying changes were correct in isolation, both were tested, and together they silently disabled the check that would have caught them.

**A benchmark my own mining had skewed.** Filtering to pull request events produced 42 cases with zero infrastructure failures, where guessing `code_change` scored 0.857. Re-mining across all event types found the missing class.

## How it works

```
run url -> ingest -> condense -> classify -> localize -> fix -> repair -> policy -> verify -> draft PR
           [det]     [det]       [model]     [model]     [model] [det]   [det]    [det]     [det, guarded]
```

**What `verify` means today, precisely.** It has four rungs: the patch applies, it lints, the failing test passes, nothing else broke. For the executable cases the whole original job is rerun, so a pass means the failure stopped and nothing else in the suite broke, with no judgement call left. The 48 mined cases are marked `verification: none`, because checking whether a patch works needs the repository at the failing commit and those store logs and diffs.

### The fix funnel

Measured on the 20 executable cases the control below found sound, with `gemini-3.1-flash-lite`. The model is shown the file it asked to edit, read out of the artifact itself, and every patch is applied by `git apply` only.

| stage | cases | share of attempted |
| --- | --- | --- |
| patch attempted | 20 | 1.00 |
| applies cleanly | 5 | 0.25 |
| failing test passes | 2 | 0.10 |
| nothing else broke | 2 | 0.10 |

**Three quarters of patches are rejected before anything runs.** Splitting that by cause is the useful part, and it needs the localization result alongside the patch:

| outcome | cases |
| --- | --- |
| well formed, context did not match the file | 5 |
| patched a file that was not the culprit | 5 |
| malformed diff | 5 |
| applied, did not fix the failure | 3 |
| fixed the build and broke nothing | 2 |

Localization was on target for 14 of 20. So in most cases the model finds the right file, is handed that file's exact contents, and still writes a diff git will not take. The failures split three ways almost evenly, which is why a single rate is the wrong summary: aiming at the wrong file, mis-copying context, and emitting malformed output need three different fixes.

No LLM judge produces this table. A judge reading the 15 rejected patches would have seen a confident explanation and a plausible diff on most of them.

**A deterministic repair sits between the model and the verifier**, because a hunk header states line counts the diff already contains and a model has no business getting them wrong. It recomputes those counts and restores context markers the model dropped, and it is only credited when the model's own patch is refused first.

On this run it rescued nothing. Five patches were still malformed in ways it cannot reconstruct, which is the honest result: it fixes arithmetic, not a diff that was never coherent. An earlier run where it converted a rejection into a working fix was measured through the broken verifier, so that result does not stand.

### Checking the checker

A funnel of near-zeros has two explanations, a model that writes bad patches or a verifier that cannot recognise a good one, and they need opposite responses. So every case is first verified twice with no model involved: once with the maintainer's own fix, which must reach the top rung, and once with that fix deliberately corrupted, which must not.

**Nine of thirteen executable cases pass that control.** The other four are excluded from the funnel rather than scored: three fail at dependency install or package download inside the image, one fails two of its own tests with the maintainer's fix applied. Counting them would have blamed the model for a case the harness cannot run.

That exclusion is published, in [`results/verifier-control.json`](results/verifier-control.json), rather than being a quiet narrowing of the denominator.

The control also earns its keep by catching the harness. A case whose reference fix stopped applying was not a bad artifact: the repository is CRLF throughout, and four separate layers were stripping the carriage returns out of its patch, one of them `.gitattributes` normalising the file on commit. It came back clean once they were preserved.

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
uv run python -m evals.verifier_control                        # can these cases measure a fix?
uv run python -m evals.fix_funnel --model M                    # how far do the patches get?
```

The last two start containers holding an entire CI environment, several gigabytes each, so they run on disposable machines rather than a laptop:

```bash
gh workflow run eval-full.yml       # score a model over the whole dataset
gh workflow run fix-funnel.yml      # run every proposed patch and see how far it gets
gh workflow run import-bugswarm.yml # add executable cases, one artifact per runner
```

Candidates for that last one come from a query rather than a hand-picked list, so the selection criteria are inspectable and repeatable:

```bash
uv run python scripts/select_bugswarm.py --limit 12
```

Credentials go in `.env`, which is gitignored:

```
BUILDSLEUTH_GITHUB_TOKEN=...     # fine grained, Actions: read
BUILDSLEUTH_GEMINI_API_KEY=...   # free tier from aistudio.google.com
BUILDSLEUTH_PR_ALLOWLIST=        # empty means no repository may be written to
```

Only the first is needed to fetch a run. The eval harness runs its baselines with no key at all, so the numbers below can be reproduced in part before signing up for anything.

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

- **71 cases, and the class balance is heavily skewed:** 61 `code_change`, 4 flaky, 3 pipeline config, 3 infrastructure. That is roughly what mining public repositories yields, and it is why macro F1 rather than accuracy is the headline. It also means the three small classes are each measured on a handful of cases.
- **The 23 executable cases are all `code_change`,** so they widen what can be verified by execution without widening class coverage at all.
- **The fix funnel rests on 20 cases.** Twenty supports "most patches are rejected before execution", which is a large effect. It does not support ranking two models on fix quality, and the 2 successes are an anecdote with error bars rather than a 10 percent rate to quote.
- **Every apply rate published before 2026-08-10 is withdrawn.** They were measured through a verifier that accepted patches git rejects. The figures above are the first taken through checks proven to discriminate.
- **Three executable cases are excluded** because their own reference fix does not make the build pass, all three failing at dependency install or package download inside the image. They are named in the control record rather than dropped quietly.
- **The patch repair is deterministic and narrow.** It recomputes hunk counts and restores missing markers, and on the current run it rescued nothing. It cannot help a patch whose context does not match the file, which is now the largest single failure at 5 of 20.
- **The model choice is not settled on quality.** `gemini-3.1-flash-lite` is the default because it completes a full pass on a free tier, not because it won a fair comparison against larger models on equal coverage.
- A full run is about 140 model calls and takes roughly twelve minutes, most of it waiting out per-minute rate limits. That is the free tier working as intended, not a failure.
- Regression tolerances are deliberately loose, because measured run-to-run noise is larger than the drift worth catching. They tighten when the dataset does.

## Documentation

- [Architecture](docs/architecture.md), the pipeline diagram and the decisions that cost something
- [Taxonomy](docs/taxonomy.md), the failure classes, their literature grounding and open edges
- [Eval methodology](docs/eval-methodology.md), how each metric is computed and what it does not mean
- [Dataset](dataset/README.md), how cases were mined, labelled and verified
- [Contributing](CONTRIBUTING.md), the gates, the style rules and the invariants that are load bearing

## Results in this repository

Every number in this README is backed by a file, and each is regenerated by a command rather than edited by hand.

| file | what it holds |
| --- | --- |
| `results/54ba5ec-gemini-3.1-flash-lite-*.json` | the classification and localization scorecard, all 71 cases |
| `results/54ba5ec-baseline-regex-none.json` | the regex baseline over the same 71 |
| `results/fix-funnel.json` | the fix funnel, per case, with the reason each patch stopped where it did |
| `results/verifier-control.json` | which cases can measure a fix, and why the others cannot |
| `results/baseline.json` | pointer to the scorecard the regression gate compares against |

## License

MIT
