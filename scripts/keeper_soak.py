"""Soak test: does the keeper network keep working, run after run?

The e2e proves a single execution is correct. This proves the *loop* is: an
upkeep executed over and over for as long as you leave it, with the schedule
never drifting, the escrow draining by exactly one fee per run, and the app
account staying able to pay out everything it holds.

Rounds, not clocks, drive the contract — so the same loop that survives
minutes here is what runs for months on a real chain, where each round is
~2.8 seconds of wall time instead of one transaction.

Run:  poetry run python -m scripts.keeper_soak [--minutes 3] [--network localnet]
"""

import argparse
import logging
import time

import algokit_utils

from scripts import keeper_bot, network as net
from scripts.keeper_e2e import (
    FEE,
    INTERVAL_ROUNDS,
    _assert,
    _read_upkeep,
    _register,
)
from smart_contracts.artifacts.keeper.keeper_client import CancelArgs, TopUpArgs
from smart_contracts.keeper.deploy_config import deploy as deploy_keeper
from smart_contracts.pulse.deploy_config import deploy as deploy_pulse

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Enough escrow that the loop tops up rather than starving mid-run.
RUNS_PER_TOP_UP = 5


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    net.add_network_argument(parser)
    parser.add_argument(
        "--minutes", type=float, default=3.0, help="how long to keep it running"
    )
    args = parser.parse_args(argv)

    algorand = net.connect(args.network)
    deployer = algorand.account.from_environment("DEPLOYER")
    keeper_client = deploy_keeper()
    pulse_client = deploy_pulse()
    app_id = keeper_client.app_id

    upkeep_id = _register(
        algorand, keeper_client, deployer, pulse_client.app_id, FEE * RUNS_PER_TOP_UP
    )
    logger.info(
        f"Soaking upkeep {upkeep_id} on app {app_id} for {args.minutes} minute(s): "
        f"every {INTERVAL_ROUNDS} rounds, {FEE} µALGO per run"
    )

    started = time.monotonic()
    deadline = started + args.minutes * 60
    runs = 0
    top_ups = 0
    try:
        while time.monotonic() < deadline:
            before, _ = _read_upkeep(algorand, app_id, upkeep_id)
            if before.balance < before.fee_per_execution:
                keeper_client.send.top_up(
                    args=TopUpArgs(
                        upkeep_id=upkeep_id,
                        funding_payment=algorand.create_transaction.payment(
                            algokit_utils.PaymentParams(
                                sender=deployer.address,
                                receiver=keeper_client.app_address,
                                amount=algokit_utils.AlgoAmount(
                                    micro_algo=FEE * RUNS_PER_TOP_UP
                                ),
                            )
                        ),
                    )
                )
                top_ups += 1
                before, _ = _read_upkeep(algorand, app_id, upkeep_id)

            net.wait_for_round(algorand, before.next_execution_round, poker=deployer)
            keeper_bot.main(
                ["--once", "--network", args.network, "--app-id", str(app_id)]
            )
            after, _ = _read_upkeep(algorand, app_id, upkeep_id)

            runs += 1
            _assert(f"run {runs}: executions", after.times_executed, before.times_executed + 1)
            _assert(f"run {runs}: escrow", after.balance, before.balance - before.fee_per_execution)
            _assert(
                f"run {runs}: schedule held",
                after.next_execution_round,
                before.next_execution_round + before.interval_rounds,
            )
            info = algorand.client.algod.account_info(keeper_client.app_address)
            assert info["amount"] - info["min-balance"] >= after.balance, (
                f"run {runs}: app account cannot cover its escrow"
            )
    finally:
        final, _ = _read_upkeep(algorand, app_id, upkeep_id)
        keeper_client.send.cancel(
            args=CancelArgs(upkeep_id=upkeep_id),
            params=algokit_utils.CommonAppCallParams(
                extra_fee=algokit_utils.AlgoAmount(micro_algo=1_000)
            ),
        )

    elapsed = time.monotonic() - started
    logger.info("")
    logger.info(f"Soak passed on {args.network} ✔")
    logger.info(
        f"  {runs} consecutive executions over {elapsed / 60:.1f} minutes, "
        f"{top_ups} top-up(s), no drift"
    )
    logger.info(f"  Pulse.beats = {pulse_client.state.global_state.beats}")
    logger.info(
        f"  {final.times_executed} runs recorded on the upkeep before it was cancelled"
    )


if __name__ == "__main__":
    main()
