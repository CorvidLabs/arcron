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
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"

#: What a workflow may grant without someone deciding to.
ALLOWED = {"contents": {"read", "none"}}


def _workflows() -> list[Path]:
    return sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml"))


def test_there_are_workflows_to_check() -> None:
    # A checker that matches nothing passes for ever.
    assert len(_workflows()) >= 3


def test_every_workflow_declares_permissions() -> None:
    missing = [
        p.name
        for p in _workflows()
        if "permissions" not in (yaml.safe_load(p.read_text()) or {})
    ]
    assert not missing, (
        "these workflows inherit the repository default instead of declaring "
        f"their own: {missing}"
    )


def test_no_workflow_grants_more_than_a_checkout_needs() -> None:
    # Not a ban on write. A workflow that genuinely needs to comment or release
    # should say so, and this failing is how that decision gets seen rather than
    # absorbed.
    over: list[str] = []
    for p in _workflows():
        perms = (yaml.safe_load(p.read_text()) or {}).get("permissions")
        if perms in ("read-all", "write-all") or not isinstance(perms, dict):
            over.append(f"{p.name}: {perms!r}")
            continue
        for scope, level in perms.items():
            if level not in ALLOWED.get(scope, set()):
                over.append(f"{p.name}: {scope}: {level}")
    assert not over, (
        "these grant more than a checkout needs; if that is deliberate, widen "
        f"ALLOWED here so the decision is recorded: {over}"
    )
