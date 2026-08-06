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
