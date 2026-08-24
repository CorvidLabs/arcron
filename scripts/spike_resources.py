"""Spike: what resources can an Archon-triggered inner call actually reach?

Archon's `execute` submits an inner app call with no foreign arrays. The docs
called that a v1 limitation without establishing what it forbids, so this
measures it: a probe app reaches for an account, an asset and a third app that
no argument names, and each pattern is run twice —

1. **bare** — the keeper sends `execute` with only what Archon needs;
2. **keeper-supplied** — the keeper adds resource references to its own
   transaction, supplying *availability* without supplying data.

If (2) succeeds where (1) fails, keepers can unlock resources without touching
the trust model: the creator still fixes what is called.

Run:  poetry run python -m scripts.spike_resources [--network localnet]
"""

import argparse
import logging

import algokit_utils
from algosdk import abi, transaction

from scripts import keeper_bot, network as net
from scripts.keeper_e2e import _box_mbr, _read_upkeep, _selector
from smart_contracts.artifacts.keeper.keeper_client import CancelArgs, RegisterArgs
from smart_contracts.artifacts.resource_probe.resource_probe_client import ConfigureArgs
from smart_contracts.keeper.deploy_config import deploy as deploy_keeper
from smart_contracts.pulse.deploy_config import deploy as deploy_pulse
from smart_contracts.resource_probe.deploy_config import deploy as deploy_probe

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

FEE = 4_000
INTERVAL = 10
# Generous: the group carries Archon's inner call, the probe's own inner
# transaction and the keeper's payment.
EXECUTE_FEE = 8_000

PROBES = (
    ("inner payment to an unreferenced account", "probe_payment()uint64"),
    ("inner asset transfer to an unreferenced account", "probe_asset_transfer()uint64"),
    ("read an unreferenced account's ALGO balance", "probe_read_balance()uint64"),
    ("read an unreferenced account's asset holding", "probe_read_holding()uint64"),
    ("inner call to an unreferenced app", "probe_app_call()uint64"),
)


def _register(algorand, keeper_client, deployer, target_app: int, call_data: bytes) -> int:
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
            funding_payment=payment(FEE * 3),
            target_app=target_app,
            call_data=call_data,
            interval_rounds=INTERVAL,
            fee_per_execution=FEE,
            policy=0,  # CATCH_UP; this spike measures resources, not scheduling
            fee_cap=0,
        )
    ).abi_return


def _execute(algorand, app_id: int, account, upkeep_id: int, target_app: int, *, refs) -> str:
    """Send `execute` by hand so the keeper's own resource references can vary."""
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
        foreign_apps=[target_app, *refs.get("apps", [])],
        accounts=refs.get("accounts", []),
        foreign_assets=refs.get("assets", []),
    )
    signed = account.signer.sign_transactions([txn], [0])
    txid = algorand.client.algod.send_transactions(signed)
    transaction.wait_for_confirmation(algorand.client.algod, txid, 6)
    return txid


def _summarise(error: str) -> str:
    """The part of an algod rejection that names the rule that was broken."""
    for marker in ("unavailable", "not opted in", "logic eval error", "invalid"):
        index = error.lower().find(marker)
        if index != -1:
            return error[index : index + 90].strip().replace("\n", " ")
    return error.strip()[:90]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    net.add_network_argument(parser)
    args = parser.parse_args(argv)

    algorand = net.connect(args.network)
    deployer = algorand.account.from_environment("DEPLOYER")
    algod = algorand.client.algod
    logger.info(f"algod build: {algod.versions()['build']}")

    keeper_client = deploy_keeper()
    pulse_client = deploy_pulse()
    probe_client = deploy_probe()

    # An account that appears in no argument anywhere.
    subject = algorand.account.random()
    algorand.send.payment(
        algokit_utils.PaymentParams(
            sender=deployer.address,
            receiver=subject.address,
            amount=algokit_utils.AlgoAmount(micro_algo=400_000),
        )
    )
    # An asset the probe holds and the subject can receive.
    asset_id = algorand.send.asset_create(
        algokit_utils.AssetCreateParams(sender=deployer.address, total=1_000_000)
    ).asset_id
    algorand.send.asset_opt_in(
        algokit_utils.AssetOptInParams(sender=subject.address, asset_id=asset_id)
    )
    algorand.send.payment(
        algokit_utils.PaymentParams(
            sender=deployer.address,
            receiver=probe_client.app_address,
            amount=algokit_utils.AlgoAmount(micro_algo=400_000),
        )
    )
    probe_client.send.configure(
        args=ConfigureArgs(
            subject=subject.address, asset=asset_id, app=pulse_client.app_id
        )
    )
    probe_client.send.opt_in_to_asset(
        params=algokit_utils.CommonAppCallParams(
            extra_fee=algokit_utils.AlgoAmount(micro_algo=1_000),
            asset_references=[asset_id],
        )
    )
    algorand.send.asset_transfer(
        algokit_utils.AssetTransferParams(
            sender=deployer.address,
            receiver=probe_client.app_address,
            asset_id=asset_id,
            amount=1_000,
        )
    )
    logger.info(
        f"Keeper {keeper_client.app_id}, probe {probe_client.app_id}, "
        f"pulse {pulse_client.app_id}, asset {asset_id}, subject {subject.address}"
    )

    results: list[tuple[str, str, str]] = []
    for label, signature in PROBES:
        call_data = _selector(signature)
        outcomes: dict[str, str] = {}
        for mode, refs in (
            ("bare", {}),
            (
                "keeper-supplied",
                {
                    "accounts": [subject.address],
                    "assets": [asset_id],
                    "apps": [pulse_client.app_id],
                },
            ),
        ):
            upkeep_id = _register(
                algorand, keeper_client, deployer, probe_client.app_id, call_data
            )
            upkeep, _ = _read_upkeep(algorand, keeper_client.app_id, upkeep_id)
            net.wait_for_round(algorand, upkeep.next_execution_round, poker=deployer)
            try:
                _execute(
                    algorand,
                    keeper_client.app_id,
                    deployer,
                    upkeep_id,
                    probe_client.app_id,
                    refs=refs,
                )
                outcomes[mode] = "works"
            except Exception as exc:
                outcomes[mode] = f"fails — {_summarise(str(exc))}"
            keeper_client.send.cancel(
                args=CancelArgs(upkeep_id=upkeep_id),
                params=algokit_utils.CommonAppCallParams(
                    extra_fee=algokit_utils.AlgoAmount(micro_algo=1_000)
                ),
            )
        results.append((label, outcomes["bare"], outcomes["keeper-supplied"]))
        logger.info(f"{label}: bare={outcomes['bare']} | refs={outcomes['keeper-supplied']}")

    logger.info("")
    logger.info("| Resource pattern | Bare `execute` | Keeper supplies references |")
    logger.info("|------------------|----------------|----------------------------|")
    for label, bare, supplied in results:
        logger.info(f"| {label} | {bare} | {supplied} |")


if __name__ == "__main__":
    main()
