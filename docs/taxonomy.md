# Failure taxonomy

Four classes, each with subcategories. The top level answers the question a
developer actually has when a build goes red: is this my code, is the test
lying, is the machine broken, or is the pipeline wrong.

## The classes

### `code_change`

The repository source is wrong. A rerun of the same commit fails identically.

| Subcategory | Meaning |
| --- | --- |
| `compile_error` | Build, import, or syntax failure, including a symbol absent on the running interpreter |
| `test_assertion` | A test asserted something false about the code |
| `runtime_error` | The code crashed rather than failing an assertion |
| `type_check` | A type checker rejected the code |
| `lint_or_format` | A linter, formatter, or docs build rejected the content |
| `policy_gate` | A contribution rule found a required artefact missing |
| `dependency_conflict` | A declared requirement can never resolve |

### `flaky_test`

The source is fine. The same commit passes on a rerun. Grounded in Luo et al.,
*An Empirical Analysis of Flaky Tests* (FSE 2014), whose root-cause categories
these subcategories follow: `async_wait`, `concurrency`,
`test_order_dependency`, `randomness`, `network_flakiness`, `resource_leak`.

The order-dependency distinction traces to iDFlakies (ICST 2019), which split
flaky tests into order-dependent and non-order-dependent.

### `infra_environment`

The runner, network, or an external service failed, not the code. Categories
follow Rausch et al. (MSR 2017), which documented transient git and
infrastructure errors as a distinct source of Travis CI build noise:
`runner_outage`, `dependency_registry`, `network_timeout`, `oom_or_disk`,
`external_service`.

### `pipeline_config`

The workflow definition itself is wrong: `yaml_syntax`, `action_version`,
`build_step_misconfig`, `permissions_or_secrets`, `cache_misconfig`,
`matrix_or_trigger`. The class comes from the CI-lifecycle taxonomies in
Vassallo et al. (ICSME 2017) and Lou et al. (ESEC/FSE 2020).

## `related_to_diff`

An orthogonal flag, and nullable. Huang et al. (2026) found developers spend a
median of four hours deciding whether a failure even relates to their change,
which is why it is tracked at all.

It is null when the case carries no diff and the class does not settle the
question. The agent is shown no diff either in that situation, so scoring it
there measures guessing rather than triage. Those cases are excluded from the
metric instead of being given an answer inferred from a branch name.

## How three subcategories got here

The taxonomy was revised twice, both times because independent annotators
could not apply it consistently. That is the signal worth acting on: a
category scheme annotators disagree about is a broken measuring instrument.

**`policy_gate`** was added after two annotators independently forced a
missing changelog entry into `lint_or_format` and both flagged it as wrong. A
contribution rule is not a style violation.

**`build_step_misconfig`** was added after both annotators reported that
nothing described a build or checkout step which cannot build the project as
configured. They picked different wrong answers, which is what a missing
category looks like from the outside.

**`runtime_error`** was added after four annotators across two rounds
independently reported that nothing covered a deterministic crash which is
neither a failed compile nor a failed assertion: a broken database migration,
an `AttributeError` from an API a change removed. Each forced it somewhere
different.

Existing labels were left as their annotators wrote them rather than quietly
rewritten to use the new values.

## Known open edges

Annotators flagged these and none is resolved:

- **Checkout and SCM failures** have no clean home. A missing
  `refs/pull/N/merge` is neither a network error, a bad workflow, nor wrong
  source code, and it can be permanent. This produced the one class-level
  disagreement in the whole dataset.
- **Unresolvable requirements originating in a third-party repository** that
  the job checks out are under-specified. The rule contemplates "our
  requirement" versus "registry down" and not "an unpinned external repository
  moved".
- **`flaky_test` requires positive evidence of a passing rerun**, which a
  single-run log structurally cannot contain. The rule therefore
  under-reports flakes, and the four in this dataset are the ones where a
  rerun record or a mechanistic proof of a race happened to be available.

## Cost weighting

Not all mistakes cost the same. Calling a real code bug a flake hides a
regression and is penalised three times as heavily as the reverse, which only
wastes an investigation. The full matrix is in
`src/buildsleuth/models/taxonomy.py`.

## Sources

- Luo, Hariri, Eloussi, Marinov. *An Empirical Analysis of Flaky Tests.* FSE 2014.
- Lam, Oei, Shi, Marinov, et al. *iDFlakies: A Framework for Detecting and Partially Classifying Flaky Tests.* ICST 2019.
- Rausch, Hummer, Leitner, Schulte. *An Empirical Analysis of Build Failures in the Continuous Integration Workflows of Java-Based Open-Source Software.* MSR 2017.
- Vassallo, Schermann, Zampetti, et al. *A Tale of CI Build Failures.* ICSME 2017.
- Lou, Chen, Zhang, et al. *Understanding Build Issue Resolution in Practice.* ESEC/FSE 2020.
- Huang et al. *Is this Build Failure Related to my Patch?* arXiv, 2026.
