"""Tests for BugSwarm candidate selection.

Selection ran against the catalogue alone, so a second run reproposed the
artifacts the first run had already imported, and reproposed sibling matrix
jobs of those same bugs under different image tags. These cover the dataset
being read back in as the starting point.
"""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "select_bugswarm.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("select_bugswarm", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


select = _load_script()


def _write_case(
    cases_dir: Path,
    case_id: str,
    *,
    repo: str = "acme/widget",
    tag: str = "acme-widget-1",
    failing_tests: list[str] | None = None,
) -> None:
    payload = {
        "case_id": case_id,
        "inputs": {"repo": repo},
        "ground_truth": {"failing_tests": failing_tests or ["test_a (mod.A)"]},
        "verification": {"docker_image": f"bugswarm/cached-images:{tag}"},
    }
    case_dir = cases_dir / case_id
    case_dir.mkdir(parents=True)
    (case_dir / "case.json").write_text(json.dumps(payload), encoding="utf-8")


def _artifact(tag: str, repo: str, failed_tests: str) -> dict[str, Any]:
    return {
        "image_tag": tag,
        "repo": repo,
        "test_framework": "pytest",
        "failed_job": {"failed_tests": failed_tests},
    }


@pytest.fixture
def cases_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "bugswarm"
    directory.mkdir()
    return directory


def _serve(monkeypatch: pytest.MonkeyPatch, *pages: list[dict[str, Any]]) -> None:
    def fetch_page(page: int) -> list[dict[str, Any]]:
        return pages[page - 1] if page <= len(pages) else []

    monkeypatch.setattr(select, "fetch_page", fetch_page)


class TestReadExisting:
    def test_reads_the_tag_out_of_the_image_reference(self, cases_dir: Path) -> None:
        _write_case(cases_dir, "bs-0010", tag="bwhmather-verktyg-109227527")
        assert select.read_existing(cases_dir).tags == {"bwhmather-verktyg-109227527"}

    def test_counts_how_many_cases_each_repository_already_has(self, cases_dir: Path) -> None:
        _write_case(cases_dir, "bs-0016", repo="web2py/web2py", tag="web2py-web2py-1")
        _write_case(cases_dir, "bs-0017", repo="web2py/web2py", tag="web2py-web2py-2")
        assert select.read_existing(cases_dir).repo_counts == {"web2py/web2py": 2}

    def test_records_the_bug_each_case_covers(self, cases_dir: Path) -> None:
        _write_case(cases_dir, "bs-0010", failing_tests=["test_b", "test_a"])
        assert select.read_existing(cases_dir).bugs == {("acme/widget", ("test_a", "test_b"))}

    def test_an_absent_dataset_is_not_an_error(self, tmp_path: Path) -> None:
        # The script has to run on a checkout that has imported nothing yet.
        existing = select.read_existing(tmp_path / "nothing-here")
        assert existing.tags == set()

    def test_a_case_with_no_repository_contributes_no_counts(self, cases_dir: Path) -> None:
        _write_case(cases_dir, "bs-0001", repo="", tag="Abjad-abjad-289716771")
        existing = select.read_existing(cases_dir)
        assert existing.tags == {"Abjad-abjad-289716771"}
        assert existing.repo_counts == {}


class TestBugKey:
    def test_is_independent_of_the_order_the_tests_are_listed_in(self) -> None:
        assert select.bug_key("a/b", "t2#t1") == select.bug_key("a/b", "t1#t2")

    def test_ignores_empty_names_from_a_trailing_separator(self) -> None:
        assert select.bug_key("a/b", "t1#") == ("a/b", ("t1",))


class TestCandidates:
    def test_skips_a_tag_already_imported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _serve(
            monkeypatch,
            [
                _artifact("acme-widget-1", "acme/widget", "test_a"),
                _artifact("acme-gadget-9", "acme/gadget", "test_z"),
            ],
        )
        existing = select.Existing(tags={"acme-widget-1"}, bugs=set(), repo_counts={})
        chosen = list(select.candidates(limit=5, per_repo=2, existing=existing))
        assert [artifact["image_tag"] for artifact in chosen] == ["acme-gadget-9"]

    def test_skips_a_sibling_matrix_job_of_a_bug_already_imported(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A different job id for the same failure passes the tag check, so
        # excluding tags alone still readmits the bug.
        _serve(monkeypatch, [_artifact("acme-widget-2", "acme/widget", "test_a")])
        existing = select.Existing(
            tags={"acme-widget-1"}, bugs={select.bug_key("acme/widget", "test_a")}, repo_counts={}
        )
        assert list(select.candidates(limit=5, per_repo=2, existing=existing)) == []

    def test_counts_existing_cases_against_the_per_repository_cap(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _serve(
            monkeypatch,
            [
                _artifact("acme-widget-3", "acme/widget", "test_new"),
                _artifact("acme-gadget-9", "acme/gadget", "test_z"),
            ],
        )
        existing = select.Existing(tags=set(), bugs=set(), repo_counts={"acme/widget": 2})
        chosen = list(select.candidates(limit=5, per_repo=2, existing=existing))
        assert [artifact["repo"] for artifact in chosen] == ["acme/gadget"]

    def test_selects_everything_new_when_the_dataset_is_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _serve(monkeypatch, [_artifact("acme-widget-1", "acme/widget", "test_a")])
        assert len(list(select.candidates(limit=5, per_repo=2))) == 1

    def test_still_drops_artifacts_with_no_named_failing_tests(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _serve(monkeypatch, [_artifact("acme-widget-1", "acme/widget", "")])
        assert list(select.candidates(limit=5, per_repo=2)) == []

    def test_stops_at_the_first_empty_page(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _serve(monkeypatch, [], [_artifact("acme-widget-1", "acme/widget", "test_a")])
        assert list(select.candidates(limit=5, per_repo=2)) == []


def test_the_dataset_directory_the_script_defaults_to_exists() -> None:
    # A wrong default would silently exclude nothing and reselect everything.
    assert (select.DEFAULT_CASES_DIR / "bs-0010" / "case.json").is_file()
