"""Spike: can a target re-enter Arcron's `execute` from inside its own call?

`execute` writes the upkeep's state before it submits the inner app call, so a
re-entrant execution has to satisfy the schedule afresh. Under `SKIP_AHEAD`
that is the end of it — the upkeep is no longer due. Under `CATCH_UP` a
neglected upkeep still has a backlog when the target is running, so the
question is real, and the answer decides whether a keeper may safely reference
more than one upkeep box in a transaction.

The second half of the question is who a nested execution pays. Inside the
inner call, `Txn.sender` is whatever submitted it — the *target*, not the
keeper — so a re-entrant `execute` pays the target out of the creator's
escrow.

Run:  poetry run python -m scripts.spike_reentrancy [--network localnet]
"""

import argparse
import logging

import algokit_utils
from algosdk import abi, transaction

from scripts import keeper_bot, network as net
from scripts.keeper_e2e import _box_mbr, _read_upkeep, _selector
from smart_contracts.artifacts.keeper.keeper_client import CancelArgs, RegisterArgs
from smart_contracts.artifacts.resource_probe.resource_probe_client import (
    ConfigureReentryArgs,
)
from smart_contracts.keeper.deploy_config import deploy as deploy_keeper
from smart_contracts.resource_probe.deploy_config import deploy as deploy_probe

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

FEE = 4_000
INTERVAL = 10
# Generous: the keeper's call, Arcron's inner call to the probe, the probe's
# re-entrant call, Arcron's inner call from *that*, and two payments.
EXECUTE_FEE = 20_000
CALL_SIGNATURE = "reenter()uint64"


def _register(algorand, keeper_client, deployer, target_app: int, policy: int) -> int:
    call_data = _selector(CALL_SIGNATURE)
    first_valid = algorand.client.algod.status()["last-round"]

    def payment(amount: int):
        return algorand.create_transaction.payment(
            algokit_utils.PaymentParams(
                sender=deployer.address,
                receiver=keeper_client.app_address,
                amount=algokit_utils.AlgoAmount(micro_algo=amount),
                first_valid_round=first_valid,
                last_valid_round=first_valid + 1_000,
            )
        )

    return keeper_client.send.register(
        args=RegisterArgs(
            mbr_payment=payment(_box_mbr(call_data)),
            funding_payment=payment(FEE * 10),
            target_app=target_app,
            call_data=call_data,
            interval_rounds=INTERVAL,
            fee_per_execution=FEE,
            policy=policy,
            fee_cap=0,
        )
    ).abi_return


def _execute(algorand, app_id: int, account, upkeep_id: int, target_app: int) -> str:
    method = abi.Method.from_signature("execute(uint64)uint64")
    params = algorand.client.algod.suggested_params()
    params.flat_fee = True
    params.fee = EXECUTE_FEE
    txn = transaction.ApplicationNoOpTxn(
        sender=account.address,
        sp=params,
        index=app_id,
        app_args=[method.get_selector(), upkeep_id.to_bytes(8, "big")],
        boxes=[(0, b"u" + upkeep_id.to_bytes(8, "big"))],
        foreign_apps=[target_app],
    )
    signed = account.signer.sign_transactions([txn], [0])
    txid = algorand.client.algod.send_transactions(signed)
    transaction.wait_for_confirmation(algorand.client.algod, txid, 6)
    return txid


def _balance(algorand, address: str) -> int:
    return algorand.client.algod.account_info(address)["amount"]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    net.add_network_argument(parser)
    args = parser.parse_args(argv)

    algorand = net.connect(args.network)
    deployer = algorand.account.from_environment("DEPLOYER")
    keeper_client = deploy_keeper()
    probe = deploy_probe()
    app_id = keeper_client.app_id

    for label, policy, neglect in (
        ("SKIP_AHEAD, three intervals of backlog", keeper_bot.SKIP_AHEAD, 3),
        ("CATCH_UP, no backlog", keeper_bot.CATCH_UP, 0),
        ("CATCH_UP, three intervals of backlog", keeper_bot.CATCH_UP, 3),
    ):
        upkeep_id = _register(algorand, keeper_client, deployer, probe.app_id, policy)
        probe.send.configure_reentry(
            args=ConfigureReentryArgs(keeper_app=app_id, upkeep_id=upkeep_id)
        )
        upkeep, _ = _read_upkeep(algorand, app_id, upkeep_id)
        net.wait_for_round(
            algorand, upkeep.next_execution_round + neglect * INTERVAL, poker=deployer
        )
        before, _ = _read_upkeep(algorand, app_id, upkeep_id)
        probe_before = _balance(algorand, probe.app_address)
        keeper_before = _balance(algorand, deployer.address)
        try:
            _execute(algorand, app_id, deployer, upkeep_id, probe.app_id)
        except Exception as exc:  # a rejection is a result, not a failure
            text = str(exc).replace("\n", " ")
            index = max(text.find("logic eval error"), 0)
            logger.info(f"{label:<40} rejected — {text[index:index + 140]}")
        else:
            after, _ = _read_upkeep(algorand, app_id, upkeep_id)
            drained = before.balance - after.balance
            to_probe = _balance(algorand, probe.app_address) - probe_before
            logger.info(
                f"{label:<40} accepted — escrow -{drained} µALGO, "
                f"executions +{after.times_executed - before.times_executed}, "
                f"target received {to_probe} µALGO"
            )
        keeper_client.send.cancel(
            args=CancelArgs(upkeep_id=upkeep_id),
            params=algokit_utils.CommonAppCallParams(
                extra_fee=algokit_utils.AlgoAmount(micro_algo=1_000)
            ),
        )


if __name__ == "__main__":
    main()
