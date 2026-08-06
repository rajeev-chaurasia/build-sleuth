"""Mining signals: hints for a labeller, never labels in their own right."""

import pytest

from buildsleuth.dataset.mining import (
    Candidate,
    Signal,
    build_candidate,
    classify_signal,
    diversify,
    is_setup_step,
)
from buildsleuth.models.run import Conclusion, Job, RunRef, Step, WorkflowRun
from buildsleuth.models.taxonomy import FailureClass


def _run(repo: str = "octo/demo", attempt: int = 1) -> WorkflowRun:
    return WorkflowRun(
        ref=RunRef(repo=repo, run_id=1),
        workflow_name="CI",
        run_attempt=attempt,
        head_sha="abc123",
        conclusion=Conclusion.FAILURE,
        event="pull_request",
        html_url="https://github.com/octo/demo/actions/runs/1",
    )


def _job(step_name: str, conclusion: Conclusion = Conclusion.FAILURE) -> Job:
    return Job(
        job_id=1,
        name="build",
        conclusion=conclusion,
        steps=[Step(number=1, name=step_name, conclusion=Conclusion.FAILURE)],
    )


@pytest.mark.parametrize(
    "step_name",
    ["Checkout", "Set up Python 3.12", "setup-node", "Install dependencies", "Restore cache"],
)
def test_environment_steps_are_recognised_as_setup(step_name: str) -> None:
    assert is_setup_step(step_name) is True


@pytest.mark.parametrize("step_name", ["Run pytest", "Build wheels", "cargo test", "Lint"])
def test_project_steps_are_not_setup(step_name: str) -> None:
    assert is_setup_step(step_name) is False


def test_a_passing_rerun_of_the_same_commit_suggests_a_flake() -> None:
    signal = classify_signal(_run(attempt=2), _job("Run pytest"), passed_on_rerun=True)
    assert signal is Signal.RERUN_PASSED_SAME_COMMIT


def test_a_first_attempt_cannot_have_passed_on_rerun() -> None:
    """The signal needs a later attempt to exist, so attempt one falls through."""
    signal = classify_signal(_run(attempt=1), _job("Run pytest"), passed_on_rerun=True)
    assert signal is Signal.FAILED_IN_PROJECT_STEP


@pytest.mark.parametrize("conclusion", [Conclusion.CANCELLED, Conclusion.TIMED_OUT])
def test_cancellation_and_timeout_suggest_infrastructure(conclusion: Conclusion) -> None:
    assert classify_signal(_run(), _job("Run pytest", conclusion)) is Signal.CANCELLED_OR_TIMED_OUT


def test_failing_before_the_project_runs_suggests_infrastructure() -> None:
    assert classify_signal(_run(), _job("Install dependencies")) is Signal.FAILED_IN_SETUP_STEP


def test_failing_in_the_project_step_suggests_a_code_change() -> None:
    assert classify_signal(_run(), _job("Run pytest")) is Signal.FAILED_IN_PROJECT_STEP


def test_a_job_with_no_failed_step_yields_no_signal() -> None:
    job = Job(job_id=1, name="build", conclusion=Conclusion.FAILURE, steps=[])
    assert classify_signal(_run(), job) is Signal.NO_SIGNAL


def test_every_signal_carries_a_rationale() -> None:
    """A hint nobody can audit is worse than no hint."""
    for signal in Signal:
        candidate = Candidate(
            repo="octo/demo",
            run_id=1,
            run_attempt=1,
            head_sha="abc",
            workflow_name="CI",
            failed_job="build",
            failed_step="test",
            html_url="https://example.com",
            signal=signal,
        )
        assert candidate.rationale


def test_no_signal_suggests_no_class() -> None:
    """Silence is the honest answer when nothing cheap applied."""
    candidate = build_candidate(_run(), Job(job_id=1, name="build", conclusion=None, steps=[]))
    assert candidate.suggested_class is None


def test_candidate_records_the_run_it_came_from() -> None:
    candidate = build_candidate(_run(), _job("Run pytest"))
    assert candidate.repo == "octo/demo"
    assert candidate.failed_step == "Run pytest"
    assert candidate.suggested_class is FailureClass.CODE_CHANGE


def test_diversify_caps_each_repository() -> None:
    """A benchmark dominated by one project measures that project."""
    candidates = [build_candidate(_run(repo=f"org/repo{i // 5}"), _job("test")) for i in range(15)]
    kept = list(diversify(candidates, per_repo=2))

    assert len(kept) == 6
    for repo in {candidate.repo for candidate in kept}:
        assert sum(1 for candidate in kept if candidate.repo == repo) == 2


def test_diversify_keeps_the_earliest_candidates_per_repo() -> None:
    candidates = [build_candidate(_run(), _job("test")) for _ in range(4)]
    assert len(list(diversify(candidates, per_repo=1))) == 1
