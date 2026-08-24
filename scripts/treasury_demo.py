"""A treasury that distributes on a schedule nobody controls.

Several consecutive distributions, driven by a real keeper, with deposits
arriving between them — which is the point: the schedule keeps running whether
or not anyone is paying attention, and nobody can move a distribution to a
more convenient moment.

Run:  poetry run python -m scripts.treasury_demo [--network localnet]
"""

import argparse
import logging

import algokit_utils

from scripts import keeper_bot, network as net
from scripts.keeper_e2e import _assert, _box_mbr, _quiet, _read_upkeep, _selector
from smart_contracts.artifacts.keeper.keeper_client import CancelArgs, RegisterArgs
from smart_contracts.artifacts.treasury.treasury_client import (
    ConfigureArgs,
    DepositArgs,
    OwedToArgs,
    TreasuryClient,
    TreasuryFactory,
)
from smart_contracts.keeper.contract import CATCH_UP
from smart_contracts.keeper.deploy_config import deploy as deploy_keeper

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

DISTRIBUTE_SIGNATURE = "distribute()uint64"
UPKEEP_INTERVAL = 10
FEE = 4_000
DEPOSIT = 1_000_000
SPLITS = (5_000, 3_000, 2_000)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    net.add_network_argument(parser)
    args = parser.parse_args(argv)

    algorand = net.connect(args.network)
    founder = algorand.account.from_environment("DEPLOYER")
    algod = algorand.client.algod
    keeper_client = deploy_keeper()

    # ------------------------------------------------------------------
    logger.info("── 1. A treasury with a fixed split ──")
    treasury, _ = algorand.client.get_typed_app_factory(
        TreasuryFactory, default_sender=founder.address
    ).send.create.bare()
    algorand.send.payment(
        algokit_utils.PaymentParams(
            sender=founder.address,
            receiver=treasury.app_address,
            amount=algokit_utils.AlgoAmount(micro_algo=400_000),
        )
    )
    parties = []
    for share in SPLITS:
        party = algorand.account.random()
        algorand.send.payment(
            algokit_utils.PaymentParams(
                sender=founder.address,
                receiver=party.address,
                amount=algokit_utils.AlgoAmount(micro_algo=300_000),
            )
        )
        parties.append((party, share))

    treasury.send.configure(
        args=ConfigureArgs(
            mbr_payment=algorand.create_transaction.payment(
                algokit_utils.PaymentParams(
                    sender=founder.address,
                    receiver=treasury.app_address,
                    amount=algokit_utils.AlgoAmount(micro_algo=200_000),
                )
            ),
            # The ABI type is (address,uint64,uint64)[] — who, share, owed.
            recipients=[(party.address, share, 0) for party, share in parties],
        )
    )
    logger.info(
        f"  Treasury {treasury.app_id}: "
        + ", ".join(f"{share / 100:g}%" for _, share in parties)
        + " — fixed, with no way to change it"
    )

    # ------------------------------------------------------------------
    logger.info("── 2. An upkeep drives the distributions ──")
    call_data = _selector(DISTRIBUTE_SIGNATURE)

    def payment(amount: int):
        return algorand.create_transaction.payment(
            algokit_utils.PaymentParams(
                sender=founder.address,
                receiver=keeper_client.app_address,
                amount=algokit_utils.AlgoAmount(micro_algo=amount),
            )
        )

    upkeep_id = keeper_client.send.register(
        args=RegisterArgs(
            mbr_payment=payment(_box_mbr([call_data])),
            funding_payment=payment(FEE * 6),
            target_app=treasury.app_id,
            call_args=[call_data],
            interval_rounds=UPKEEP_INTERVAL,
            fee_per_execution=FEE,
            # Every period's deposits must be distributed — skipping loses a week of allocations.
            policy=CATCH_UP,
            fee_cap=0,
            fee_asset=0,
            asset_fee=0,
        )
    ).abi_return

    def run_keeper() -> None:
        upkeep, _ = _read_upkeep(algorand, keeper_client.app_id, upkeep_id)
        net.wait_for_round(algorand, upkeep.next_execution_round, poker=founder)
        keeper_bot.main(
            ["--once", "--network", args.network, "--app-id", str(keeper_client.app_id)]
        )

    # ------------------------------------------------------------------
    logger.info("── 3. A quiet period distributes nothing, harmlessly ──")
    run_keeper()
    _assert("distributions", treasury.state.global_state.distributions, 0)

    # ------------------------------------------------------------------
    logger.info("── 4. Three consecutive scheduled distributions ──")
    for round_index in (1, 2, 3):
        treasury.send.deposit(
            args=DepositArgs(
                payment=algorand.create_transaction.payment(
                    algokit_utils.PaymentParams(
                        sender=founder.address,
                        receiver=treasury.app_address,
                        amount=algokit_utils.AlgoAmount(micro_algo=DEPOSIT),
                    )
                )
            )
        )
        run_keeper()
        _assert(f"distribution {round_index}", treasury.state.global_state.distributions, round_index)

    for (party, share), expected in zip(
        parties, [DEPOSIT * share // 10_000 * 3 for _, share in parties]
    ):
        _assert(
            f"owed to the {share / 100:g}% recipient",
            treasury.send.owed_to(args=OwedToArgs(who=party.address)).abi_return,
            expected,
        )
    _assert("nothing left undistributed", treasury.state.global_state.balance, 0)

    # ------------------------------------------------------------------
    logger.info("── 5. Recipients pull their own money ──")
    party, share = parties[0]
    client = TreasuryClient(
        algorand=algorand,
        app_id=treasury.app_id,
        default_sender=party.address,
        default_signer=party.signer,
    )
    before = algod.account_info(party.address)["amount"]
    claimed = client.send.claim(
        params=algokit_utils.CommonAppCallParams(
            extra_fee=algokit_utils.AlgoAmount(micro_algo=1_000)
        )
    ).abi_return
    expected = DEPOSIT * share // 10_000 * 3
    _assert("claimed", claimed, expected)
    _assert(
        "received (net of the 2,000 µALGO spent claiming)",
        algod.account_info(party.address)["amount"] - before,
        expected - 2_000,
    )

    # An unclaimed allocation must never block the schedule for anyone else.
    treasury.send.deposit(
        args=DepositArgs(
            payment=algorand.create_transaction.payment(
                algokit_utils.PaymentParams(
                    sender=founder.address,
                    receiver=treasury.app_address,
                    amount=algokit_utils.AlgoAmount(micro_algo=DEPOSIT),
                )
            )
        )
    )
    run_keeper()
    _assert("distributions", treasury.state.global_state.distributions, 4)

    with _quiet():
        keeper_client.send.cancel(
            args=CancelArgs(upkeep_id=upkeep_id),
            params=algokit_utils.CommonAppCallParams(
                extra_fee=algokit_utils.AlgoAmount(micro_algo=1_000)
            ),
        )

    logger.info("")
    logger.info(f"Treasury demo passed on {args.network} ✔")
    logger.info(
        f"  Treasury {treasury.app_id}: 4 scheduled distributions, "
        f"{treasury.state.global_state.allocated_total} µALGO still owed and claimable"
    )
    logger.info("  Nobody could delay a distribution, skip one, or change who receives it.")


if __name__ == "__main__":
    main()
