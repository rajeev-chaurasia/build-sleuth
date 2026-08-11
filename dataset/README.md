# BuildSleuth benchmark dataset

Curated CI failures with hand-checked ground truth, snapshotted so they stay
replayable after GitHub expires the original logs (90 day default retention).

## Current status

71 cases: 47 mined from real GitHub Actions runs, 23 imported from BugSwarm
artifacts, and 1 hand-written synthetic case. The 23 BugSwarm cases each carry a
container image and a failing test command, so a proposed fix on them can be run
rather than only read.

Label scrutiny is tracked two ways, and they are not the same claim. 6 cases are
verified by a human (`gm-0001` through `gm-0005` and `sy-0001`), and those same 6
are the only cases tagged `smoke`. A further 45 are unanimously adjudicated,
which `Provenance.trusted` also accepts, putting 48 cases in reach of the pull
request gate. `TriageCase` refuses a `smoke` tag on an unverified case, so the
gate cannot silently run on unreviewed labels.

| Class | Cases |
|---|---|
| `code_change` | 61 |
| `flaky_test` | 4 |
| `infra_environment` | 3 |
| `pipeline_config` | 3 |

## Layout

```
dataset/
  manifest.json              rebuilt by scripts/validate_dataset.py
  cases/
    gha-mined/<case_id>/     mined from a real workflow run
      case.json
      logs/failed_job.txt    cleaned log of the failed job
      diff.patch             the change under test, when the run had a PR
    bugswarm/<case_id>/      imported from a BugSwarm artifact
      case.json
      logs/failed_job.txt
      fix.diff               the maintainer's own fix, for scoring a proposal
    synthetic/<case_id>/     hand-written, never presented as real
```

The directory name is the case id. Every path in `case.json` is relative to the
case directory, and the loader refuses to load a case whose referenced files are
missing or point outside it.

## Taxonomy

Categories follow the CI failure literature so labels are defensible rather than
invented. See `src/buildsleuth/models/taxonomy.py` for the enum and the
subcategories of each class.

| Class | Meaning | Source |
|---|---|---|
| `code_change` | The change under test is at fault: it fails to build, fails a test, or fails a static gate. | Vassallo et al. ICSME 2017; Lou et al. FSE 2020 |
| `flaky_test` | Same commit, different outcome. Async waits, ordering, concurrency, randomness. | Luo et al. FSE 2014; iDFlakies ICST 2019 |
| `infra_environment` | Runner, registry, network, disk, or a third-party service. | Rausch et al. MSR 2017 |
| `pipeline_config` | The workflow definition is at fault, not the repository source. | Vassallo et al. ICSME 2017 |

`related_to_diff` is tracked separately from the class because that is the
question a developer actually has to answer first. Huang et al. 2026 found a
median of four hours spent deciding whether a failure relates to the change.

Misclassification is not symmetric, so `ERROR_COSTS` in `taxonomy.py` weights
calling a real code bug a flake at 3x the neutral cost: that hides a regression,
while investigating a flake only wastes time.

## Labelling methodology

1. Snapshot the run with `buildsleuth fetch <run_url>`, which writes
   `snapshots/<repo>-<run_id>/` with `snapshot.json`, `log.txt`, and
   `diff.patch` when the run has an associated pull request.
2. Read the failed job log. The class comes from the decisive line in the log,
   not from the job name and not from the step name.
3. Set `related_to_diff` from evidence: the captured diff, the head branch, or
   the trigger event. A scheduled run on the default branch is not related to a
   diff.
4. Fill `culprit_files` only when the log genuinely identifies a file. An empty
   list is the correct answer for an infrastructure failure, and for a failure
   whose fix is a file that does not exist yet.
5. Record the reasoning in `provenance.notes`, quoting the decisive log line,
   including anything the evidence does not settle.
6. Leave `verified_by_human` false until a human has reviewed the case, and
   never tag an unverified case `smoke`.

### Known limitations

- None of the mined runs has a rerun attempt, a linked fix commit, or a linked
  issue, so no mined case is labelled from the confirmation signals those
  provide. The 23 imported cases carry `imported` instead, and their culprit
  files come from the maintainer's own fix rather than from a labeller.
- `verification.method` is `none` on the 48 mined and synthetic cases, so no fix
  can be scored as verified on them. Only the 23 BugSwarm cases are executable.
- The classes are heavily unbalanced towards `code_change`, which is 61 of the
  71 cases. Accuracy flatters a model on this mix, so the scorecard leads with
  macro F1 over all four classes.

## Adding a case

1. `uv run buildsleuth fetch <run_url>`.
2. `mkdir dataset/cases/gha-mined/<case_id>/logs` and copy the cleaned log in as
   `logs/failed_job.txt`. Truncate to the last 2000 lines only if the log is
   huge, keep the failure region, and say so in `provenance.notes`. Copy
   `diff.patch` when the snapshot has one.
3. Write `case.json` against the `TriageCase` schema in
   `src/buildsleuth/models/case.py`. Take `repo`, `run_id`, `run_attempt`,
   `head_sha`, `pr_number`, and the job and step names from `snapshot.json`.
4. Label it by reading the log, per the methodology above.
5. `uv run python scripts/validate_dataset.py` to validate every case and
   rebuild `manifest.json`, then commit the case and the manifest together.

CI runs the same script with `--check`, which fails when the committed manifest
no longer matches the cases on disk.

## Manifest and dataset hash

`dataset_hash` is the first 12 hex characters of a sha256 over the canonical
JSON of every case plus a digest of every file each case references. Cases are
sorted by id, JSON keys are sorted, text is read with LF line endings, and no
timestamp is folded in, so the same dataset hashes the same on every machine.
Scorecards quote the hash so a result can name the exact dataset it was
measured on.

## Case index

`manifest.json` is the index: every case id with its failure class, source and
tags, plus the case count and the dataset hash. It is rebuilt from the cases on
disk rather than maintained by hand, so prefer it over a table here, which would
go stale the next time a case is added. The repo, the subcategory and the
labelling notes live in each `case.json`.

The mined logs are unmodified except for three things: the UTF-8 BOM that
GitHub puts on downloaded logs is dropped, `clean_log` is applied again to
strip the timestamps and escape sequences that survived the first pass, and
gm-0001 and gm-0005 are truncated to their last 2000 lines.
