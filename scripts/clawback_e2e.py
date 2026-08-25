"""An ASA bonus that gets clawed back must not strand the ALGO.

The unit tests for this use an upkeep whose bonus was never funded, which
exercises the same branch but not the same situation. Here the asset is really
funded, really clawed back by its issuer, and the recovery really happens on
an AVM: the mock neither runs inner transactions nor enforces minimum
balances, so it cannot show that a failed asset transfer would have taken the
ALGO refund down with it.

Run:  poetry run python -m scripts.clawback_e2e [--network localnet]
"""

import argparse
import logging

import algokit_utils

from scripts import network as net
from scripts.keeper_e2e import _assert, _box_mbr, _read_upkeep, _selector
from smart_contracts.artifacts.keeper.keeper_client import (
    CancelArgs,
    ExecuteArgs,
    OptInAssetArgs,
    RegisterArgs,
    TopUpAssetArgs,
)
from smart_contracts.keeper.contract import ASSET_OPT_IN_MBR, CATCH_UP
from smart_contracts.keeper.deploy_config import deploy as deploy_keeper

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

FEE = 4_000
BONUS = 500
INTERVAL = 10


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    net.add_network_argument(parser)
    args = parser.parse_args(argv)

    algorand = net.connect(args.network)
    algod = algorand.client.algod
    creator = algorand.account.from_environment("DEPLOYER")
    keeper = deploy_keeper()

    logger.info("── 1. An issuer who kept clawback ──")
    issuer = algorand.account.random()
    algorand.send.payment(
        algokit_utils.PaymentParams(
            sender=creator.address, receiver=issuer.address,
            amount=algokit_utils.AlgoAmount(micro_algo=2_000_000),
        )
    )
    asset = algorand.send.asset_create(
        algokit_utils.AssetCreateParams(
            sender=issuer.address, total=1_000_000, decimals=0,
            asset_name="Clawback Points", unit_name="CBP",
            clawback=issuer.address, manager=issuer.address,
        )
    ).asset_id
    logger.info(f"   Asset {asset}, clawback held by its issuer")

    logger.info("── 2. An upkeep offering it as a bonus ──")
    call_data = _selector("tick()uint64")

    def payment(amount: int, receiver=None):
        return algorand.create_transaction.payment(
            algokit_utils.PaymentParams(
                sender=creator.address,
                receiver=receiver or keeper.app_address,
                amount=algokit_utils.AlgoAmount(micro_algo=amount),
            )
        )

    from smart_contracts.pulse.deploy_config import deploy as deploy_pulse

    pulse = deploy_pulse()
    upkeep_id = keeper.send.register(
        args=RegisterArgs(
            mbr_payment=payment(_box_mbr([call_data])),
            funding_payment=payment(FEE * 4),
            target_app=pulse.app_id,
            call_args=[call_data],
            interval_rounds=INTERVAL,
            fee_per_execution=FEE,
            policy=CATCH_UP,
            fee_cap=0,
            fee_asset=asset,
            asset_fee=BONUS,
        )
    ).abi_return

    keeper.send.opt_in_asset(
        args=OptInAssetArgs(
            mbr_payment=payment(ASSET_OPT_IN_MBR), upkeep_id=upkeep_id, asset=asset
        ),
        params=algokit_utils.CommonAppCallParams(
            extra_fee=algokit_utils.AlgoAmount(micro_algo=1_000)
        ),
    )
    algorand.send.asset_opt_in(
        algokit_utils.AssetOptInParams(sender=creator.address, asset_id=asset)
    )
    algorand.send.asset_transfer(
        algokit_utils.AssetTransferParams(
            sender=issuer.address, receiver=creator.address, asset_id=asset, amount=BONUS * 2
        )
    )
    keeper.send.top_up_asset(
        args=TopUpAssetArgs(
            upkeep_id=upkeep_id,
            asset_funding=algorand.create_transaction.asset_transfer(
                algokit_utils.AssetTransferParams(
                    sender=creator.address, receiver=keeper.app_address,
                    asset_id=asset, amount=BONUS * 2,
                )
            ),
        )
    )
    upkeep, _ = _read_upkeep(algorand, keeper.app_id, upkeep_id)
    _assert("bonus recorded", upkeep.asset_balance, BONUS * 2)
    held = algod.account_asset_info(keeper.app_address, asset)["asset-holding"]["amount"]
    _assert("and actually held", held, BONUS * 2)

    logger.info("── 3. The issuer takes it back, then destroys the asset ──")
    # Two variants matter here and only one is reachable. A clawback cannot
    # also close the target's opt-in: the protocol rejects that outright with
    # "cannot close asset by clawback", so the app's holding can be emptied
    # but not removed. Destroying the asset afterwards is allowed, and leaves
    # the app opted in to something that no longer exists.
    algorand.send.asset_transfer(
        algokit_utils.AssetTransferParams(
            sender=issuer.address, receiver=issuer.address,
            clawback_target=keeper.app_address, asset_id=asset, amount=BONUS * 2,
        )
    )
    algorand.send.asset_destroy(
        algokit_utils.AssetDestroyParams(sender=issuer.address, asset_id=asset)
    )
    logger.info("   Asset destroyed while the app is still opted in to it.")
    gone = False
    try:
        algod.asset_info(asset)
    except Exception:
        gone = True
    _assert("the asset no longer exists", gone, True)
    upkeep, _ = _read_upkeep(algorand, keeper.app_id, upkeep_id)
    _assert("but the book still says it does", upkeep.asset_balance, BONUS * 2)

    logger.info("── 4. Cancelling still returns the ALGO ──")
    # This is the whole point. Trusting the book value would make the asset
    # transfer fail, and it shares a transaction with the ALGO refund, so the
    # creator would lose their escrow and their box minimum balance to
    # somebody else's asset settings, permanently.
    before = algod.account_info(creator.address)["amount"]
    refund = keeper.send.cancel(
        args=CancelArgs(upkeep_id=upkeep_id),
        params=algokit_utils.CommonAppCallParams(
            extra_fee=algokit_utils.AlgoAmount(micro_algo=2_000)
        ),
    ).abi_return
    after = algod.account_info(creator.address)["amount"]
    _assert("the escrow and box MBR came back", refund > 0, True)
    _assert("and actually reached the creator", after > before, True)

    logger.info("── 5. An execution still happens when the bonus cannot be paid ──")
    # cancel was covered above; execute pays the bonus on a different path and
    # was only ever covered by a unit test using an upkeep whose bonus was
    # never funded. Here the bonus is really funded and really taken back, so
    # the keeper's ALGO fee and the inner call to the target have to survive
    # an asset transfer that cannot happen.
    third_issuer = algorand.account.random()
    algorand.send.payment(
        algokit_utils.PaymentParams(
            sender=creator.address, receiver=third_issuer.address,
            amount=algokit_utils.AlgoAmount(micro_algo=2_000_000),
        )
    )
    third_asset = algorand.send.asset_create(
        algokit_utils.AssetCreateParams(
            sender=third_issuer.address, total=1_000_000, decimals=0,
            asset_name="Taken Points", unit_name="TKP", clawback=third_issuer.address,
        )
    ).asset_id
    third = keeper.send.register(
        args=RegisterArgs(
            mbr_payment=payment(_box_mbr([call_data])),
            funding_payment=payment(FEE * 4),
            target_app=pulse.app_id,
            call_args=[call_data],
            interval_rounds=INTERVAL,
            fee_per_execution=FEE,
            policy=CATCH_UP,
            fee_cap=0,
            fee_asset=third_asset,
            asset_fee=BONUS,
        )
    ).abi_return
    keeper.send.opt_in_asset(
        args=OptInAssetArgs(
            mbr_payment=payment(ASSET_OPT_IN_MBR), upkeep_id=third, asset=third_asset
        ),
        params=algokit_utils.CommonAppCallParams(
            extra_fee=algokit_utils.AlgoAmount(micro_algo=1_000)
        ),
    )
    algorand.send.asset_opt_in(
        algokit_utils.AssetOptInParams(sender=creator.address, asset_id=third_asset)
    )
    algorand.send.asset_transfer(
        algokit_utils.AssetTransferParams(
            sender=third_issuer.address, receiver=creator.address,
            asset_id=third_asset, amount=BONUS * 2,
        )
    )
    keeper.send.top_up_asset(
        args=TopUpAssetArgs(
            upkeep_id=third,
            asset_funding=algorand.create_transaction.asset_transfer(
                algokit_utils.AssetTransferParams(
                    sender=creator.address, receiver=keeper.app_address,
                    asset_id=third_asset, amount=BONUS * 2,
                )
            ),
        )
    )
    algorand.send.asset_transfer(
        algokit_utils.AssetTransferParams(
            sender=third_issuer.address, receiver=third_issuer.address,
            clawback_target=keeper.app_address, asset_id=third_asset, amount=BONUS * 2,
        )
    )
    beats_before = pulse.state.global_state.beats
    upkeep, _ = _read_upkeep(algorand, keeper.app_id, third)
    net.wait_for_round(algorand, upkeep.next_execution_round, poker=creator)
    keeper.send.execute(
        args=ExecuteArgs(upkeep_id=third),
        params=algokit_utils.CommonAppCallParams(
            extra_fee=algokit_utils.AlgoAmount(micro_algo=2_000)
        ),
    )
    _assert("the target was still called", pulse.state.global_state.beats, beats_before + 1)
    upkeep, _ = _read_upkeep(algorand, keeper.app_id, third)
    _assert("and the execution was recorded", upkeep.times_executed, 1)

    logger.info("── 6. And a frozen creator can still cancel ──")
    # The other way an issuer can make a transfer fail. A frozen holding still
    # reports a balance, so checking the amount is not enough, and the creator
    # cannot escape by opting out: a frozen account cannot close a holding.
    frozen_issuer = algorand.account.random()
    algorand.send.payment(
        algokit_utils.PaymentParams(
            sender=creator.address, receiver=frozen_issuer.address,
            amount=algokit_utils.AlgoAmount(micro_algo=2_000_000),
        )
    )
    frozen_asset = algorand.send.asset_create(
        algokit_utils.AssetCreateParams(
            sender=frozen_issuer.address, total=1_000_000, decimals=0,
            asset_name="Freeze Points", unit_name="FZP",
            freeze=frozen_issuer.address, manager=frozen_issuer.address,
        )
    ).asset_id
    second = keeper.send.register(
        args=RegisterArgs(
            mbr_payment=payment(_box_mbr([call_data])),
            funding_payment=payment(FEE * 4),
            target_app=pulse.app_id,
            call_args=[call_data],
            interval_rounds=INTERVAL,
            fee_per_execution=FEE,
            policy=CATCH_UP,
            fee_cap=0,
            fee_asset=frozen_asset,
            asset_fee=BONUS,
        )
    ).abi_return
    keeper.send.opt_in_asset(
        args=OptInAssetArgs(
            mbr_payment=payment(ASSET_OPT_IN_MBR), upkeep_id=second, asset=frozen_asset
        ),
        params=algokit_utils.CommonAppCallParams(
            extra_fee=algokit_utils.AlgoAmount(micro_algo=1_000)
        ),
    )
    algorand.send.asset_opt_in(
        algokit_utils.AssetOptInParams(sender=creator.address, asset_id=frozen_asset)
    )
    algorand.send.asset_transfer(
        algokit_utils.AssetTransferParams(
            sender=frozen_issuer.address, receiver=creator.address,
            asset_id=frozen_asset, amount=BONUS * 2,
        )
    )
    keeper.send.top_up_asset(
        args=TopUpAssetArgs(
            upkeep_id=second,
            asset_funding=algorand.create_transaction.asset_transfer(
                algokit_utils.AssetTransferParams(
                    sender=creator.address, receiver=keeper.app_address,
                    asset_id=frozen_asset, amount=BONUS * 2,
                )
            ),
        )
    )
    algorand.send.asset_freeze(
        algokit_utils.AssetFreezeParams(
            sender=frozen_issuer.address, asset_id=frozen_asset,
            account=creator.address, frozen=True,
        )
    )
    logger.info("   The issuer froze the creator, who cannot opt out.")
    refund_two = keeper.send.cancel(
        args=CancelArgs(upkeep_id=second),
        params=algokit_utils.CommonAppCallParams(
            extra_fee=algokit_utils.AlgoAmount(micro_algo=2_000)
        ),
    ).abi_return
    _assert("cancel still returns the ALGO", refund_two > 0, True)

    logger.info("")
    logger.info("Clawback e2e passed.")
    logger.info(f"  Recovered {refund} microAlgos from an upkeep whose bonus was taken.")


if __name__ == "__main__":
    main()
