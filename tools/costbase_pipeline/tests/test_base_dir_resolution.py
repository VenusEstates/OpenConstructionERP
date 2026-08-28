"""The cost base directory must come from configuration, never from where the code lives.

This file exists because of a break that no other test could see. `BASE_DIR` used to be
`Path(__file__).resolve().parent.parent`, which resolved to the data directory only because
the pipeline source sat inside it. Moving the code somewhere version-controlled repointed it
at a directory containing no parquet at all -- and every other test would have stayed green,
because they all construct their input with `source_path=tmp_path / ...` and never exercise
the production resolution path.

A gate that goes green over a pipeline that can no longer find its input is worse than no
gate, so the resolution itself is asserted here.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from extract_translation_corpus import BASE_DIR_ENV, resolve_base_dir

PIPELINE_DIR = Path(__file__).resolve().parents[1]


def test_explicit_argument_wins(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(BASE_DIR_ENV, str(tmp_path / "from-env"))
    (tmp_path / "from-env").mkdir()
    (tmp_path / "explicit").mkdir()
    assert resolve_base_dir(tmp_path / "explicit") == tmp_path / "explicit"


def test_environment_is_the_fallback(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(BASE_DIR_ENV, str(tmp_path))
    assert resolve_base_dir() == tmp_path


def test_unset_is_refused_rather_than_guessed(monkeypatch) -> None:
    # The old behaviour silently produced a plausible-looking path. Refusing is the point:
    # a wrong directory yields "no regions found", which reads like an empty run.
    monkeypatch.delenv(BASE_DIR_ENV, raising=False)
    with pytest.raises(SystemExit, match=BASE_DIR_ENV):
        resolve_base_dir()


def test_a_directory_that_does_not_exist_is_refused(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv(BASE_DIR_ENV, raising=False)
    with pytest.raises(SystemExit, match="does not exist"):
        resolve_base_dir(tmp_path / "nope")


def test_the_old_module_level_constant_is_gone() -> None:
    """`BASE_DIR` must not be importable again.

    This is what actually guards materialize_localized_outputs. It never wrote
    `PIPELINE_DIR.parent` itself -- it imported the finished constant from here, so the AST
    rule below cannot see its version of the bug. Re-adding the export would silently make
    that import work again, so the export itself is the thing pinned.
    """
    import extract_translation_corpus

    assert not hasattr(extract_translation_corpus, "BASE_DIR"), (
        "BASE_DIR is back as a module-level constant; callers will import a data path "
        "derived from the source location instead of calling resolve_base_dir()"
    )


@pytest.mark.parametrize(
    "module",
    [
        "extract_translation_corpus.py",
        "extract_controlled_values.py",
        "materialize_localized_outputs.py",
    ],
)
def test_no_module_derives_the_data_directory_from_its_own_location(
    module: str,
) -> None:
    """`PIPELINE_DIR.parent` is the exact expression that broke. Ban it by inspection.

    Checked on the source rather than at runtime because the broken form is only wrong once
    the file moves, and by then the move has already happened.
    """
    tree = ast.parse((PIPELINE_DIR / module).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "parent"
            and isinstance(node.value, ast.Name)
            and node.value.id == "PIPELINE_DIR"
        ):
            pytest.fail(
                f"{module} derives a data path from its own location via PIPELINE_DIR.parent; "
                f"use resolve_base_dir() so the path comes from --base-dir or ${BASE_DIR_ENV}"
            )
