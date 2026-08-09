"""Tests for verifying a patch by rerunning the build that failed."""

import base64

from buildsleuth.pipeline.verify import VerificationLevel
from buildsleuth.sandbox.bugswarm_runner import (
    APPLY_MARKER,
    PATCH_CRLF,
    PATCH_LF,
    PATCH_PATH,
    ReproductionResult,
    parse_result,
    terminate,
    to_verification,
    verification_script,
)

PATCH = "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n"


class TestVerificationScript:
    def test_carries_the_patch_base64_encoded(self) -> None:
        # Quoting a diff into a shell command corrupts it in ways that look
        # like the model wrote a bad patch.
        script = verification_script(PATCH, "repo")
        encoded = base64.b64encode(PATCH.encode()).decode()
        assert encoded in script

    def test_normalizes_a_patch_with_no_trailing_newline(self) -> None:
        script = verification_script(PATCH.rstrip("\n"), "repo")
        assert base64.b64encode(PATCH.encode()).decode() in script

    def test_reruns_the_original_job(self) -> None:
        assert "run_failed.sh" in verification_script(PATCH, "repo")

    def test_falls_back_to_patch_when_the_checkout_is_not_a_git_repo(self) -> None:
        script = verification_script(PATCH, "repo")
        assert "git apply" in script
        assert "patch -p1" in script


class TestParseResult:
    def test_reads_the_marker_rather_than_the_exit_code(self) -> None:
        # A reproduction script can exit non-zero for reasons unrelated to
        # the patch, and counting those as a failed fix understates the model.
        result = parse_result(91, f"noise\n{APPLY_MARKER}1\n")
        assert result.applied is False

    def test_an_applied_patch_with_a_passing_build(self) -> None:
        result = parse_result(0, f"{APPLY_MARKER}0\nall tests passed\n")
        assert result.applied is True
        assert result.build_passed is True

    def test_an_applied_patch_with_a_failing_build(self) -> None:
        result = parse_result(1, f"{APPLY_MARKER}0\nFAILED test_x\n")
        assert result.applied is True
        assert result.build_passed is False

    def test_missing_marker_counts_as_not_applied(self) -> None:
        assert parse_result(0, "container died early").applied is False


class TestToVerification:
    def test_a_patch_that_does_not_apply_reaches_nothing(self) -> None:
        level = to_verification(ReproductionResult(False, False, "")).level
        assert level is VerificationLevel.NOTHING

    def test_a_patch_that_applies_but_does_not_fix_stops_at_applies(self) -> None:
        # Never LINTS: this path reruns the job and never lints, so reporting
        # LINTS would claim a check that did not run.
        result = to_verification(ReproductionResult(True, False, "boom"))
        assert result.level is VerificationLevel.APPLIES

    def test_a_passing_rerun_reaches_the_top_rung(self) -> None:
        # The script reruns the whole job, so a pass covers the rest of the
        # suite and there is no weaker claim to make.
        result = to_verification(ReproductionResult(True, True, "ok"))
        assert result.level is VerificationLevel.NOTHING_ELSE_BROKE
        assert result.fixed_the_failure

    def test_keeps_a_tail_of_the_output_for_diagnosis(self) -> None:
        result = to_verification(ReproductionResult(True, False, "x" * 5000))
        assert 0 < len(result.stdout_tail) <= 800


class TestApplyOutput:
    def test_keeps_what_the_apply_step_said(self) -> None:
        output = f"noise\nerror: patch failed: src/a.py:12\n{APPLY_MARKER}1\nlater build noise\n"
        assert "patch failed: src/a.py:12" in parse_result(91, output).apply_output

    def test_a_rejection_reports_the_apply_error_not_the_build_tail(self) -> None:
        # A rerun prints thousands of lines afterwards and the reason a patch
        # was rejected is four words near the start.
        output = f"error: corrupt patch at line 9\n{APPLY_MARKER}1\n" + "build noise\n" * 500
        result = to_verification(parse_result(91, output))
        assert "corrupt patch at line 9" in result.stdout_tail
        assert "build noise" not in result.stdout_tail

    def test_excludes_the_marker_itself(self) -> None:
        assert APPLY_MARKER not in parse_result(0, f"a\n{APPLY_MARKER}0\n").apply_output


CRLF_PATCH = "--- a/x.py\r\n+++ b/x.py\r\n@@ -1 +1 @@\r\n-a\r\n+b\r\n"


class TestLineEndings:
    """Two cases had every hunk rejected because carriage returns were
    stripped somewhere between the container and git apply. The repository
    was CRLF throughout, so LF context lines matched nothing."""

    def test_carriage_returns_survive_into_the_script(self) -> None:
        script = verification_script(CRLF_PATCH, "repo")
        decoded = base64.b64decode(script.split("echo ")[1].split(" |")[0]).decode()
        assert "\r\n" in decoded

    def test_terminate_leaves_crlf_alone(self) -> None:
        assert terminate(CRLF_PATCH) == CRLF_PATCH

    def test_terminate_adds_the_newline_git_requires(self) -> None:
        assert terminate("--- a/x").endswith("\n")

    def test_terminate_does_not_double_terminate_a_crlf_patch(self) -> None:
        assert not terminate(CRLF_PATCH).endswith("\r\n\n")

    def test_the_apply_chain_retries_without_whitespace_sensitivity(self) -> None:
        # Otherwise a correct patch is rejected over line endings, which
        # measures the repository rather than the patch.
        script = verification_script(CRLF_PATCH, "repo")
        assert "--ignore-whitespace" in script
        assert script.index("git apply --whitespace=nowarn /tmp") < script.index(
            "--ignore-whitespace"
        )


class TestBothLineEndingConventions:
    """Normalising every patch to LF broke the CRLF repositories; preserving
    line endings broke the LF ones when the model emitted carriage returns.
    Three cases flipped from applying to not applying on that change alone."""

    def test_tries_the_patch_as_written_first(self) -> None:
        script = verification_script(PATCH, "repo")
        assert script.index(f"git apply --whitespace=nowarn {PATCH_PATH}") < script.index(
            f"git apply --whitespace=nowarn {PATCH_LF}"
        )

    def test_tries_an_lf_and_a_crlf_copy(self) -> None:
        script = verification_script(PATCH, "repo")
        assert PATCH_LF in script
        assert PATCH_CRLF in script

    def test_builds_the_crlf_copy_from_the_lf_one(self) -> None:
        # Deriving it from the original would double the carriage returns on
        # a patch that already had them.
        script = verification_script(PATCH, "repo")
        # sed needs the two characters backslash and r, not a carriage return.
        assert rf"sed 's/$/\r/' {PATCH_LF} > {PATCH_CRLF}" in script
