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

# `.sh` is here because `deploy/vps/install.sh` defaulted to a superseded app
# and this test could not see it: shell scripts point humans at deployments
# just as effectively as Python does.
CHECKED_SUFFIXES = {".py", ".ts", ".md", ".yml", ".yaml", ".example", ".sh", ".service"}

# Deployments that exist but must never be presented as current.
#
# 769823086 (alpha-1) was missing from this set until 2026-08-26, and the
# cost was exactly what this file was written to prevent: a keeper bot ran
# for hours against it while the live deployment went unserviced. It is
# immutable and pre-governance, so pointing anything at it is worse than
# pointing at an empty registry.
# The rain hub's superseded deployments were missing from this set until
# 2026-08-31, and cost the same thing again in a smaller way: the rain-bot
# workflow's dispatch input offered 769988156 as its prefilled default long
# after the hub moved twice, so accepting the default aimed a scan at the
# oldest dead app while the header comment above it claimed otherwise, and
# deploy/rain.env.example told a new operator the same thing. Both were found
# by reading, not by this test, because this set only knew about keepers.
# Both of those files left this repository with rain on 2026-08-31
# (docs/design/split.md), so the two rain ids now guard nothing but prose. They
# stay anyway: docs/testnet.md, docs/status.md and the split plan still name
# them as dead deployments, and the day one of those ids reappears in a file
# that is not history is the day this set earns its keep a second time.
SUPERSEDED = {
    "769823086", "769802474", "769772891", "769772906",  # keeper
    "769988156", "770029154",  # rain hub, moved to CorvidLabs/arcron-rain
}

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
    # Names the dead deployments precisely so that somebody holding an old
    # link can tell it is dead. That is the opposite of a pointer to follow,
    # but it is indistinguishable from one by grep.
    "docs/status.md",
    # The superseded-deployments table, and the analysis of what the gated
    # rain bought. It names dead apps in strikethrough with the word
    # "superseded" beside them; that is the warning, not a pointer.
    "docs/testnet.md",
    # The split plan, which names the dead rain apps in order to point at the
    # two places that still offer them as defaults. Caught by this test within
    # a minute of being written, which is the test working.
    "docs/design/split.md",
    "SECURITY.md",
    "tests/test_app_id_consistency.py",
    # Acceptance criteria and the console plan name the dead deployments as
    # the input to a scenario ("what should the banner say about this one?")
    # and as the record of a misconfiguration. Both are warnings.
    "docs/ac/j1-j5.md",
    "docs/console-plan.md",
}

# Dated review records. They describe the tree as it stood on a day, so a
# superseded id in one is evidence, not a pointer.
HISTORICAL_PREFIXES = ("docs/reviews/",)

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


def latest_release_stage() -> str:
    """The stage in the last row of the release table, e.g. `alpha-3`.

    The regex below already captured this and threw it away, which is a fair
    summary of how the stage has been maintained: `docs/releases.md` is updated
    on every deployment and the prose describing "where we are" is updated when
    somebody remembers. On 2026-08-27 the live app had been alpha-3 for a day
    while README.md said alpha-1 twice, docs/arcron.md's headline table said
    alpha-1 twenty lines above its own liveness table saying alpha-3, and an
    example said alpha-2.
    """
    return _last_release_row().group(1)


def _last_release_row() -> "re.Match[str]":
    rows = [
        found
        for line in (ROOT / "docs" / "releases.md").read_text().splitlines()
        if (found := re.match(r"\|\s*((?:alpha|beta|rc|mainnet)-\d+)\s*\|", line))
    ]
    assert rows, "docs/releases.md has no recorded release"
    return rows[-1]


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
        if not path.is_file() or path.suffix not in CHECKED_SUFFIXES:
            continue
        relative = path.relative_to(ROOT).as_posix()
        if (
            relative in HISTORICAL
            or relative.startswith(HISTORICAL_PREFIXES)
            or "node_modules" in relative
            # `.github/` was hidden by a blanket dot-prefix skip. Issue
            # templates and workflows point humans and machines at deployments
            # as effectively as source does, and `bug_report.yml` was naming a
            # superseded app while this test reported everything clean.
            or (relative.startswith(".") and not relative.startswith(".github/"))
        ):
            continue
        if relative.endswith(".example") or "/workflows/" in relative:
            pass  # still checked; listed here only because they are not source
        text = path.read_text(errors="replace")
        for app_id in SUPERSEDED:
            if app_id in text:
                offenders.append(f"{relative} names superseded app {app_id}")
    assert not offenders, "Superseded app ids outside historical files:\n  " + "\n  ".join(offenders)


# --- the release stage, in the files a stranger reads ---------------------

# What a stranger opens first. These are exempt from the superseded-app-id
# check above, because they name dead deployments deliberately, and that
# exemption is exactly why nothing noticed them naming a dead *stage*.
STRANGER_FACING = (
    "README.md",
    "SECURITY.md",
    "docs/arcron.md",
    "docs/status.md",
    "docs/integrating.md",
)

# A stage token next to one of these is history, not a claim about now.
HISTORICAL_MARKERS = (
    "superseded", "predates", "replaced", "earlier", "was ", "were ",
    "first deployment", "no longer", "until", "abandoned", "stranded",
    "migrat", "since", "previous", "deployed as", "up to and including",
    "old ", "retired", "immutable",
)

STAGE_PATTERN = re.compile(r"\b((?:alpha|beta|rc|mainnet)-\d+)\b")


def test_no_stranger_facing_file_names_a_superseded_stage_as_current() -> None:
    """A stage claim that has gone stale is a lie about what you are trusting.

    `docs/releases.md` is updated on every deployment. The prose saying "where
    we are" is updated when somebody remembers, and on 2026-08-27 nobody had:
    the live app had been alpha-3 for a day while README.md said alpha-1 twice,
    docs/arcron.md's headline table said alpha-1 twenty lines above its own
    liveness table saying alpha-3, and examples/register_upkeep.py said alpha-2.

    A reader cannot tell which sentence is the stale one, which is corrosive in
    a project whose safety case is that it tells you uncomfortable things
    plainly.

    Mentions of an older stage are fine, and necessary: the release history and
    the migration notes have to name what they are talking about. What is
    refused is an older stage named with nothing around it to say it is past.
    """
    current = latest_release_stage()
    offenders = []

    for relative in STRANGER_FACING:
        path = ROOT / relative
        if not path.is_file():
            continue
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            lowered = line.lower()
            if any(marker in lowered for marker in HISTORICAL_MARKERS):
                continue
            # A table row from the release history is a record, not a claim.
            if re.match(r"\|\s*(?:alpha|beta|rc|mainnet)-\d+\s*\|", line):
                continue
            for stage in STAGE_PATTERN.findall(line):
                if stage != current:
                    offenders.append(f"{relative}:{number} says {stage}, live is {current}\n      {line.strip()[:100]}")

    assert not offenders, (
        f"Stale release stage in files a stranger reads (live is {current}):\n  "
        + "\n  ".join(offenders)
    )


def test_production_wrappers_take_network_from_the_environment() -> None:
    """CLI `--network testnet` overrides `ARCRON_NETWORK`.

    A MainNet env file on a stock unit then looks healthy while it services
    TestNet. The units pass no `--network`; `deploy/keeper.env` /
    `notifier.env` set `ARCRON_NETWORK`. GitHub Actions stays TestNet on
    purpose — it is the soak stopgap, not the MainNet host.
    """
    service = (ROOT / "deploy" / "keeper-bot.service").read_text()
    assert "--network testnet" not in service
    notifier = (ROOT / "deploy" / "notifier.service").read_text()
    assert "--network testnet" not in notifier
    compose = (ROOT / "deploy" / "compose.yaml").read_text()
    assert '"--network", "testnet"' not in compose
    dockerfile = (ROOT / "deploy" / "Dockerfile").read_text()
    assert "--network\", \"testnet\"" not in dockerfile
    assert 'CMD ["--network", "testnet"]' not in dockerfile
    example = (ROOT / "deploy" / "keeper.env.example").read_text()
    assert "ARCRON_NETWORK=testnet" in example

