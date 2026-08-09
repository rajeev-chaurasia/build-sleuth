"""Tests for recomputing hunk headers a model got wrong.

The failure being repaired is real and measured: three of eight rejected
patches in the last funnel run failed with `corrupt patch at line N`, which
is git refusing a header whose counts do not match its body.
"""

from buildsleuth.pipeline.patch_repair import needs_repair, repair_patch

WELL_FORMED = "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1,3 +1,3 @@\n a\n-b\n+c\n d\n"


class TestLeavesCorrectPatchesAlone:
    def test_a_well_formed_patch_is_returned_unchanged(self) -> None:
        assert repair_patch(WELL_FORMED) == WELL_FORMED
        assert not needs_repair(WELL_FORMED)

    def test_content_outside_hunks_is_untouched(self) -> None:
        repaired = repair_patch(WELL_FORMED.replace("@@ -1,3 +1,3 @@", "@@ -1,9 +1,9 @@"))
        assert "diff --git a/x.py b/x.py" in repaired
        assert "--- a/x.py" in repaired
        assert "+++ b/x.py" in repaired

    def test_the_hunk_body_is_never_edited(self) -> None:
        broken = WELL_FORMED.replace("@@ -1,3 +1,3 @@", "@@ -1,7 +1,7 @@")
        repaired = repair_patch(broken)
        assert " a\n-b\n+c\n d\n" in repaired


class TestRecomputesCounts:
    def test_fixes_a_header_that_overstates_the_hunk(self) -> None:
        # The exact shape that produced "corrupt patch at line N".
        broken = WELL_FORMED.replace("@@ -1,3 +1,3 @@", "@@ -1,7 +1,5 @@")
        assert "@@ -1,3 +1,3 @@" in repair_patch(broken)
        assert needs_repair(broken)

    def test_fixes_a_header_that_understates_the_hunk(self) -> None:
        broken = WELL_FORMED.replace("@@ -1,3 +1,3 @@", "@@ -1,1 +1,1 @@")
        assert "@@ -1,3 +1,3 @@" in repair_patch(broken)

    def test_keeps_the_start_lines(self) -> None:
        broken = WELL_FORMED.replace("@@ -1,3 +1,3 @@", "@@ -240,9 +260,9 @@")
        assert "@@ -240,3 +260,3 @@" in repair_patch(broken)

    def test_keeps_the_section_heading(self) -> None:
        broken = WELL_FORMED.replace("@@ -1,3 +1,3 @@", "@@ -1,9 +1,9 @@ def thing():")
        assert "@@ -1,3 +1,3 @@ def thing():" in repair_patch(broken)

    def test_counts_additions_and_removals_separately(self) -> None:
        patch = "--- a/x\n+++ b/x\n@@ -1,1 +1,1 @@\n a\n-b\n-c\n+d\n"
        assert "@@ -1,3 +1,2 @@" in repair_patch(patch)

    def test_handles_a_pure_addition(self) -> None:
        patch = "--- a/x\n+++ b/x\n@@ -5,0 +6,0 @@\n+new line\n"
        assert "@@ -0,0 +6,1 @@" in repair_patch(patch)

    def test_repairs_every_hunk_not_only_the_first(self) -> None:
        patch = "--- a/x\n+++ b/x\n@@ -1,9 +1,9 @@\n a\n+b\n@@ -50,9 +51,9 @@\n c\n-d\n"
        repaired = repair_patch(patch)
        assert "@@ -1,1 +1,2 @@" in repaired
        assert "@@ -50,2 +51,1 @@" in repaired


class TestModelHabits:
    def test_a_blank_context_line_counts_as_context(self) -> None:
        # Models drop the leading space on blank lines, which makes the
        # counts disagree even when the header arithmetic was intended.
        patch = "--- a/x\n+++ b/x\n@@ -1,4 +1,4 @@\n a\n\n-b\n+c\n"
        assert "@@ -1,3 +1,3 @@" in repair_patch(patch)

    def test_the_no_newline_marker_is_not_counted(self) -> None:
        patch = "--- a/x\n+++ b/x\n@@ -1,9 +1,9 @@\n-a\n+b\n\\ No newline at end of file\n"
        assert "@@ -1,1 +1,1 @@" in repair_patch(patch)

    def test_a_hunk_ending_at_the_next_file_is_bounded_there(self) -> None:
        patch = (
            "--- a/x\n+++ b/x\n@@ -1,9 +1,9 @@\n-a\n+b\n--- a/y\n+++ b/y\n@@ -1,9 +1,9 @@\n-c\n+d\n"
        )
        repaired = repair_patch(patch)
        assert repaired.count("@@ -1,1 +1,1 @@") == 2

    def test_carriage_returns_survive_the_rewrite(self) -> None:
        # A repaired patch still has to apply to a CRLF checkout.
        patch = "--- a/x\r\n+++ b/x\r\n@@ -1,9 +1,9 @@\r\n-a\r\n+b\r\n"
        repaired = repair_patch(patch)
        assert "@@ -1,1 +1,1 @@\r\n" in repaired
        assert "-a\r\n" in repaired


class TestDegradesQuietly:
    def test_text_that_is_not_a_patch_is_returned_unchanged(self) -> None:
        assert repair_patch("this is not a diff\n") == "this is not a diff\n"

    def test_an_empty_patch_is_returned_unchanged(self) -> None:
        assert repair_patch("") == ""

    def test_a_header_with_no_body_becomes_an_empty_hunk(self) -> None:
        assert "@@ -0,0 +0,0 @@" in repair_patch("--- a/x\n+++ b/x\n@@ -1,3 +1,3 @@\n")
