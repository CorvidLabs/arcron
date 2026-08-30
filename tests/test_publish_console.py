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


def test_the_routes_note_is_read_not_remembered(tmp_path: Path) -> None:
    """The bug this replaced.

    The note said "the console has no client-side routes yet" and kept saying
    it for three releases after six of them landed. A note that states a fact
    goes stale; one that reads a fact cannot.
    """
    from scripts.publish_console import declared_routes

    web = tmp_path / "web" / "src" / "app"
    web.mkdir(parents=True)
    (web / "routes.ts").write_text(
        "export const routes: Routes = [\n"
        "  { path: '', component: Registry },\n"
        "  { path: 'u/:id', component: Upkeep },\n"
        "  { path: 'rain/new', component: NewRain },\n"
        "  { path: '**', redirectTo: '' },\n"
        "];\n"
    )
    assert declared_routes(tmp_path) == ["u/:id", "rain/new"]


def test_the_empty_path_and_catch_all_need_no_fallback(tmp_path: Path) -> None:
    # `/arcron/console/` is a real file and `**` never reaches the server.
    from scripts.publish_console import declared_routes

    web = tmp_path / "web" / "src" / "app"
    web.mkdir(parents=True)
    (web / "routes.ts").write_text("[{ path: '' }, { path: '**', redirectTo: '' }]")
    assert declared_routes(tmp_path) == []


def test_a_missing_route_table_reports_nothing_rather_than_failing(tmp_path: Path) -> None:
    from scripts.publish_console import declared_routes

    assert declared_routes(tmp_path) == []


def test_the_real_console_declares_the_routes_the_fallback_is_for() -> None:
    """Pinned against the live table, so removing the fallback shows up here."""
    from scripts.publish_console import declared_routes

    routes = declared_routes()
    assert "u/:id" in routes
    assert "rain/:id" in routes
    assert "" not in routes and "**" not in routes
