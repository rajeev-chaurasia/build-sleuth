from typer.testing import CliRunner

from buildsleuth import __version__
from buildsleuth.cli import app

runner = CliRunner()


def test_version_command_prints_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_no_args_shows_help() -> None:
    result = runner.invoke(app, [])
    assert "triage" in result.output.lower() or "Usage" in result.output
