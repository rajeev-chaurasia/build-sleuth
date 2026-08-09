"""Tests for verifying a patch by rerunning the build that failed."""

import base64

from buildsleuth.pipeline.verify import VerificationLevel
from buildsleuth.sandbox.bugswarm_runner import (
    APPLY_MARKER,
    ReproductionResult,
    parse_result,
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
