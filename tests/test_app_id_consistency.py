"""The live TestNet app id must be the same everywhere it is claimed.

alpha-1 moved the keeper to a new app, and most references followed but not
all: the console still defaulted to `769802474`, a superseded deployment whose
registry is empty and whose boxes are a different shape. A visitor opening the
console would have seen an empty registry and concluded the network was dead.

Nothing failed, because no test tied these files together. This one does:
`docs/releases.md` is the single source of truth for which app is live, and
every file that points a human or a program at "the" deployment must agree
with it.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# Deployments that exist but must never be presented as current.
SUPERSEDED = {"769802474", "769772891", "769772906"}

# Files that record history and are *expected* to name superseded apps: a
# completed task or an explicit "do not use this one" warning.
HISTORICAL = {
    "README.md",
    "specs/keeper/tasks.md",
    "specs/keeper/context.md",
    "AGENTS.md",
    "CLAUDE.md",
    "docs/releases.md",
    "docs/arcron.md",
    "tests/test_app_id_consistency.py",
}

# Every place that points at the live keeper app, with the pattern that finds it.
LIVE_KEEPER_POINTERS = [
    ("js/src/networks.ts", r"defaultAppId: (\d+)"),
    ("deploy/keeper.env.example", r"KEEPER_APP_ID=(\d+)"),
    ("deploy/notifier.env.example", r"KEEPER_APP_ID=(\d+)"),
    ("examples/register_upkeep.py", r"KEEPER_APP_ID = (\d+)"),
    (".github/workflows/keeper-bot.yml", r'default: "(\d+)"'),
    ("docs/integrating.md", r"get_application_address\((\d+)\)"),
    ("docs/arcron.md", r'"app_id": (\d+)'),
    ("fledge.toml", r"testnet_snapshot --network testnet --app-id (\d+)"),
]


def latest_release_app_id() -> str:
    """The app id in the last row of the release table — what is live now."""
    rows = [
        line
        for line in (ROOT / "docs" / "releases.md").read_text().splitlines()
        if re.match(r"\|\s*(alpha|beta|rc|mainnet)-\d+\s*\|", line)
    ]
    assert rows, "docs/releases.md has no recorded release"
    found = re.search(r"application/(\d+)", rows[-1])
    assert found, f"last release row names no app id: {rows[-1]}"
    return found.group(1)


@pytest.mark.parametrize(("relative_path", "pattern"), LIVE_KEEPER_POINTERS)
def test_live_pointer_matches_the_recorded_release(relative_path: str, pattern: str) -> None:
    expected = latest_release_app_id()
    text = (ROOT / relative_path).read_text()
    found = re.search(pattern, text)
    assert found, f"{relative_path}: pattern {pattern!r} found nothing — did the file move?"
    assert found.group(1) == expected, (
        f"{relative_path} points at app {found.group(1)}, but docs/releases.md records "
        f"{expected} as the live deployment. Update the pointer, or record a new release."
    )


def test_superseded_apps_appear_only_where_history_is_recorded() -> None:
    """A dead app id outside a historical file is a pointer someone will follow."""
    offenders = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or path.suffix not in {".py", ".ts", ".md", ".yml", ".yaml", ".example"}:
            continue
        relative = path.relative_to(ROOT).as_posix()
        if relative in HISTORICAL or "node_modules" in relative or relative.startswith("."):
            continue
        if relative.endswith(".example") or "/workflows/" in relative:
            pass  # still checked; listed here only because they are not source
        text = path.read_text(errors="replace")
        for app_id in SUPERSEDED:
            if app_id in text:
                offenders.append(f"{relative} names superseded app {app_id}")
    assert not offenders, "Superseded app ids outside historical files:\n  " + "\n  ".join(offenders)
