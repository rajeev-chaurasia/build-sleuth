"""Command line entry point.

Subcommands are added phase by phase. Each maps to one pipeline stage so
every stage stays independently runnable and debuggable.
"""

import io
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from buildsleuth import __version__
from buildsleuth.config import load_settings

# CI logs are UTF-8 but Windows consoles and pipes default to a legacy code
# page, so force UTF-8 output rather than crash on the first non-ASCII line.
for _stream in (sys.stdout, sys.stderr):
    if isinstance(_stream, io.TextIOWrapper) and _stream.encoding.lower() not in ("utf-8", "utf8"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

app = typer.Typer(
    name="buildsleuth",
    add_completion=False,
    no_args_is_help=True,
)

console = Console()

SNAPSHOT_FILE = "snapshot.json"
LOG_FILE = "log.txt"
DIFF_FILE = "diff.patch"
DATASET_DIR = Path("dataset")
RESULTS_DIR = Path("results")


@app.callback()
def main() -> None:
    """Triage failing CI runs: classify, localize, and propose verified fixes."""


@app.command()
def version() -> None:
    """Print the installed version."""
    typer.echo(f"buildsleuth {__version__}")


@app.command()
def fetch(
    run_url: str,
    out: Annotated[Path | None, typer.Option(help="Output directory for the snapshot.")] = None,
) -> None:
    """Snapshot a failing workflow run for offline triage."""
    from buildsleuth.pipeline.ingest import ingest
    from buildsleuth.providers.github.client import GitHubClient
    from buildsleuth.providers.github.provider import GitHubProvider

    settings = load_settings()
    token = settings.github_token.get_secret_value() if settings.github_token else None
    provider = GitHubProvider(GitHubClient(token=token))

    snapshot = ingest(run_url, provider)

    ref = snapshot.run.ref
    out_dir = out or Path("snapshots") / f"{ref.repo.replace('/', '-')}-{ref.run_id}"
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / SNAPSHOT_FILE).write_text(snapshot.model_dump_json(indent=2), encoding="utf-8")
    (out_dir / LOG_FILE).write_text(snapshot.log_text, encoding="utf-8")
    if snapshot.diff_text is not None:
        (out_dir / DIFF_FILE).write_text(snapshot.diff_text, encoding="utf-8")

    console.print(f"[green]snapshot written to[/green] {out_dir}")
    console.print(
        f"run: {snapshot.run.workflow_name} | failed job: {snapshot.failed_job.name}"
        f" | failed step: {snapshot.failed_step.name if snapshot.failed_step else 'unknown'}"
        f" | pr: {snapshot.pr_number if snapshot.pr_number is not None else 'none'}"
    )


@app.command()
def condense(
    log_file: Path,
    max_chars: Annotated[int | None, typer.Option(help="Character budget for the output.")] = None,
) -> None:
    """Condense a CI log to the excerpts most likely to explain the failure."""
    from buildsleuth.condense.clean import clean_log
    from buildsleuth.condense.router import DEFAULT_MAX_CHARS
    from buildsleuth.condense.router import condense as condense_log

    cleaned = clean_log(log_file.read_text(encoding="utf-8", errors="replace"))
    result = condense_log(cleaned, max_chars=max_chars or DEFAULT_MAX_CHARS)

    console.print(
        f"[bold]{result.strategy}[/bold] | {len(result.excerpts)} excerpt(s)"
        f" from {result.total_lines} lines"
    )
    # rich degrades unencodable characters gracefully on legacy Windows consoles
    console.print(result.as_text(), markup=False, highlight=False)


@app.command()
def dataset(
    check: Annotated[bool, typer.Option(help="Fail if the manifest is stale.")] = False,
) -> None:
    """Validate the case dataset and refresh its manifest."""
    from buildsleuth.dataset.manifest import build_manifest, read_manifest, write_manifest

    manifest = build_manifest(DATASET_DIR)
    if check:
        if read_manifest(DATASET_DIR) != manifest:
            console.print("[red]manifest is stale, rerun without --check[/red]")
            raise typer.Exit(code=1)
        console.print("[green]manifest is up to date[/green]")
        return

    write_manifest(DATASET_DIR, manifest)
    console.print(f"[green]{manifest.case_count} case(s)[/green] | hash {manifest.dataset_hash}")
