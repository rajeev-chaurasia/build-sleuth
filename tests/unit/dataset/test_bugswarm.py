"""Tests for BugSwarm artifact extraction.

Each of these covers a way the extractor has actually been wrong. Two of them
produced cases that looked fine and carried ground truth describing a
virtualenv, which is worse than an import that fails loudly.
"""

import pytest

from buildsleuth.dataset.bugswarm import (
    CACHE_DIR,
    Artifact,
    ArtifactError,
    build_artifact,
    extract_script,
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
    def test_names_the_checkout_rather_than_taking_the_first_directory(self) -> None:
        script = extract_script(75136021799, "Archery")
        assert "PROJ=Archery" in script
        # Taking the first directory is what selected the build cache.
        assert 'ls "$FAILED" 2>/dev/null | head -1' not in script

    def test_skips_the_build_cache_when_falling_back(self) -> None:
        assert f"grep -v {CACHE_DIR}" in extract_script(1, "missing")

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


class TestParseSections:
    def test_ignores_output_before_the_first_separator(self) -> None:
        sections = parse_sections("noise from the shell\n" + _output(log="real"))
        assert sections["log"].strip() == "real"


def test_verification_reruns_the_failing_build() -> None:
    assert verification_commands(TRAVIS_TAG) == ["bash /usr/local/bin/run_failed.sh"]
