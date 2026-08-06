"""Validate the curated dataset and refresh its manifest.

Run it plain to rebuild dataset/manifest.json, or with --check in CI to fail
when the committed manifest no longer matches what is on disk.
"""

import argparse
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from rich.console import Console
from rich.table import Table

from buildsleuth.dataset.loader import DatasetError, load_cases
from buildsleuth.dataset.manifest import build_manifest, read_manifest, write_manifest
from buildsleuth.models.case import TriageCase

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET_DIR = REPO_ROOT / "dataset"
EXIT_OK = 0
EXIT_FAILED = 1

console = Console()


def _counts_table(title: str, key: str, counts: Counter[str]) -> Table:
    table = Table(title=title, title_justify="left")
    table.add_column(key)
    table.add_column("cases", justify="right")
    for name, count in sorted(counts.items()):
        table.add_row(name, str(count))
    return table


def print_summary(cases: list[TriageCase], dataset_hash: str) -> None:
    """Print counts per failure class and per source, plus the honesty counters."""
    console.print(
        _counts_table(
            "By failure class",
            "failure_class",
            Counter(case.ground_truth.failure_class.value for case in cases),
        )
    )
    console.print(
        _counts_table(
            "By source",
            "source",
            Counter(case.provenance.source.value for case in cases),
        )
    )
    verified = sum(1 for case in cases if case.provenance.verified_by_human)
    smoke = sum(1 for case in cases if case.is_smoke)
    console.print(
        f"{len(cases)} case(s) | {verified} verified by human | {smoke} smoke-tagged"
        f" | dataset hash {dataset_hash}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET_DIR,
        help="Dataset directory holding cases/ and manifest.json.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Do not write; exit non-zero when the on-disk manifest is stale.",
    )
    args = parser.parse_args(argv)
    dataset_dir: Path = args.dataset

    try:
        cases = load_cases(dataset_dir)
        rebuilt = build_manifest(dataset_dir)
        on_disk = read_manifest(dataset_dir) if args.check else None
    except DatasetError as exc:
        console.print(f"[red]dataset invalid:[/red] {exc}")
        return EXIT_FAILED

    print_summary(cases, rebuilt.dataset_hash)

    if on_disk is None:
        path = write_manifest(dataset_dir, rebuilt)
        console.print(f"[green]manifest written to[/green] {path}")
        return EXIT_OK

    if on_disk != rebuilt:
        console.print(
            f"[red]manifest is stale:[/red] on disk {on_disk.dataset_hash}"
            f" ({on_disk.case_count} cases), rebuilt {rebuilt.dataset_hash}"
            f" ({rebuilt.case_count} cases). Run scripts/validate_dataset.py to refresh it."
        )
        return EXIT_FAILED

    console.print("[green]manifest is up to date[/green]")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
