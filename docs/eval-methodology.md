# Eval methodology

## What a scorecard is

One JSON file per run under `results/`, pinning the four things that produced
every number: code revision, model, prompt hash, and dataset hash. A number
without those four is not quotable, so they are part of the filename.

Prompt hash covers every active prompt, so editing any of them produces a new
scorecard identity rather than silently overwriting a comparison.

## Metrics, and why each exists

**Coverage.** The share of cases a model actually answered. Reported first,
because every other metric is computed over the cases a model managed and is
therefore flattered by dropping the hard ones. A model that crashes on half
the dataset would otherwise post the best numbers on the page.

**Accuracy.** Included because readers expect it, and close to useless on this
data: 38 of 48 cases are `code_change`, so a classifier that answers
`code_change` unconditionally scores 0.792.

**Macro F1.** The headline. Averaged over all four classes including
zero-support ones, which caps a subset missing a class below 1.0 by design.
That is stated in the rendered scorecard rather than left as a trap.

**Cost-weighted error.** Not all mistakes cost the same. Calling a real bug a
flake hides a regression and is penalised three times as heavily as the
reverse. Range is 0 to 3, which the scorecard prints, because a number that
looks like a rate but is not gets misread.

**Localization: hit@1, hit@5, MRR.** File-level, following the fault
localization literature. Cases with no expected culprit are excluded rather
than scored as zero.

**Fix quality: a funnel.** Applies, lints, failing test passes, nothing else
broke. A single pass rate hides the difference between a model whose patches
do not apply and one whose patches apply and change nothing, and those need
opposite responses. Cases where the agent declined to patch are counted apart
from failures, because declining to patch a flake is correct and scoring it as
a miss would push a model toward guessing.

**This funnel currently has no data.** Only the first rung can be evaluated
without the repository checked out at the failing commit, and every case in
the benchmark stores logs and a diff rather than a checkout, so all 48 are
marked `verification: none`. The scorer and the container runner both exist
and are tested; what does not exist is a case they can run against.

Making this measurable means reproducible cases: importing BugSwarm
artifacts, which ship a failing build, a passing build and the fix diff, or
pinning a small number of repositories whose test suite can be run from a
clean container. Until then, treat every claim about fix quality here as
unproven rather than merely small.

Nothing is scored by asking a model whether an answer looks good. Execution
decides.

## How ground truth was established

Every case was labelled by two annotators working blind: they saw the log,
the diff, and the run metadata, and never the recorded label, the mining
heuristic's guess, or each other's work. Only cases where both independently
agreed on class, subcategory, and diff relatedness entered the dataset.

Across 44 mined cases:

| axis | agreement |
| --- | --- |
| failure class | 43 of 44 |
| class, subcategory and relatedness together | 42 of 44 |

Disagreements were not resolved by a tiebreaker. They were held back for a
person, because a case two annotators read differently is either genuinely
ambiguous or a hole in the taxonomy, and the second possibility is what
produced three new subcategories.

Culprit file lists are intersected, not merged. A path only one annotator
named is a guess by definition, and a wrong path costs more than a missing
one because somebody goes and reads it.

**The honest limit of this.** These annotators are instances of the same model
family with correlated training and blind spots. High agreement between them
is a real quality signal, since they were genuinely blind to each other and to
the existing label, but it is not equivalent to two independent human experts
agreeing and should not be read as such. Six cases carry a human sign-off,
recorded in a separate field from annotator agreement so the two are never
conflated.

## A single run is not a measurement

Two runs of the same model, same prompt, same dataset, at temperature zero,
produced macro F1 of 0.714 and 0.450. Nothing differed but nondeterminism.

That 0.26 swing was larger than the 0.02 regression tolerance the gate shipped
with. A gate tuned tighter than the noise fires on the noise, and a gate that
cries wolf gets ignored, which is worse than having none. Tolerances now sit
above the measured spread, and `python -m evals.trials` reports mean, range,
and standard deviation so they can be retuned from evidence.

The real fix is more cases. Tolerances stay loose until the dataset is large
enough to tighten them honestly.

## The regression gate

A pull request touching agent code, prompts, or the dataset reruns the suite
and posts a scorecard diff. The check fails when a metric drifts past
tolerance.

Three things the gate refuses to do:

- **Compare across different subsets or dataset hashes.** Metrics only mean
  the same thing over the same cases.
- **Treat a lost report as a skip.** If the baseline had a classification
  report and the current run does not, that is a total collapse, not an
  absence of news.
- **Ignore coverage.** Coverage is compared first. A model that stopped
  answering would otherwise raise every other metric and pass.

That last one is not hypothetical. It was a real defect: a model that crashed
on 60 of 100 cases and got the remaining 40 right scored better on every
metric and the gate passed it.

## Running it

```bash
uv run python -m evals.run_eval --model baseline-regex           # no key needed
uv run python -m evals.compare_models --models a,b,c             # every axis at once
uv run python -m evals.trials --model M --trials 3               # measure the noise
uv run python -m evals.diff_baseline --baseline X --current Y    # the gate itself
```

## Why the method is shaped this way

Each rule below exists because its absence produced a wrong number that looked
right. They are recorded here so a future change does not quietly undo one.

**Coverage is read before any other column.** A model that crashes on 60 of
100 cases and answers the rest correctly beats an honest model on accuracy,
macro F1 and cost-weighted error at once. Every one of those metrics is
computed over the cases a model managed, so failing to answer is rewarded
unless coverage is gated first. A lost verdict is therefore a regression, not
an absence of news.

**A partial run is not a small run.** The scorecard records how many cases
produced a verdict and why the others did not. When a fifth of the benchmark
started disappearing, the number that exposed it was coverage: the discarded
cases were identical between runs, and a genuinely exhausted daily quota
cannot produce the same boundary twice. The cause was a per-minute rate limit
sharing the wording of a spent daily allowance, and both being treated as
fatal. Without coverage as a first-class column it would have read as a
limitation of the free tier.

**Output budgets are sized for how a model works, not for the answer.** A
reasoning model spends hidden thinking tokens against the same budget as the
reply. A budget sized for a short verdict leaves such a model nothing to
answer with, and it scores as incapable. One model was reported unrankable at
3 of 6 for this reason, and went to best in class when the budget changed. The
registry now carries a `reasoning` flag and a test asserts every reasoning
model gets the larger budget.

**Fix quality is scored by execution, never by a judge.** The rejected patches
in the funnel are mostly well argued and plausible looking. A model asked to
grade them would pass many, because the failure is in whether the context
lines match a file it cannot see, not in the reasoning. `git apply` has no
opinion about how convincing a patch is.

**The verifier is itself verified, on every case, before it is trusted.** Each
case runs the maintainer's own fix, which must reach the top rung, and a
deliberately damaged copy, which must not. A case failing either test is
excluded from fix metrics and named, because it cannot distinguish a bad patch
from a case the harness cannot run.

That control has caught the harness twice. Once when a repository using CRLF
throughout had its patch silently normalised to LF at four separate layers,
one of them a `.gitattributes` rule applying on commit, which made every hunk
fail and looked like a broken artifact. And once when two changes that were
each correct and each tested combined into a verifier that accepted patches
git rejects: a repair pass recomputed the hunk headers the control had
deliberately damaged, while a fuzzy `patch` fallback accepted whatever
survived. A patch referencing a line present in no file applied cleanly, and
the build passed, on 16 of 22 cases.

The general lesson is the reason this document exists. A measurement that
looks like a property of the world is often a property of the instrument, and
only a check that runs against a known answer can tell the two apart.
