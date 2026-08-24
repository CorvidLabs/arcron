"""A feed goes quiet, and a keeper notices. End to end.

The three states the issue asks for — fresh, gone stale, recovered — driven by
a real Archon upkeep and a real reporter.

Run:  poetry run python -m scripts.watchdog_demo [--network localnet]
"""

import argparse
import logging

import algokit_utils

from scripts import keeper_bot, network as net
from scripts.keeper_e2e import _assert, _box_mbr, _quiet, _read_upkeep, _selector
from smart_contracts.artifacts.keeper.keeper_client import CancelArgs, RegisterArgs
from smart_contracts.artifacts.watchdog.watchdog_client import (
    ConfigureArgs,
    UpdateArgs,
    WatchdogClient,
    WatchdogFactory,
)
from smart_contracts.keeper.deploy_config import deploy as deploy_keeper

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

CHECK_SIGNATURE = "check_freshness()uint64"
THRESHOLD_ROUNDS = 30
UPKEEP_INTERVAL = 10
FEE = 4_000


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    net.add_network_argument(parser)
    args = parser.parse_args(argv)

    algorand = net.connect(args.network)
    owner = algorand.account.from_environment("DEPLOYER")
    keeper_client = deploy_keeper()

    # ------------------------------------------------------------------
    logger.info("── 1. A feed and its reporter ──")
    watchdog, _ = algorand.client.get_typed_app_factory(
        WatchdogFactory, default_sender=owner.address
    ).send.create.bare()
    reporter = algorand.account.random()
    algorand.send.payment(
        algokit_utils.PaymentParams(
            sender=owner.address,
            receiver=reporter.address,
            amount=algokit_utils.AlgoAmount(micro_algo=400_000),
        )
    )
    watchdog.send.configure(
        args=ConfigureArgs(reporter=reporter.address, threshold_rounds=THRESHOLD_ROUNDS)
    )
    reporter_client = WatchdogClient(
        algorand=algorand,
        app_id=watchdog.app_id,
        default_sender=reporter.address,
        default_signer=reporter.signer,
    )
    reporter_client.send.update(args=UpdateArgs(value=1_234))
    _assert("reading", watchdog.send.reading().abi_return, 1_234)
    logger.info(f"  Watchdog {watchdog.app_id}: stale after {THRESHOLD_ROUNDS} quiet rounds")

    # ------------------------------------------------------------------
    logger.info("── 2. A keeper watches the watcher ──")
    call_data = _selector(CHECK_SIGNATURE)

    def payment(amount: int):
        return algorand.create_transaction.payment(
            algokit_utils.PaymentParams(
                sender=owner.address,
                receiver=keeper_client.app_address,
                amount=algokit_utils.AlgoAmount(micro_algo=amount),
            )
        )

    upkeep_id = keeper_client.send.register(
        args=RegisterArgs(
            mbr_payment=payment(_box_mbr(call_data)),
            funding_payment=payment(FEE * 6),
            target_app=watchdog.app_id,
            call_data=call_data,
            interval_rounds=UPKEEP_INTERVAL,
            fee_per_execution=FEE,
        )
    ).abi_return

    def sweep() -> None:
        upkeep, _ = _read_upkeep(algorand, keeper_client.app_id, upkeep_id)
        net.wait_for_round(algorand, upkeep.next_execution_round, poker=owner)
        keeper_bot.main(
            ["--once", "--network", args.network, "--app-id", str(keeper_client.app_id)]
        )

    # ------------------------------------------------------------------
    logger.info("── 3. Fresh: the checks find nothing wrong ──")
    sweep()
    _assert("stale", watchdog.send.is_stale().abi_return, False)
    _assert("episodes", watchdog.state.global_state.stale_episodes, 0)

    # ------------------------------------------------------------------
    logger.info("── 4. The reporter goes quiet ──")
    quiet_until = (
        watchdog.state.global_state.updated_round + THRESHOLD_ROUNDS + 1
    )
    net.wait_for_round(algorand, quiet_until, poker=owner)
    sweep()
    _assert("stale", watchdog.send.is_stale().abi_return, True)
    _assert("episodes", watchdog.state.global_state.stale_episodes, 1)
    flagged = watchdog.state.global_state.stale_since
    logger.info(f"  Flagged at round {flagged} by a keeper — not by the reporter")

    # A flagged feed is not re-flagged on every cadence.
    sweep()
    _assert("still one episode", watchdog.state.global_state.stale_episodes, 1)
    _assert("flagged round unchanged", watchdog.state.global_state.stale_since, flagged)

    # ------------------------------------------------------------------
    logger.info("── 5. Recovered: an update clears the flag ──")
    reporter_client.send.update(args=UpdateArgs(value=5_678))
    _assert("stale", watchdog.send.is_stale().abi_return, False)
    _assert("reading", watchdog.send.reading().abi_return, 5_678)
    _assert(
        "the outage is still on the record",
        watchdog.state.global_state.stale_episodes,
        1,
    )
    logger.info(
        f"  Recovered at round {watchdog.state.global_state.last_recovery_round}; "
        f"episode 1 remains on the record"
    )
    sweep()
    _assert("stays clear while fresh", watchdog.send.is_stale().abi_return, False)

    with _quiet():
        keeper_client.send.cancel(
            args=CancelArgs(upkeep_id=upkeep_id),
            params=algokit_utils.CommonAppCallParams(
                extra_fee=algokit_utils.AlgoAmount(micro_algo=1_000)
            ),
        )

    logger.info("")
    logger.info(f"Staleness watchdog demo passed on {args.network} ✔")
    logger.info(f"  Watchdog {watchdog.app_id}: fresh → stale → recovered, one episode recorded")
    logger.info("  The contract never read the value; it only ever compared rounds.")


if __name__ == "__main__":
    main()
