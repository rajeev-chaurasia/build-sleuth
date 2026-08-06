"""Evidence tools: line-addressed log reads, diff listing, and log search."""

from buildsleuth.tools.evidence import (
    MAX_DIFF_CHARS_PER_FILE,
    MAX_LOG_LINES_PER_CALL,
    NOT_FOUND,
    Evidence,
)

TEN_LINE_LOG = "\n".join(f"line {number}" for number in range(1, 11))
LONG_LOG = "\n".join(f"line {number}" for number in range(1, 501))

DIFF = """diff --git a/src/app.py b/src/app.py
index 1111111..2222222 100644
--- a/src/app.py
+++ b/src/app.py
@@ -1,2 +1,2 @@
-old_call()
+new_call()
diff --git a/tests/test_app.py b/tests/test_app.py
index 3333333..4444444 100644
--- a/tests/test_app.py
+++ b/tests/test_app.py
@@ -1 +1 @@
-assert old_call()
+assert new_call()
"""

SEARCH_LOG = "\n".join(
    [
        "starting build",
        "ERROR: first problem",
        "still going",
        "ERROR: second problem",
        "ERROR: third problem",
        "done",
    ]
)


def test_read_log_range_slices_inclusively_and_one_based() -> None:
    body = Evidence(log_text=TEN_LINE_LOG).read_log_range(2, 4)
    assert body == "[lines 2-4 of 10]\nline 2\nline 3\nline 4"


def test_read_log_range_header_states_the_true_total() -> None:
    assert Evidence(log_text=TEN_LINE_LOG).read_log_range(1, 1).startswith("[lines 1-1 of 10]")


def test_start_past_the_end_reports_the_real_range() -> None:
    """A model asking past the end needs the bounds back, not an empty answer."""
    assert Evidence(log_text=TEN_LINE_LOG).read_log_range(11, 20) == (
        f"{NOT_FOUND}: the log has lines 1 to 10"
    )


def test_start_below_one_reports_the_real_range() -> None:
    assert Evidence(log_text=TEN_LINE_LOG).read_log_range(0, 5) == (
        f"{NOT_FOUND}: the log has lines 1 to 10"
    )


def test_end_past_the_last_line_clamps_without_claiming_truncation() -> None:
    """Nothing was dropped, so saying truncated would send the model looking for more."""
    body = Evidence(log_text=TEN_LINE_LOG).read_log_range(9, 50)
    assert body.startswith("[lines 9-10 of 10]")
    assert "truncated" not in body
    assert body.splitlines()[1:] == ["line 9", "line 10"]


def test_request_wider_than_the_cap_is_capped_and_marked_truncated() -> None:
    body = Evidence(log_text=LONG_LOG).read_log_range(1, 500)
    header, *lines = body.splitlines()
    assert header == f"[lines 1-{MAX_LOG_LINES_PER_CALL} of 500 (truncated)]"
    assert len(lines) == MAX_LOG_LINES_PER_CALL
    assert lines[-1] == f"line {MAX_LOG_LINES_PER_CALL}"


def test_request_exactly_at_the_cap_is_not_marked_truncated() -> None:
    body = Evidence(log_text=LONG_LOG).read_log_range(1, MAX_LOG_LINES_PER_CALL)
    assert body.startswith(f"[lines 1-{MAX_LOG_LINES_PER_CALL} of 500]")


def test_diff_paths_preserve_header_order() -> None:
    assert Evidence(log_text="boom", diff_text=DIFF).diff_paths() == [
        "src/app.py",
        "tests/test_app.py",
    ]


def test_diff_paths_are_deduplicated() -> None:
    repeated = DIFF + "diff --git a/src/app.py b/src/app.py\n@@ -9 +9 @@\n+again\n"
    assert Evidence(log_text="boom", diff_text=repeated).diff_paths() == [
        "src/app.py",
        "tests/test_app.py",
    ]


def test_a_rename_reports_the_new_path() -> None:
    """The b-side is the name the culprit file has now, which is the one to open."""
    renamed = "diff --git a/src/old_name.py b/src/new_name.py\n@@ -1 +1 @@\n-x\n+y\n"
    assert Evidence(log_text="boom", diff_text=renamed).diff_paths() == ["src/new_name.py"]


def test_list_diff_files_renders_one_path_per_line() -> None:
    assert Evidence(log_text="boom", diff_text=DIFF).list_diff_files() == (
        "src/app.py\ntests/test_app.py"
    )


def test_list_diff_files_without_a_diff_says_so() -> None:
    assert Evidence(log_text="boom").list_diff_files() == f"{NOT_FOUND}: this case carries no diff"


def test_diff_paths_without_a_diff_is_empty() -> None:
    assert Evidence(log_text="boom").diff_paths() == []


def test_read_diff_for_file_stops_before_the_next_header() -> None:
    hunk = Evidence(log_text="boom", diff_text=DIFF).read_diff_for_file("src/app.py")
    assert hunk.startswith("diff --git a/src/app.py b/src/app.py")
    assert "+new_call()" in hunk
    assert "test_app.py" not in hunk


def test_read_diff_for_the_last_file_runs_to_the_end() -> None:
    hunk = Evidence(log_text="boom", diff_text=DIFF).read_diff_for_file("tests/test_app.py")
    assert hunk.startswith("diff --git a/tests/test_app.py")
    assert "+assert new_call()" in hunk


def test_read_diff_for_an_absent_path_names_the_path() -> None:
    assert Evidence(log_text="boom", diff_text=DIFF).read_diff_for_file("src/other.py") == (
        f"{NOT_FOUND}: src/other.py is not in the diff"
    )


def test_read_diff_without_a_diff_says_so() -> None:
    assert Evidence(log_text="boom").read_diff_for_file("src/app.py") == (
        f"{NOT_FOUND}: this case carries no diff"
    )


def test_an_oversized_hunk_is_truncated_with_a_marker() -> None:
    header = "diff --git a/src/big.py b/src/big.py\n"
    big = header + "\n".join(f"+padding line {number}" for number in range(MAX_DIFF_CHARS_PER_FILE))
    hunk = Evidence(log_text="boom", diff_text=big).read_diff_for_file("src/big.py")
    assert hunk.endswith("\n...[hunk truncated]")
    assert hunk.startswith(header)
    assert len(hunk) == MAX_DIFF_CHARS_PER_FILE + len("\n...[hunk truncated]")


def test_search_log_returns_one_based_line_numbers() -> None:
    assert Evidence(log_text=SEARCH_LOG).search_log("ERROR") == (
        "2: ERROR: first problem\n4: ERROR: second problem\n5: ERROR: third problem"
    )


def test_search_log_respects_max_hits_and_counts_the_rest() -> None:
    body = Evidence(log_text=SEARCH_LOG).search_log("ERROR", max_hits=2)
    assert body == "2: ERROR: first problem\n4: ERROR: second problem\n...1 more matches"


def test_search_log_without_a_match_says_what_was_searched() -> None:
    assert Evidence(log_text=SEARCH_LOG).search_log("SegFault") == (
        f"{NOT_FOUND}: no line contains 'SegFault'"
    )


def test_search_log_trims_long_lines() -> None:
    """One pathological line must not flood the context window."""
    log = "prefix ERROR " + "x" * 500
    hit = Evidence(log_text=log).search_log("ERROR")
    number, _, text = hit.partition(": ")
    assert number == "1"
    assert len(text) == 160


def test_an_empty_log_never_raises() -> None:
    evidence = Evidence(log_text="")
    assert evidence.read_log_range(1, 10) == "[lines 1-1 of 1]\n"
    assert evidence.search_log("anything").startswith(NOT_FOUND)
    assert evidence.list_diff_files().startswith(NOT_FOUND)
    assert evidence.diff_paths() == []
    assert evidence.read_diff_for_file("src/app.py").startswith(NOT_FOUND)


def test_a_single_line_log_never_raises() -> None:
    evidence = Evidence(log_text="only line", diff_text=DIFF)
    assert evidence.read_log_range(1, 1) == "[lines 1-1 of 1]\nonly line"
    assert evidence.read_log_range(2, 3).startswith(NOT_FOUND)
    assert evidence.search_log("only") == "1: only line"
    assert evidence.list_diff_files() == "src/app.py\ntests/test_app.py"
