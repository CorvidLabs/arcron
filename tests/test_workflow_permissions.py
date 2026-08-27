"""Every workflow declares its own token permissions.

A workflow with no `permissions:` block takes whatever the repository's default
happens to be. That default is `read` today, and a default is a setting: it can
be widened later, by somebody who is not thinking about these files, and every
workflow silently widens with it. CodeQL opened five alerts for exactly that.

The floor is `contents: read`, which is what a checkout needs. Nothing in this
repository writes from a workflow: no comments, no pushes, no tags, no releases.
A job that ever needs more has to say so in the file and be noticed here.

This exists because the alerts were opened against files that had been correct
for months. Nobody removed a permissions block; there had never been one, and
nothing asked.

The block is read without a YAML library. The first version imported `yaml`,
which is in no dependency of this project and was leaking into one developer's
virtualenv from elsewhere, so CI could not collect this module at all. Adding a
YAML parser to a smart-contract project to read five files is the wrong trade,
and a lenient hand parser guarding a security property is worse than either. So
`_permissions` understands exactly the shapes these files use and raises on
anything else: an unreadable block fails the test rather than passing it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"

#: What a workflow may grant without someone deciding to.
ALLOWED = {"contents": {"read", "none"}}

#: Returned when a file has no top-level `permissions:` at all.
MISSING = object()


def _permissions(text: str) -> object:
    """The top-level `permissions:` value, as a str or a dict of str to str.

    Deliberately narrow. Top-level keys sit at column zero, so the block is the
    run of more-indented lines after `permissions:`. Anything this does not
    recognise raises, because the alternative is a guard that quietly reports
    "no problem" about a file it could not read.
    """
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not line.startswith("permissions:"):
            continue

        inline = line[len("permissions:") :].strip()
        if inline and not inline.startswith("#"):
            return inline

        block: dict[str, str] = {}
        for entry in lines[index + 1 :]:
            if not entry.strip() or entry.lstrip().startswith("#"):
                continue
            if not entry[:1].isspace():
                break  # back to column zero: the block has ended.
            scope, sep, level = entry.strip().partition(":")
            if not sep:
                raise ValueError(f"cannot read permissions entry: {entry!r}")
            block[scope.strip()] = level.split("#")[0].strip()
        if not block:
            raise ValueError("permissions: is present but grants nothing")
        return block

    return MISSING


def _workflows() -> list[Path]:
    return sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml"))


def test_there_are_workflows_to_check() -> None:
    # A checker that matches nothing passes for ever.
    assert len(_workflows()) >= 3


def test_every_workflow_declares_permissions() -> None:
    missing = [p.name for p in _workflows() if _permissions(p.read_text()) is MISSING]
    assert not missing, (
        "these workflows inherit the repository default instead of declaring "
        f"their own: {missing}"
    )


def test_no_workflow_grants_more_than_a_checkout_needs() -> None:
    # Not a ban on write. A workflow that genuinely needs to comment or release
    # should say so, and this failing is how that decision gets seen rather than
    # absorbed.
    over: list[str] = []
    for path in _workflows():
        perms = _permissions(path.read_text())
        if not isinstance(perms, dict):
            over.append(f"{path.name}: {perms!r}")
            continue
        for scope, level in perms.items():
            if level not in ALLOWED.get(scope, set()):
                over.append(f"{path.name}: {scope}: {level}")
    assert not over, (
        "these grant more than a checkout needs; if that is deliberate, widen "
        f"ALLOWED here so the decision is recorded: {over}"
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("on: push\npermissions:\n  contents: read\n", {"contents": "read"}),
        ("permissions:\n  contents: read\n\njobs:\n  ci:\n", {"contents": "read"}),
        ("permissions: read-all\n", "read-all"),
        ("permissions:\n  contents: read  # a checkout\n", {"contents": "read"}),
        ("jobs:\n  ci:\n    permissions:\n      contents: write\n", MISSING),
        ("on: push\njobs: {}\n", MISSING),
    ],
)
def test_the_parser_reads_the_shapes_these_files_use(text: str, expected: object) -> None:
    # The last two matter most: a `permissions:` indented under a job is not a
    # top-level grant, and must not be mistaken for one.
    assert _permissions(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "permissions:\njobs: {}\n",  # present, grants nothing
        "permissions:\n  contents\n",  # no colon
    ],
)
def test_the_parser_refuses_what_it_cannot_read(text: str) -> None:
    # A guard that cannot read a file has to say so. Returning "fine" here is
    # the failure mode this whole module exists to prevent.
    with pytest.raises(ValueError):
        _permissions(text)
