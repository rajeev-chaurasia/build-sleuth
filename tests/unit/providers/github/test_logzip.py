import io
import zipfile

from buildsleuth.providers.github.logzip import extract_job_log, extract_step_log


def make_zip(entries: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, text in entries.items():
            archive.writestr(name, text)
    return buffer.getvalue()


SIMPLE_ZIP = make_zip(
    {
        "1_build.txt": "combined build log",
        "build/1_Set up job.txt": "step one",
        "build/2_Run tests.txt": "step two",
        "2_lint.txt": "combined lint log",
        "lint/1_Set up job.txt": "lint step one",
    }
)


def test_extract_job_log_normal_name() -> None:
    assert extract_job_log(SIMPLE_ZIP, "build") == "combined build log"
    assert extract_job_log(SIMPLE_ZIP, "lint") == "combined lint log"


def test_extract_job_log_missing_job() -> None:
    assert extract_job_log(SIMPLE_ZIP, "deploy") is None


def test_extract_step_log_normal_name() -> None:
    assert extract_step_log(SIMPLE_ZIP, "build", 2) == "step two"
    assert extract_step_log(SIMPLE_ZIP, "lint", 1) == "lint step one"


def test_extract_step_log_missing_step() -> None:
    assert extract_step_log(SIMPLE_ZIP, "build", 9) is None


def test_extract_step_log_missing_job() -> None:
    assert extract_step_log(SIMPLE_ZIP, "deploy", 1) is None


def test_slashes_in_job_name_become_spaces() -> None:
    archive = make_zip(
        {
            "1_test   unit.txt": "unit job log",
            "test   unit/3_Run pytest.txt": "pytest output",
        }
    )
    assert extract_job_log(archive, "test / unit") == "unit job log"
    assert extract_step_log(archive, "test / unit", 3) == "pytest output"


def test_colons_in_job_name_become_spaces() -> None:
    archive = make_zip(
        {
            "1_deploy  prod.txt": "deploy log",
            "deploy  prod/2_Push.txt": "push output",
        }
    )
    assert extract_job_log(archive, "deploy: prod") == "deploy log"
    assert extract_step_log(archive, "deploy: prod", 2) == "push output"


def test_truncated_names_match_by_prefix() -> None:
    """GitHub truncates zip entry names near 90 characters, so long names match by prefix."""
    stored = "matrix job (ubuntu-latest, python 3.12, integration suite) shard one of four runners"
    full_name = f"{stored} with a tail that gets cut"
    archive = make_zip(
        {
            f"1_{stored}.txt": "truncated job log",
            f"{stored}/4_Compile.txt": "compile output",
        }
    )
    assert extract_job_log(archive, full_name) == "truncated job log"
    assert extract_step_log(archive, full_name, 4) == "compile output"


def test_longest_match_wins_over_shorter_prefix() -> None:
    archive = make_zip(
        {
            "1_build.txt": "just build",
            "2_build and lint.txt": "build and lint",
        }
    )
    assert extract_job_log(archive, "build and lint") == "build and lint"
    assert extract_job_log(archive, "build") == "just build"


def test_step_number_matching_is_exact() -> None:
    archive = make_zip(
        {
            "build/1_First.txt": "first",
            "build/12_Twelfth.txt": "twelfth",
        }
    )
    assert extract_step_log(archive, "build", 1) == "first"
    assert extract_step_log(archive, "build", 12) == "twelfth"


def test_empty_zip_returns_none() -> None:
    archive = make_zip({})
    assert extract_job_log(archive, "build") is None
    assert extract_step_log(archive, "build", 1) is None
