"""Regression: the real keeper bot services a six-reference target and
refuses a seven-reference one, pinning the boundary `docs/arcron.md`
documents.

`smart_contracts/sim_probe/` exists for exactly this. `needs_six` reads six
accounts named nowhere in the call, which fits the six references Arcron
leaves a target once its own two (the upkeep's box and the target app) are
set aside. `needs_seven` needs a seventh, and nothing can attach one from
inside a real `execute` -- that is a genuine AVM ceiling, proven by hand in
`scripts/spike_simulate_test_button.py` (section 5b).

That spike proved the AVM side by building the call itself. This proves the
*bot* side: it runs both upkeeps through `scripts.keeper_bot.main`, the same
entry point a deployed keeper uses, rather than a hand-built transaction. If
`_resolve_execute_references` ever regresses back to leaving references to
algokit-utils' own populator (which caps at four direct accounts and refuses
a fifth), needs_six starts failing here instead of only in a spike nobody
runs in CI.

Run:  poetry run python -m scripts.reference_boundary [--network localnet]
"""

import argparse
import logging

import algokit_utils

from scripts import keeper_bot, network as net
from scripts.keeper_e2e import _box_mbr, _read_upkeep, _selector
from smart_contracts.artifacts.keeper.keeper_client import CancelArgs, RegisterArgs
from smart_contracts.artifacts.sim_probe.sim_probe_client import ConfigureSubjectsArgs
from smart_contracts.keeper.deploy_config import deploy as deploy_keeper
from smart_contracts.sim_probe.deploy_config import deploy as deploy_probe

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)
# algokit-utils logs every send failure at ERROR with a full traceback before
# re-raising. needs_seven() failing is the whole point of this script, and
# the keeper bot already reports it (as a WARNING, via its own
# execute_failed event), so the noisier duplicate is silenced here the same
# way scripts/spike_simulate_test_button.py does. Its logger is a standalone
# AlgoKitLogger instance, not one `logging.getLogger` can reach.
from algokit_utils.config import config as _algokit_config  # noqa: E402

_algokit_config.logger.setLevel(logging.CRITICAL)

FEE = 4_000
INTERVAL = 10


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    net.add_network_argument(parser)
    args = parser.parse_args(argv)

    algorand = net.connect(args.network)
    deployer = algorand.account.from_environment("DEPLOYER")
    algod = algorand.client.algod

    keeper_client = deploy_keeper()
    probe_client = deploy_probe()
    app_id = keeper_client.app_id

    # Seven accounts named nowhere in any call -- needs_six reaches for the
    # first six, needs_seven for all of them.
    subjects = [algorand.account.random() for _ in range(7)]
    for subject in subjects:
        algorand.send.payment(
            algokit_utils.PaymentParams(
                sender=deployer.address,
                receiver=subject.address,
                amount=algokit_utils.AlgoAmount(micro_algo=200_000),
            )
        )
    probe_client.send.configure_subjects(
        args=ConfigureSubjectsArgs(
            s0=subjects[0].address,
            s1=subjects[1].address,
            s2=subjects[2].address,
            s3=subjects[3].address,
            s4=subjects[4].address,
            s5=subjects[5].address,
            s6=subjects[6].address,
        )
    )

    def register(signature: str) -> int:
        call_data = _selector(signature)
        first_valid = algod.status()["last-round"]

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
                mbr_payment=payment(_box_mbr([call_data])),
                funding_payment=payment(FEE * 5),
                target_app=probe_client.app_id,
                call_args=[call_data],
                interval_rounds=INTERVAL,
                fee_per_execution=FEE,
                policy=0,  # CATCH_UP
                fee_cap=0,
                fee_asset=0,
                asset_fee=0,
            )
        ).abi_return

    logger.info("Registering a needs_six() upkeep and a needs_seven() upkeep …")
    six_id = register("needs_six()uint64")
    seven_id = register("needs_seven()uint64")

    six_before, _ = _read_upkeep(algorand, app_id, six_id)
    seven_before, _ = _read_upkeep(algorand, app_id, seven_id)
    due_round = max(six_before.next_execution_round, seven_before.next_execution_round)
    net.wait_for_round(algorand, due_round, poker=deployer)

    logger.info(f"Running the real keeper bot (app {app_id}) against both …")
    keeper_bot.main(["--once", "--network", args.network, "--app-id", str(app_id)])

    six_after, _ = _read_upkeep(algorand, app_id, six_id)
    seven_after, _ = _read_upkeep(algorand, app_id, seven_id)

    assert six_after.times_executed == 1, (
        f"needs_six() should have been serviced by the bot in one scan "
        f"(times_executed={six_after.times_executed}). Six references fit "
        f"what a real execute() leaves a target (docs/arcron.md); if this "
        f"fails, the bot has regressed to leaving references to algokit-"
        f"utils' own populator, which caps at four direct accounts -- see "
        f"scripts.keeper_bot._resolve_execute_references."
    )
    logger.info(f"  ✔ needs_six(): serviced (times_executed={six_after.times_executed})")

    assert seven_after.times_executed == 0, (
        f"needs_seven() should never be serviceable -- it needs a seventh "
        f"reference beyond what Arcron leaves any target, a real AVM "
        f"ceiling (docs/arcron.md), not a bot limitation -- but "
        f"times_executed={seven_after.times_executed}."
    )
    logger.info(
        f"  ✔ needs_seven(): still refused (times_executed={seven_after.times_executed})"
    )

    keeper_client.send.cancel(
        args=CancelArgs(upkeep_id=six_id),
        params=algokit_utils.CommonAppCallParams(
            extra_fee=algokit_utils.AlgoAmount(micro_algo=1_000)
        ),
    )
    keeper_client.send.cancel(
        args=CancelArgs(upkeep_id=seven_id),
        params=algokit_utils.CommonAppCallParams(
            extra_fee=algokit_utils.AlgoAmount(micro_algo=1_000)
        ),
    )
    logger.info("Reference boundary holds: six is serviced, seven is refused.")


if __name__ == "__main__":
    main()
