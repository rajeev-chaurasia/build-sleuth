# BuildSleuth

**AI proposes, deterministic checks verify.**

BuildSleuth reads a failing GitHub Actions run, decides what kind of failure it is, finds the file responsible, and opens a draft pull request with a fix. Every model output is a proposal that must pass deterministic gates before anything is promoted.

Explaining a failed build is a commodity feature now. Measuring how often the explanation is right, at what cost, and catching it when it gets worse is not, so this project publishes its own benchmark.

## It works on real failures

[**Draft pull request #2**](https://github.com/rajeev-chaurasia/build-sleuth/pull/2) was opened by BuildSleuth against this repository, from a genuinely failing build. It is a draft, labelled `ai-generated`, names the model that wrote it, quotes the log lines behind the verdict, and says what was checked mechanically and what was not.

The first failure it ever triaged was not the one staged for it. GitHub Actions went down mid-demo with `Failed to resolve action download info. Error: Service Unavailable`. The agent classified it `infra_environment / external_service` at full confidence and **refused to write a patch**: *"Rerun, pin, or quarantine instead."* Nobody could have staged that.

## Scorecard

61 cases: 48 failures mined and labelled from ten public repositories, plus 13 imported as executable artifacts. The table below is classification, scored on the 48; the fix funnel further down is scored on the executable subset. Every model runs through the same code path.

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

**Ground truth that looked fine and described a virtualenv.** The artifact extractor took the first directory in the image, which is the build cache, so three imported cases arrived with a "maintainer fix" that was a 479KB diff of a rebuilt Python environment. Later it resolved the owner directory instead of the checkout, and every culprit path came out as `numpy/numpy/core/arrayprint.py`, which scores a correct answer as a miss. Both passed silently. Resolving to the cache is now an error rather than a case.

**Benchmark cases that cannot measure anything.** Running each maintainer fix through the verifier before trusting it showed that four of thirteen do not pass their own fix. Had they stayed in, the reported rate would have been computed over cases where no patch could ever have succeeded.

**A fix rate that was measuring my own harness.** The funnel's first numbers came from a fix stage handed an empty set of file contents, so the model was writing a unified diff for source it had never read and its context lines could only match by accident. Supplying the file upstream at the same commit was no better, because these artifacts are patched for reproducibility and upstream disagrees with the image. Reading the file out of the artifact itself is the version now reported, and it scores worse than either: the earlier apply rate was luck, not capability.

**A benchmark my own mining had skewed.** Filtering to pull request events produced 42 cases with zero infrastructure failures, where guessing `code_change` scored 0.857. Re-mining across all event types found the missing class.

## How it works

```
run url -> ingest -> condense -> classify -> localize -> fix -> policy -> verify -> draft PR
           [det]     [det]       [model]     [model]     [model] [det]    [det]     [det, guarded]
```

**What `verify` means today, precisely.** It has four rungs: the patch applies, it lints, the failing test passes, nothing else broke. For the executable cases the whole original job is rerun, so a pass means the failure stopped and nothing else in the suite broke, with no judgement call left. The 48 mined cases are marked `verification: none`, because checking whether a patch works needs the repository at the failing commit and those store logs and diffs.

### The fix funnel

Measured on the nine executable cases the control below found sound, with `gemini-3.1-flash-lite`. The model is shown the file it asked to edit, read out of the artifact itself.

| stage | cases | share of attempted |
| --- | --- | --- |
| patch attempted | 9 | 1.00 |
| applies cleanly | 1 | 0.11 |
| failing test passes | 0 | 0.00 |
| nothing else broke | 0 | 0.00 |

**Eight of nine patches are rejected before anything is run.** Splitting that by cause is the useful part, and it needs the localization result alongside the patch:

| why it failed | cases |
| --- | --- |
| patched a file that was not the culprit | 3 |
| malformed diff, `corrupt patch at line N` | 3 |
| well formed, context did not match the file | 2 |
| applied, did not fix the failure | 1 |

Localization was on target for 6 of 9. So the model is usually looking at the right file, has been handed that file's exact contents, and still emits a diff git will not take. Three of those are not near-misses: the hunk header disagrees with the hunk body, which is a formatting failure rather than a reasoning one.

No LLM judge produces this table. A judge reading those eight patches would have seen a correct explanation and a plausible-looking diff, and the three malformed ones would have scored well. `git apply` disagreed with all of them.

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

The last two start containers holding a whole CI environment, so they run on disposable machines rather than a laptop: `gh workflow run fix-funnel.yml`. Importing new executable cases works the same way, one artifact per runner:

```bash
uv run python scripts/select_bugswarm.py --limit 12
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

- 61 cases, target is 80. Small enough that a handful of cases moves a number.
- **The fix funnel rests on nine cases, and single runs move.** Nine is enough to say most patches are rejected before execution, which is a large and repeatable effect across every configuration tried. It is not enough to rank models on fix quality, and individual cases flip between runs. Treat the funnel shape as the result and the exact counts as noisy.
- **Zero fixes is a real number, not a rounding of one.** The one case that passed in an earlier run did so while the model was patching a file it had not read.
- Class balance is skewed: 38 `code_change`, 4 flaky, 3 infrastructure, 3 pipeline config. That is roughly what mining public repositories yields, and it is why macro F1 rather than accuracy is the headline.
- **The dataset has outgrown the free tier.** A full run is about ninety model calls and the daily quota ran out mid-run. Full-suite runs need batching, a paid key, or a local model.
- **The model choice is not settled on quality.** `gemini-3.1-flash-lite` is the default because it completed a run, not because it won a fair comparison. `gemini-3.5-flash` looked stronger on the cases it finished before running out of quota, which is exactly the partial number this harness exists to distrust.
- Regression tolerances are deliberately loose, because measured run-to-run noise is larger than the drift worth catching. They tighten when the dataset does.
- **Five executable cases are excluded because their own reference fix does not pass the verifier.** Two of those do not even apply, which points at the extraction rather than at the cases. That is unfinished work, and until it is finished the measurable subset is smaller than the importable one.
- Class balance is unchanged by the import: the 13 executable cases are all `code_change`, so they widen fix coverage and not class coverage.

## Documentation

- [Architecture](docs/architecture.md), and the decisions that cost something
- [Taxonomy](docs/taxonomy.md), with its literature grounding and open edges
- [Eval methodology](docs/eval-methodology.md), including what the numbers do not mean
- [Dataset](dataset/README.md), how cases were mined and labelled

## License

MIT
