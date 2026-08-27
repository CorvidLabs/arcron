"""A repository path named in a document has to exist.

This has now gone wrong twice. `CLAUDE.md`, `AGENTS.md` and a test docstring all
pointed at `web/src/app/core/upkeep.ts` after the decoder moved to `js/src/`, so
an agent following the project's own instructions looked in the wrong place. And
`SECURITY.md` listed that same dead path among the things it asks people to
report bugs in — a vulnerability scope naming a file a reporter cannot open.

Prose can afford a stale reference for a week. A path cannot: it is an
instruction, and following it fails.

The check is deliberately narrow. It looks only at backticked strings that are
unambiguously repository paths — a directory separator and a known source
extension, or a known top-level directory — so a shell command, an npm package
or a URL fragment is not mistaken for a file. Being narrow is what keeps it from
being ignored.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Documents whose paths are instructions somebody follows.
CHECKED = [
    "README.md",
    "START-HERE.md",
    "SECURITY.md",
    "CONTRIBUTING.md",
    "AGENTS.md",
    "CLAUDE.md",
]

#: Plus everything in docs/, except the review archive, which is a record of
#: what people said at the time and is not improved by editing it later.
CHECKED += [
    str(path.relative_to(ROOT))
    for path in sorted((ROOT / "docs").rglob("*.md"))
    if "reviews" not in path.parts and "design" not in path.parts
]

#: Extensions that make a backticked string a source path rather than prose.
SOURCE_SUFFIXES = (".py", ".ts", ".js", ".json", ".toml", ".yml", ".yaml", ".css", ".html", ".sh", ".tex")

#: Top-level directories in this repository, for paths without an extension.
TOP_LEVEL = ("smart_contracts/", "scripts/", "tests/", "web/", "js/", "specs/", "docs/", ".github/")

#: Inside a backtick, this is a path we can check.
BACKTICKED = re.compile(r"`([^`\n]+)`")


def _file_part(text: str) -> str:
    """The path, with any symbol or line reference stripped.

    Documents here cite a place, not just a file: `scripts/network.py::wait_for_round`
    and `web/src/app/core/wallets.ts:26-36` are both normal and both useful. The
    file still has to exist, which is the half this checks; whether the symbol or
    the line is still there is beyond what a path check can know.
    """
    return re.split(r"::|:\d", text, maxsplit=1)[0]


def _looks_like_a_repository_path(text: str) -> bool:
    if " " in text or text.startswith(("http", "-", "$", "#")):
        return False
    path = _file_part(text)
    if path.endswith(SOURCE_SUFFIXES) and "/" in path:
        return True
    return path.startswith(TOP_LEVEL) and not path.endswith("/")


def _paths_in(document: Path) -> list[str]:
    return [
        match
        for match in BACKTICKED.findall(document.read_text())
        if _looks_like_a_repository_path(match)
    ]


def test_every_path_named_in_a_document_exists() -> None:
    missing: list[str] = []
    for name in CHECKED:
        document = ROOT / name
        if not document.exists():
            continue
        for path in _paths_in(document):
            # A glob is a description, not a destination.
            if any(character in path for character in "*?["):
                continue
            if not (ROOT / _file_part(path)).exists():
                missing.append(f"{name} -> {path}")
    assert not missing, "documents name paths that do not exist:\n  " + "\n  ".join(missing)


def test_the_check_is_actually_looking_at_something() -> None:
    # A path checker that matches nothing passes for ever. This is the guard
    # against a regex that quietly stops recognising paths.
    found = sum(len(_paths_in(ROOT / name)) for name in CHECKED if (ROOT / name).exists())
    assert found > 40, f"only {found} paths recognised; the matcher has probably stopped working"


def test_the_decoder_twin_is_named_where_it_lives() -> None:
    # The specific failure that produced this file, pinned by name.
    assert (ROOT / "js/src/upkeep.ts").exists()
    assert not (ROOT / "web/src/app/core/upkeep.ts").exists()
