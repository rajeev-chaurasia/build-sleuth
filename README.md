# BuildSleuth

**AI proposes, deterministic checks verify.**

BuildSleuth ingests a failing GitHub Actions run, classifies the failure (code change, flaky test, infrastructure, or pipeline config), localizes the root cause at file level, and proposes a fix as a draft PR. Every LLM output is a validated proposal that must pass deterministic gates before anything is promoted.

The differentiator is the eval harness: a curated benchmark of real CI failures with accuracy, cost per triage, and regression tracking across prompt and model versions.

## Status

Under active development. Scorecard will land here when the eval suite is running.

| Metric | Value |
|---|---|
| Classification macro-F1 | pending |
| Localization hit@1 (file level) | pending |
| Verified-fix pass@1 | pending |
| Cost per triage | pending |
| Benchmark size | pending |

## Quick start

```bash
uv sync
uv run buildsleuth --help
```

## License

MIT
