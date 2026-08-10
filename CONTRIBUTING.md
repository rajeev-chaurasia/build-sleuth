# Contributing

The rules below are what the code already follows. They are written down so a
change that breaks one is obvious in review rather than a matter of taste.

## Getting set up

```bash
uv sync --all-groups
uv run buildsleuth --help
uv run python -m evals.run_eval        # regex baseline, needs no API key
```

Credentials live in `.env`, which is gitignored. Nothing in the repository
reads a key from anywhere else. See the quick start in the README for the
variables.

## Before opening a pull request

Run the gates in this order:

```bash
uv run ruff format . && uv run ruff check . && uv run mypy && uv run pytest --cov
```

The order matters. Formatting can produce a line the linter rejects, so
running the linter first lets that reach CI.

`--cov` matters too. CI fails under 90 percent coverage, and plain
`uv run pytest` passes locally while CI fails. That has happened once, after
new modules landed with tests covering their logic but not their entry points.

## Style

- SOLID, DRY, YAGNI. Small single purpose modules, no speculative abstractions.
- Comments explain a non-obvious constraint or they do not exist. Never narrate
  what the next line does.
- Docstrings on public functions, in plain language.
- No em-dashes anywhere: code, docs, README, commit messages. Use a plain
  hyphen or restructure the sentence.
- No bare strings or numbers with meaning. Use a StrEnum, a module level
  constant, or a Settings field.
- Python 3.12 or newer, pydantic v2 models at every stage boundary, full type
  annotations, and mypy strict has to pass.

## Architecture invariants

These are the load bearing ones. Breaking any of them changes what the project
is, so they need discussion rather than a patch.

- **Deterministic core, model at the edges.** A module either makes no model
  calls and is unit-testable, or it is a thin prompt-and-parse wrapper scored
  by the eval harness. Never both in one module.
- **Every model output is a proposal.** It is validated against a pydantic
  model, and only `guardrails/` and `pipeline/verify.py` may approve a side
  effect.
- **Prompts are versioned files.** They live in `src/buildsleuth/prompts/` as
  markdown and are content hashed into every scorecard. Never inline one in
  Python, or a metric stops being traceable to the prompt that produced it.
- **One writer.** `pipeline/pr.py` is the only caller of the RepoWriter
  protocol, and a write target has to pass the allowlist, which is empty by
  default.
- **No live model or network calls in unit tests.** GitHub traffic goes through
  recorded cassettes with secrets scrubbed. Tests that need the real thing are
  marked `integration` and excluded by default.

## Measurement

A change to a prompt, a model, or a pipeline stage needs a scorecard diff, not
an opinion. `uv run python -m evals.run_eval --model M --save` writes one to
`results/`, and `evals/diff_baseline.py` compares it to the committed baseline.

Read coverage before any other column. A model that answers only the cases it
finds easy beats an honest one on everything else, which is why coverage is
gated first and a lost report counts as a regression.

## Commits

- Conventional messages: `feat:`, `fix:`, `docs:`, `test:`, `chore:`.
- Explain why the change is right, not what the diff shows. The diff already
  shows that.
- Never force push, and never commit `.env` or any credential.
