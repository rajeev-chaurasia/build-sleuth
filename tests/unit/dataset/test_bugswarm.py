"""Tests for BugSwarm artifact extraction.

Each of these covers a way the extractor has actually been wrong. Two of them
produced cases that looked fine and carried ground truth describing a
virtualenv, which is worse than an import that fails loudly.
"""

import urllib.request

import pytest

from buildsleuth.dataset import bugswarm
from buildsleuth.dataset.bugswarm import (
    CACHE_DIR,
    Artifact,
    ArtifactError,
    build_artifact,
    extract_script,
    fetch_metadata,
    parse_metadata,
    parse_sections,
    parse_tag,
    repo_name_from_slug,
    verification_commands,
)

TRAVIS_TAG = "Abjad-abjad-289716771"
ACTIONS_TAG = "hhyo-Archery-75136021799"


def _output(log: str = "boom", diff: str = "", files: str = "", layout: str = "") -> str:
    return (
        f"=====BUILDSLEUTH_LOG=====\n{log}\n"
        f"=====BUILDSLEUTH_DIFF=====\n{diff}\n"
        f"=====BUILDSLEUTH_FILES=====\n{files}\n"
        f"=====BUILDSLEUTH_LAYOUT=====\n{layout}\n"
    )


class TestParseTag:
    def test_splits_slug_from_job_id(self) -> None:
        assert parse_tag(TRAVIS_TAG) == ("Abjad-abjad", 289716771)

    def test_accepts_the_longer_actions_era_job_ids(self) -> None:
        assert parse_tag(ACTIONS_TAG) == ("hhyo-Archery", 75136021799)

    def test_rejects_a_tag_with_no_job_id(self) -> None:
        with pytest.raises(ArtifactError):
            parse_tag("owner-repo")


class TestRepoNameFromSlug:
    @pytest.mark.parametrize(
        ("slug", "expected"),
        [
            ("hhyo-Archery", "Archery"),
            ("dalibo-pg_activity", "pg_activity"),
            ("eventlet-eventlet", "eventlet"),
            ("Abjad-abjad", "abjad"),
        ],
    )
    def test_takes_the_repository_half(self, slug: str, expected: str) -> None:
        assert repo_name_from_slug(slug) == expected

    def test_falls_back_to_the_whole_slug_when_there_is_no_owner(self) -> None:
        assert repo_name_from_slug("solo") == "solo"


class TestExtractScript:
    def test_finds_the_checkout_by_name_rather_than_taking_the_first_directory(self) -> None:
        script = extract_script(75136021799, "Archery")
        assert "-name Archery" in script
        # Taking the first directory is what selected the build cache.
        assert 'ls "$FAILED" 2>/dev/null | head -1' not in script

    def test_handles_the_checkout_being_nested_under_the_owner(self) -> None:
        # These images use failed/<owner>/<repo>, and resolving to <owner>
        # prefixes every culprit path with the repository directory name.
        script = extract_script(1, "hhyo/Archery")
        assert "PROJ=hhyo/Archery" in script
        assert "PROJ=Archery" in script

    def test_prefers_the_full_path_when_owner_and_repo_match(self) -> None:
        # numpy/numpy and scikit-learn/scikit-learn are both in the catalogue.
        # Searching by name alone finds the owner directory first, which is
        # what prefixed five culprit paths with the repository name.
        script = extract_script(1, "numpy/numpy")
        full = script.index("PROJ=numpy/numpy")
        bare = script.index("PROJ=numpy\n")
        assert full < bare

    def test_skips_the_build_cache_when_falling_back(self) -> None:
        assert f"! -name {CACHE_DIR}" in extract_script(1, "missing")

    def test_finds_the_log_instead_of_assuming_the_travis_path(self) -> None:
        script = extract_script(75136021799, "Archery")
        assert '75136021799-orig.log"' in script
        assert "find /" in script

    def test_excludes_rebuilt_environments_from_the_diff(self) -> None:
        script = extract_script(1, "repo")
        for excluded in ("-x env", "-x .git", "-x __pycache__"):
            assert excluded in script


class TestBuildArtifact:
    def test_reads_the_three_sections(self) -> None:
        artifact = build_artifact(
            TRAVIS_TAG,
            _output(log="E   assert 1 == 2", diff="--- a\n+++ b", files="src/a.py\nsrc/b.py"),
        )
        assert isinstance(artifact, Artifact)
        assert artifact.slug == "Abjad-abjad"
        assert artifact.failing_log == "E   assert 1 == 2"
        assert artifact.culprit_files == ["src/a.py", "src/b.py"]

    def test_rejects_an_artifact_with_no_log(self) -> None:
        with pytest.raises(ArtifactError, match="no original build log"):
            build_artifact(TRAVIS_TAG, _output(log="no log"))

    def test_reports_the_layout_so_a_missing_log_is_diagnosable(self) -> None:
        with pytest.raises(ArtifactError, match="root=/home/github/build"):
            build_artifact(TRAVIS_TAG, _output(log="no log", layout="root=/home/github/build"))

    def test_rejects_a_diff_of_the_build_cache(self) -> None:
        # This is the one that passed silently and wrote three cases whose
        # culprit files were virtualenv binaries.
        with pytest.raises(ArtifactError, match="build cache"):
            build_artifact(
                ACTIONS_TAG,
                _output(
                    diff="diff -ruN .../cacher/env/bin/activate ...",
                    files="env/bin/activate",
                    layout=f"root=/home/github/build project={CACHE_DIR} log=/x.log",
                ),
            )

    def test_accepts_a_real_checkout(self) -> None:
        artifact = build_artifact(
            ACTIONS_TAG,
            _output(files="sql/utils/tests.py", layout="root=/home/github/build project=Archery"),
        )
        assert artifact.culprit_files == ["sql/utils/tests.py"]


class TestMetadata:
    def test_reads_the_commit_and_failing_tests(self) -> None:
        meta = parse_metadata(
            {
                "repo": "scikit-learn/scikit-learn",
                "test_framework": "unittest",
                "metrics": {"num_of_changed_files": 1},
                "failed_job": {
                    "trigger_sha": "abc123",
                    "failed_tests": "test_a (mod.A)#test_b (mod.B)",
                },
            }
        )
        assert meta.head_sha == "abc123"
        assert meta.failing_tests == ["test_a (mod.A)", "test_b (mod.B)"]
        assert meta.repo_name == "scikit-learn"

    def test_survives_an_entry_with_no_failing_tests(self) -> None:
        meta = parse_metadata({"repo": "a/b", "failed_job": {"trigger_sha": "s"}})
        assert meta.failing_tests == []

    def test_falls_back_when_the_commit_is_blank(self) -> None:
        # The catalogue stores an empty string rather than omitting the key.
        assert parse_metadata({"repo": "a/b", "failed_job": {"trigger_sha": ""}}).head_sha == (
            "unknown"
        )

    def test_unreachable_catalogue_returns_none_rather_than_raising(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # An import should degrade to weaker ground truth, not fail outright.
        def refuse(*args: object, **kwargs: object) -> None:
            raise OSError("no route to host")

        monkeypatch.setattr(urllib.request, "urlopen", refuse)
        assert fetch_metadata(ACTIONS_TAG) is None

    def test_an_unrecognised_payload_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(bugswarm, "_load_json", lambda url, timeout: {"_error": "not found"})
        assert fetch_metadata(ACTIONS_TAG) is None


class TestParseSections:
    def test_ignores_output_before_the_first_separator(self) -> None:
        sections = parse_sections("noise from the shell\n" + _output(log="real"))
        assert sections["log"].strip() == "real"


def test_verification_reruns_the_failing_build() -> None:
    assert verification_commands(TRAVIS_TAG) == ["bash /usr/local/bin/run_failed.sh"]
