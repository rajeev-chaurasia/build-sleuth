# BuildSleuth development rules

These rules apply to every session and every subagent working in this repo.

## Style

- SOLID, DRY, YAGNI. Small single-purpose modules. No speculative abstractions.
- Comments are concise and only explain non-obvious constraints. Never narrate what the next line does.
- Docstrings on public functions, plain natural language.
- No em-dashes anywhere: code, docs, README, commit messages. Use plain hyphens or restructure the sentence.
- No magic strings or numbers. Use StrEnum, module-level constants, or Settings fields.
- Python 3.12+, pydantic v2 models at every stage boundary, full type annotations, mypy strict must pass.
- Before declaring any work done, run the gates in this order, because the formatter can produce lines the linter rejects and running them the other way round lets that reach CI:

```
uv run ruff format . && uv run ruff check . && uv run mypy && uv run pytest
```

## Architecture invariants

- Deterministic core, LLM at the edges. A module either has zero LLM calls (unit-testable) or is a thin prompt-plus-parse wrapper (eval-scored). Never both.
- Every LLM output is a pydantic-validated proposal. Only `guardrails/` and `pipeline/verify.py` may approve side effects.
- Prompts live in `src/buildsleuth/prompts/` as versioned markdown files and are content-hashed. Never inline a prompt in Python.
- `pipeline/pr.py` is the only caller of the RepoWriter protocol. Write targets must pass the WriteGuard allowlist, which defaults to empty.
- No live LLM or network calls in unit tests. GitHub HTTP goes through VCR cassettes with secrets scrubbed.

## Git

- Author and committer: Rajeev Chaurasia <Rajeevchaurasia.dev@gmail.com> (already set in repo config).
- Conventional commit messages (feat:, fix:, chore:, test:, docs:). No AI attribution or tooling mentions in commits.
- Never force-push. Never commit .env or any credential.
