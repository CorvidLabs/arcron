"""How long a deployment has been the deployment, and what would reset it.

MainNet is gated on sustained TestNet time: a hold of some weeks during which
the contract does not change and is not redeployed. If it changes, the clock
starts again.

That rule is easy to state and easy to get wrong from memory, because what
resets it is not "did anyone edit a file". Most work in this repository is
scripts, docs and the console, and none of it touches the deployed programs.
Since app 769891898 went live, 98 commits landed and 15 touched
`smart_contracts/` at all; none of those 15 changed what is on chain.

What resets the clock is a redeploy, and a redeploy becomes necessary when the
compiled programs stop matching what is deployed. So the clock runs from the
application's creation round and is only meaningful while the local build still
matches. When the build has moved ahead, the honest reading is not "the clock
is at N days", it is "a redeploy is pending and this will restart at zero".

Reads public state and the local build. Signs nothing and deploys nothing.

Run:  poetry run python -m scripts.mainnet_clock --app-id N [--contract keeper]
                                                 [--network N] [--hold-days N]

Exits 0 whatever it finds, because it is a report and a report that fails is
noise in every runner that calls it. Pass `--gate` to make an unfinished or
reset hold exit non-zero, which is what you want if something depends on it.
"""

from __future__ import annotations

import argparse
import base64
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from scripts import network as net
from scripts.keeper_bot import resolve_app_id
from scripts import verify_build

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

REPO = Path(__file__).resolve().parent.parent

#: The hold this repository talks about. Reported rather than enforced: a gate
#: nobody can measure is a gate nobody keeps.
DEFAULT_HOLD_DAYS = 30


@dataclass(frozen=True)
class Clock:
    contract: str
    app_id: int
    created_round: int
    current_round: int
    seconds_per_round: float
    local_digest: str
    remote_digest: str

    @property
    def matches_source(self) -> bool:
        return self.local_digest == self.remote_digest

    @property
    def days(self) -> float:
        return (self.current_round - self.created_round) * self.seconds_per_round / 86_400

    def remaining(self, hold_days: int) -> float:
        return max(0.0, hold_days - self.days)

    def complete(self, hold_days: int) -> bool:
        """The hold is done only if it also ran on the code that is deployed."""
        return self.matches_source and self.days >= hold_days


def last_source_commit(contract: str) -> str:
    """The last commit touching this contract's source. Context, not the measure.

    A commit that rewords a docstring changes this and not the compiled
    programs, and the compiled programs are what is deployed. Shown so a reader
    can see the difference rather than assume the two move together.
    """
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%h %ad %s", "--date=short", "--",
             f"smart_contracts/{contract}/"],
            capture_output=True, text=True, check=True, cwd=REPO,
        )
        return out.stdout.strip() or "no commits"
    except Exception as cause:  # noqa: BLE001 - context only, never fatal
        return f"unknown ({cause})"


def measure(algod, indexer, contract: str, app_id: int, seconds_per_round: float) -> Clock:
    spec = verify_build._spec(contract)
    approval, clear = verify_build._programs(spec)
    params = algod.application_info(app_id)["params"]
    return Clock(
        contract=contract,
        app_id=app_id,
        created_round=int(indexer.applications(app_id)["application"]["created-at-round"]),
        current_round=int(algod.status()["last-round"]),
        seconds_per_round=seconds_per_round,
        local_digest=verify_build._digest(approval, clear),
        remote_digest=verify_build._digest(
            base64.b64decode(params["approval-program"]),
            base64.b64decode(params["clear-state-program"]),
        ),
    )


def report(clock: Clock, hold_days: int) -> None:
    logger.info(f"{clock.contract} app {clock.app_id} on round {clock.current_round:,}")
    logger.info(f"  deployed at round {clock.created_round:,}")
    logger.info(
        f"  age {clock.days:.1f} days at {clock.seconds_per_round} s/round"
    )
    logger.info(f"  source matches chain: {'yes' if clock.matches_source else 'NO'}")
    logger.info(f"  last commit to its source: {last_source_commit(clock.contract)}")
    logger.info("")

    if not clock.matches_source:
        logger.warning(
            "The local build no longer matches what is deployed, so this hold is "
            "not running. Deploying that change restarts it at zero. Until then "
            f"the {clock.days:.1f} days above are time served by code that is "
            "about to be replaced."
        )
        logger.warning(f"  local  {clock.local_digest}")
        logger.warning(f"  chain  {clock.remote_digest}")
        return

    if clock.complete(hold_days):
        logger.info(
            f"The {hold_days} day hold is COMPLETE, on the code that is deployed."
        )
        logger.info(
            "That is the only thing this measures. It says nothing about whether "
            "the documentation is ready, whether anyone else has used it, or "
            "whether you want to."
        )
    else:
        logger.info(
            f"{clock.remaining(hold_days):.1f} days to go on a {hold_days} day hold."
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    net.add_network_argument(parser)
    parser.add_argument("--app-id", type=int, default=None, help="the keeper app (default: KEEPER_APP_ID from the environment or .env.<network>)")
    parser.add_argument("--contract", default="keeper", choices=verify_build.CONTRACTS)
    parser.add_argument("--hold-days", type=int, default=DEFAULT_HOLD_DAYS)
    parser.add_argument(
        "--gate",
        action="store_true",
        help="exit non-zero unless the hold is complete on the deployed code",
    )
    args = parser.parse_args(argv)

    net.load_network(args.network)
    algorand = net.connect(args.network)
    args.app_id = resolve_app_id(parser, args.app_id, args.network)
    clock = measure(
        algorand.client.algod,
        algorand.client.indexer,
        args.contract,
        args.app_id,
        net.seconds_per_round(args.network),
    )
    report(clock, args.hold_days)
    if args.gate and not clock.complete(args.hold_days):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
