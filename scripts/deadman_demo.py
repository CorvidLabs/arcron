"""A dead man's switch, end to end: something happens because you stopped.

The sequence a real user cares about, driven by a real keeper:

1. the owner checks in, and the scheduled sweeps do nothing;
2. the owner goes quiet;
3. a keeper — not the beneficiary, not anyone with an interest — fires it;
4. the beneficiary pulls the escrow, and the owner cannot undo any of it.

Run:  poetry run python -m scripts.deadman_demo [--network localnet]
"""

import argparse
import logging

import algokit_utils

from scripts import keeper_bot, network as net
from scripts.keeper_e2e import _assert, _box_mbr, _expect_failure, _quiet, _read_upkeep, _selector
from smart_contracts.artifacts.deadman.dead_man_client import (
    ArmArgs,
    DeadManClient,
    DeadManFactory,
)
from smart_contracts.artifacts.keeper.keeper_client import CancelArgs, RegisterArgs
from smart_contracts.keeper.contract import SKIP_AHEAD
from smart_contracts.keeper.deploy_config import deploy as deploy_keeper

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

SWEEP_SIGNATURE = "sweep()uint64"
CHECK_IN_INTERVAL = 30
UPKEEP_INTERVAL = 10
FEE = 4_000
ESCROW = 1_000_000


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    net.add_network_argument(parser)
    args = parser.parse_args(argv)

    algorand = net.connect(args.network)
    owner = algorand.account.from_environment("DEPLOYER")
    algod = algorand.client.algod
    keeper_client = deploy_keeper()

    # ------------------------------------------------------------------
    logger.info("── 1. An owner arms a switch ──")
    # Fresh instance per run: arming is once-only, by design.
    switch, _ = algorand.client.get_typed_app_factory(
        DeadManFactory, default_sender=owner.address
    ).send.create.bare()
    algorand.send.payment(
        algokit_utils.PaymentParams(
            sender=owner.address,
            receiver=switch.app_address,
            amount=algokit_utils.AlgoAmount(micro_algo=200_000),
        )
    )
    beneficiary = algorand.account.random()
    algorand.send.payment(
        algokit_utils.PaymentParams(
            sender=owner.address,
            receiver=beneficiary.address,
            amount=algokit_utils.AlgoAmount(micro_algo=300_000),
        )
    )
    deadline = switch.send.arm(
        args=ArmArgs(
            deposit=algorand.create_transaction.payment(
                algokit_utils.PaymentParams(
                    sender=owner.address,
                    receiver=switch.app_address,
                    amount=algokit_utils.AlgoAmount(micro_algo=ESCROW),
                )
            ),
            beneficiary=beneficiary.address,
            interval_rounds=CHECK_IN_INTERVAL,
        )
    ).abi_return
    logger.info(f"  Switch {switch.app_id}: check in by round {deadline} or it fires")
    _assert("escrow", switch.state.global_state.escrow, ESCROW)

    # ------------------------------------------------------------------
    logger.info("── 2. A keeper watches it ──")
    call_data = _selector(SWEEP_SIGNATURE)

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
            mbr_payment=payment(_box_mbr([call_data])),
            funding_payment=payment(FEE * 6),
            target_app=switch.app_id,
            call_args=[call_data],
            interval_rounds=UPKEEP_INTERVAL,
            fee_per_execution=FEE,
            # The switch fires once and goes inert, so replays are pure waste.
            policy=SKIP_AHEAD,
            fee_cap=0,
            fee_asset=0,
            asset_fee=0,
        )
    ).abi_return

    def sweep_once() -> None:
        upkeep, _ = _read_upkeep(algorand, keeper_client.app_id, upkeep_id)
        net.wait_for_round(algorand, upkeep.next_execution_round, poker=owner)
        keeper_bot.main(
            ["--once", "--network", args.network, "--app-id", str(keeper_client.app_id)]
        )

    # ------------------------------------------------------------------
    logger.info("── 3. While the owner is present, sweeps do nothing ──")
    sweep_once()
    _assert("fired", switch.send.has_fired().abi_return, False)
    new_deadline = switch.send.check_in().abi_return
    logger.info(f"  Owner checked in; deadline moved to {new_deadline}")
    sweep_once()
    _assert("still not fired", switch.send.has_fired().abi_return, False)
    _assert("check-ins", switch.state.global_state.check_ins, 1)
    _assert("escrow untouched", switch.state.global_state.escrow, ESCROW)

    # ------------------------------------------------------------------
    logger.info("── 4. The owner goes quiet ──")
    net.wait_for_round(algorand, new_deadline, poker=owner)
    _assert("rounds remaining", switch.send.rounds_remaining().abi_return, 0)
    sweep_once()
    fired_round = switch.state.global_state.fired_round
    _assert("fired", switch.send.has_fired().abi_return, True)
    assert fired_round >= new_deadline, "it fired before its deadline"
    logger.info(f"  Fired at round {fired_round} by a keeper, not by anyone interested")
    _assert("allocated to the beneficiary", switch.state.global_state.allocated, ESCROW)

    # ------------------------------------------------------------------
    logger.info("── 5. It cannot be undone ──")
    _expect_failure("check in after firing", "Already fired", lambda: switch.send.check_in())

    # ------------------------------------------------------------------
    logger.info("── 6. The beneficiary pulls the escrow ──")
    beneficiary_client = DeadManClient(
        algorand=algorand,
        app_id=switch.app_id,
        default_sender=beneficiary.address,
        default_signer=beneficiary.signer,
    )
    before = algod.account_info(beneficiary.address)["amount"]
    claimed = beneficiary_client.send.claim(
        params=algokit_utils.CommonAppCallParams(
            extra_fee=algokit_utils.AlgoAmount(micro_algo=1_000)
        )
    ).abi_return
    _assert("claimed", claimed, ESCROW)
    _assert(
        "received (net of the 2,000 µALGO they spent claiming)",
        algod.account_info(beneficiary.address)["amount"] - before,
        ESCROW - 2_000,
    )

    # ------------------------------------------------------------------
    logger.info("── 7. A fired switch is inert, and the upkeep should be cancelled ──")
    # This is the escrow-waste failure mode: Arcron keeps calling and keeps
    # paying keepers for a switch that has already done its one job.
    before_sweeps, _ = _read_upkeep(algorand, keeper_client.app_id, upkeep_id)
    sweep_once()
    after_sweeps, _ = _read_upkeep(algorand, keeper_client.app_id, upkeep_id)
    _assert("the keeper was still paid for a pointless sweep", after_sweeps.times_executed, before_sweeps.times_executed + 1)
    _assert(
        "and it cost the upkeep a fee",
        before_sweeps.balance - after_sweeps.balance,
        after_sweeps.fee_per_execution,
    )
    with _quiet():
        refund = keeper_client.send.cancel(
            args=CancelArgs(upkeep_id=upkeep_id),
            params=algokit_utils.CommonAppCallParams(
                extra_fee=algokit_utils.AlgoAmount(micro_algo=1_000)
            ),
        ).abi_return
    logger.info(f"  Cancelled the upkeep; {refund} µALGO returned to its creator")

    logger.info("")
    logger.info(f"Dead man's switch demo passed on {args.network} ✔")
    logger.info(f"  Switch {switch.app_id}: armed, checked in once, then fired at {fired_round}")
    logger.info("  The owner could not stop it, and the keeper had no stake in the outcome.")


if __name__ == "__main__":
    main()
