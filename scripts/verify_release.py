"""Check the live deployment against the release record, and say if the tree
has moved past it.

`verify_build` answers "is the deployed app what this tree compiles to right
now". Its own docstring names what it cannot answer: "whether the working tree
is the release tag you think it is". That gap cost a day. Two commits carrying
a security fix landed after alpha-2 was deployed, nothing redeployed, and
nothing was running the one command that would have noticed. The deployment the
docs pointed everybody at was missing the fix for a proven theft path.

So this checks two different things, and only one of them is an error:

1. **The chain must match the newest row in `docs/releases.md`.** That row is a
   published claim about which bytecode is live. If the chain disagrees with
   it, either somebody deployed without recording it or the record is wrong,
   and both are serious because third parties verify against that row.

2. **`smart_contracts/` may have moved past that row, and that is normal.**
   Between deployments the tree is supposed to be ahead. What is not fine is
   nobody knowing. So this reports the drift as a notice, with the commits, so
   it is visible every day rather than discovered later.

The distinction matters because a check that fails during ordinary development
is a check people learn to ignore, which is how the keeper cron came to be
green and useless.

    poetry run python -m scripts.verify_release --network testnet
"""

import argparse
import base64
import logging
import pathlib
import re
import subprocess
import sys

from scripts import network as net
from scripts.verify_build import _digest

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

REPO = pathlib.Path(__file__).resolve().parent.parent
RELEASES = REPO / "docs" / "releases.md"

# Paths whose contents decide the bytecode. A change under any of these means
# the tree can no longer compile to what is deployed.
BYTECODE_PATHS = ("smart_contracts/",)


class Release:
    """One row of the release table."""

    def __init__(self, stage: str, date: str, commit: str, sha256: str, app_id: int) -> None:
        self.stage = stage
        self.date = date
        self.commit = commit
        self.sha256 = sha256
        self.app_id = app_id

    def __str__(self) -> str:
        return f"{self.stage} ({self.date}, {self.commit}, app {self.app_id})"


def latest_release() -> Release:
    """The newest row of the release table.

    Rows are appended, so the last matching line is the current deployment.
    Parsed rather than hand-maintained in a second place, because a release
    record that has to be updated twice is one that will disagree with itself.
    """
    rows = []
    for line in RELEASES.read_text().splitlines():
        # | stage | date | `commit` | `sha256…` | ... [`app id`](url) ... |
        match = re.match(
            r"\|\s*(\w+-\d+)\s*\|\s*([\d-]+)\s*\|\s*`([0-9a-f]+)`\s*\|\s*`([0-9a-f]+)…?`\s*\|(.*)",
            line,
        )
        if not match:
            continue
        stage, date, commit, sha, rest = match.groups()
        app = re.search(r"\[`(\d+)`\]", rest)
        if not app:
            continue
        rows.append(Release(stage, date, commit, sha, int(app.group(1))))
    if not rows:
        raise SystemExit(f"No release rows parsed from {RELEASES}")
    return rows[-1]


def deployed_sha256(algod, app_id: int) -> str:
    """The combined sha256 of an app's programs.

    Uses `verify_build._digest` rather than recomputing it. An earlier version
    of this function hashed `approval + clear` and got a different answer,
    because the real digest separates the programs with a null byte so neither
    can be swapped independently. It reported drift on a deployment that was
    correct. A second copy of a predicate is a second thing to keep right, and
    this one was wrong within an hour of being written.
    """
    app = algod.application_info(app_id)
    approval = base64.b64decode(app["params"]["approval-program"])
    clear = base64.b64decode(app["params"]["clear-state-program"])
    return _digest(approval, clear)


def changed_since(commit: str) -> list[str]:
    """Commits touching bytecode paths since `commit`, oldest first."""
    try:
        out = subprocess.run(
            ["git", "log", "--reverse", "--format=%h %ad %s", "--date=short",
             f"{commit}..HEAD", "--", *BYTECODE_PATHS],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as error:
        logger.warning(f"Could not diff against {commit}: {error.stderr.strip()}")
        return []
    return [line for line in out.stdout.splitlines() if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    net.add_network_argument(parser)
    parser.add_argument(
        "--strict-drift",
        action="store_true",
        help="also exit non-zero when the tree has moved past the release",
    )
    args = parser.parse_args()

    algorand = net.connect(args.network or net.default_network())
    algod = algorand.client.algod

    release = latest_release()
    logger.info(f"Newest release row: {release}")

    on_chain = deployed_sha256(algod, release.app_id)
    logger.info(f"  recorded  sha256 {release.sha256}…")
    logger.info(f"  on chain  sha256 {on_chain}")

    failed = False
    if not on_chain.startswith(release.sha256):
        logger.error("")
        logger.error("✘ The live app is NOT what the release record claims.")
        logger.error(f"  {RELEASES.relative_to(REPO)} says {release.stage} is `{release.sha256}…`")
        logger.error(f"  but app {release.app_id} is running {on_chain}.")
        logger.error("  Either something was deployed without being recorded, or the")
        logger.error("  record is wrong. Third parties verify against that row.")
        failed = True
    else:
        logger.info("✔ The live app is the bytecode the release record claims.")

    drift = changed_since(release.commit)
    if drift:
        logger.info("")
        logger.warning(
            f"The tree has moved past {release.stage}: "
            f"{len(drift)} commit(s) touching {', '.join(BYTECODE_PATHS)} since {release.commit}."
        )
        for line in drift:
            logger.warning(f"    {line}")
        logger.warning("")
        logger.warning(
            "  That is normal between deployments. It stops being normal when a"
        )
        logger.warning(
            "  security fix sits here undeployed, which is what happened to alpha-2."
        )
        if args.strict_drift:
            failed = True
    else:
        logger.info(f"✔ No bytecode changes since {release.stage}; tree and chain agree.")

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
