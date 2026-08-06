"""Prompt loading and hashing.

The hash is what ties a scorecard to an exact prompt, so its stability rules
are tested as carefully as the parsing.
"""

from pathlib import Path

import pytest

from buildsleuth.prompts.loader import (
    Prompt,
    PromptError,
    combined_hash,
    content_hash,
    load_prompt,
)

VALID = """---
id: demo
version: v1
changelog: something
---
Hello {name}, this is the body.
"""


def _write(root: Path, name: str, version: str, text: str) -> Path:
    path = root / name / f"{version}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_loads_body_and_metadata(tmp_path: Path) -> None:
    _write(tmp_path, "demo", "v1", VALID)
    prompt = load_prompt("demo", root=tmp_path)
    assert prompt.name == "demo"
    assert prompt.version == "v1"
    assert prompt.text == "Hello {name}, this is the body."
    assert "---" not in prompt.text


def test_hash_ignores_line_endings_and_surrounding_blank_lines(tmp_path: Path) -> None:
    """CI checks out with different line endings; the hash must not move."""
    _write(tmp_path, "a", "v1", VALID)
    _write(tmp_path, "b", "v1", VALID.replace("\n", "\r\n"))
    assert (
        load_prompt("a", root=tmp_path).content_hash == load_prompt("b", root=tmp_path).content_hash
    )


def test_hash_ignores_changelog_but_tracks_body(tmp_path: Path) -> None:
    _write(tmp_path, "a", "v1", VALID)
    _write(tmp_path, "b", "v1", VALID.replace("changelog: something", "changelog: reworded"))
    _write(tmp_path, "c", "v1", VALID.replace("this is the body", "this is a different body"))

    original = load_prompt("a", root=tmp_path).content_hash
    assert load_prompt("b", root=tmp_path).content_hash == original
    assert load_prompt("c", root=tmp_path).content_hash != original


def test_render_substitutes_values(tmp_path: Path) -> None:
    _write(tmp_path, "demo", "v1", VALID)
    assert "Hello world" in load_prompt("demo", root=tmp_path).render(name="world")


def test_render_fails_loudly_on_a_missing_value(tmp_path: Path) -> None:
    _write(tmp_path, "demo", "v1", VALID)
    with pytest.raises(PromptError, match="needs a value"):
        load_prompt("demo", root=tmp_path).render()


def test_missing_file_names_the_path(tmp_path: Path) -> None:
    with pytest.raises(PromptError, match="no prompt file"):
        load_prompt("absent", root=tmp_path)


def test_missing_frontmatter_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "demo", "v1", "just a body, no metadata")
    with pytest.raises(PromptError, match="frontmatter"):
        load_prompt("demo", root=tmp_path)


def test_missing_id_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "demo", "v1", "---\nversion: v1\n---\nbody\n")
    with pytest.raises(PromptError, match="needs an id"):
        load_prompt("demo", root=tmp_path)


def test_empty_body_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "demo", "v1", "---\nid: demo\n---\n\n")
    with pytest.raises(PromptError, match="empty body"):
        load_prompt("demo", root=tmp_path)


def test_combined_hash_is_order_independent() -> None:
    first = Prompt(name="a", version="v1", text="x", content_hash=content_hash("x"))
    second = Prompt(name="b", version="v1", text="y", content_hash=content_hash("y"))
    assert combined_hash([first, second]) == combined_hash([second, first])


def test_combined_hash_changes_when_any_prompt_changes() -> None:
    first = Prompt(name="a", version="v1", text="x", content_hash=content_hash("x"))
    second = Prompt(name="b", version="v1", text="y", content_hash=content_hash("y"))
    edited = Prompt(name="b", version="v1", text="z", content_hash=content_hash("z"))
    assert combined_hash([first, second]) != combined_hash([first, edited])


def test_shipped_triage_prompt_loads_and_covers_the_taxonomy() -> None:
    """The real prompt must teach every class the scorer measures."""
    from buildsleuth.models.taxonomy import SUBCATEGORIES, FailureClass

    prompt = load_prompt("triage")
    for failure_class in FailureClass:
        assert failure_class.value in prompt.text
        for subcategory in SUBCATEGORIES[failure_class]:
            assert subcategory in prompt.text, f"{subcategory} missing from the triage prompt"


def test_shipped_triage_prompt_renders_with_the_context_we_supply() -> None:
    """Every placeholder in the prompt must be one the pipeline actually provides."""
    rendered = load_prompt("triage").render(
        repo="octo/demo",
        workflow_name="CI",
        failed_job="build",
        failed_step="run tests",
        run_attempt="1",
        condensed_log="FAILED tests/test_x.py::test_y",
        diff="none",
    )
    assert "octo/demo" in rendered
    assert "FAILED tests/test_x.py::test_y" in rendered
