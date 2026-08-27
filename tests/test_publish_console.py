"""The console staging script has to actually run.

`fledge lanes run ci` does not stage the console: staging needs a site checkout,
which CI has no reason to have. So the only thing exercising publish_console.py
was somebody running it, and a NameError in a helper added that morning reached
main behind a green CI run.

These tests import the module and call the piece that broke, which needs no site
and no network. They will not catch a bad copy; they will catch the module not
loading and the provenance file not being written, which is what actually
happened.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_the_module_imports() -> None:
    # The failure this pins: `def _write_provenance(target: Path)` in a module
    # that imports pathlib and never binds a bare `Path`. It is a NameError at
    # call time, not import time, so importing is necessary and not sufficient.
    importlib.import_module("scripts.publish_console")


def test_provenance_is_written_and_names_a_real_commit(tmp_path: Path) -> None:
    module = importlib.import_module("scripts.publish_console")
    module._write_provenance(tmp_path)

    written = (tmp_path / "BUILD.txt").read_text()
    assert "github.com/CorvidLabs/arcron" in written
    assert "Do not edit by hand" in written

    commit = next(line.split()[-1] for line in written.splitlines() if line.startswith("Commit:"))
    assert commit != "unknown", "provenance could not read a commit"
    # A commit this repository actually contains, not a plausible-looking hash.
    resolved = subprocess.run(
        ["git", "-C", str(ROOT), "cat-file", "-t", commit],
        capture_output=True,
        text=True,
        check=False,
    )
    assert resolved.stdout.strip() == "commit", f"{commit} is not a commit in this repository"


def test_a_dirty_tree_is_declared(tmp_path: Path) -> None:
    # A provenance file that names a commit which does not reproduce the bundle
    # is worse than no provenance, so the dirty case has to say so out loud.
    module = importlib.import_module("scripts.publish_console")
    module._write_provenance(tmp_path)
    written = (tmp_path / "BUILD.txt").read_text()

    dirty = subprocess.run(
        ["git", "-C", str(ROOT), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()

    if dirty:
        assert "WARNING" in written, "a dirty tree produced provenance with no warning"
    else:
        assert "WARNING" not in written, "a clean tree produced a spurious dirty warning"
