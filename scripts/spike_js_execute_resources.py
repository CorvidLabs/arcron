"""Spike: does the JS `execute()` service an upkeep whose target reaches an
account or an asset holding that no argument names, now that it simulates
first? (#103)

`js/src/keeper-txns.ts` used to attach only the target app and the fee asset
and never simulate, so an upkeep whose target reached any further account,
asset or app would fail from that client while the Python bot serviced it
fine. This deploys Keeper and ResourceProbe, points two ResourceProbe reads at
resources named nowhere in the call, and for each one registers, waits for the
upkeep to come due, then runs `js/scripts/execute-probe.ts` twice against that
same due upkeep:

1. **naive** -- the call shape `execute()` used before the fix: target app
   and box, nothing discovered. Expected to fail.
2. **fixed** -- the real, current `execute()`, which simulates first and
   attaches what algod reports. Expected to succeed.

The probe methods used are reads (`probe_read_balance`, `probe_read_holding`),
not the payment/transfer/app-call probes, so none of them needs extra
inner-fee budget beyond what `execute()` already books: `probe_app_call`
itself needs a real fee for its own inner call to Pulse, which is a
pre-existing constraint of that probe unrelated to resource discovery, and
would need a fee bump this harness has no way to hand the shipped `execute()`.
The "an app" branch of `foldUnnamedResources` -- decomposing `unnamed.apps`,
box app references and `appLocals` -- is exercised instead by the pure unit
test in `js/test/keeper-txns.test.ts`, which needs no chain.

Run:  poetry run python -m scripts.spike_js_execute_resources
"""

import argparse
import json
import logging
import subprocess

import algokit_utils

from scripts import network as net
from scripts.keeper_e2e import _box_mbr, _read_upkeep, _selector
from smart_contracts.artifacts.keeper.keeper_client import CancelArgs, RegisterArgs
from smart_contracts.artifacts.resource_probe.resource_probe_client import ConfigureArgs
from smart_contracts.keeper.deploy_config import deploy as deploy_keeper
from smart_contracts.resource_probe.deploy_config import deploy as deploy_probe

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

FEE = 4_000
INTERVAL = 10

PROBES = (
    ("an account no argument names", "probe_read_balance()uint64"),
    ("an asset holding no argument names", "probe_read_holding()uint64"),
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
            mbr_payment=payment(_box_mbr([call_data])),
            funding_payment=payment(FEE * 3),
            target_app=target_app,
            call_args=[call_data],
            interval_rounds=INTERVAL,
            fee_per_execution=FEE,
            policy=0,
            fee_cap=0,
            fee_asset=0,
            asset_fee=0,
        )
    ).abi_return


def _run_js(keeper_app_id: int, upkeep_id: int, probe_app_id: int, mode: str) -> dict:
    proc = subprocess.run(
        [
            "bun", "run", "js/scripts/execute-probe.ts",
            str(keeper_app_id), str(upkeep_id), str(probe_app_id), mode,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    lines = [line for line in proc.stdout.strip().splitlines() if line.startswith("{")]
    if not lines:
        return {"ok": False, "mode": mode, "error": (proc.stdout + proc.stderr).strip()}
    return json.loads(lines[-1])


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    # This spike shells out to a KMD-signed bun script, so it only makes
    # sense against LocalNet; TestNet/MainNet have no KMD to sign with.
    algorand = net.connect(net.LOCALNET)
    deployer = algorand.account.from_environment("DEPLOYER")

    keeper_client = deploy_keeper()
    probe_client = deploy_probe()
    logger.info(f"Keeper {keeper_client.app_id}, probe {probe_client.app_id}")

    # An account that appears in no argument anywhere.
    subject = algorand.account.random()
    algorand.send.payment(
        algokit_utils.PaymentParams(
            sender=deployer.address,
            receiver=subject.address,
            amount=algokit_utils.AlgoAmount(micro_algo=400_000),
        )
    )
    algorand.send.payment(
        algokit_utils.PaymentParams(
            sender=deployer.address,
            receiver=probe_client.app_address,
            amount=algokit_utils.AlgoAmount(micro_algo=200_000),
        )
    )
    # An asset the probe holds and the subject can receive, for probe_read_holding.
    asset_id = algorand.send.asset_create(
        algokit_utils.AssetCreateParams(sender=deployer.address, total=1_000_000)
    ).asset_id
    algorand.send.asset_opt_in(
        algokit_utils.AssetOptInParams(sender=subject.address, asset_id=asset_id)
    )
    probe_client.send.configure(
        args=ConfigureArgs(subject=subject.address, asset=asset_id, app=0)
    )

    results: dict[str, dict] = {}
    for label, signature in PROBES:
        call_data = _selector(signature)
        for mode in ("naive", "fixed"):
            upkeep_id = _register(algorand, keeper_client, deployer, probe_client.app_id, call_data)
            upkeep, _ = _read_upkeep(algorand, keeper_client.app_id, upkeep_id)
            net.wait_for_round(algorand, upkeep.next_execution_round, poker=deployer)

            probes_before = int(probe_client.state.global_state.probes_run)
            result = _run_js(keeper_client.app_id, upkeep_id, probe_client.app_id, mode)
            probes_after = int(probe_client.state.global_state.probes_run)
            result["probe_ran"] = probes_after > probes_before
            results[f"{label} / {mode}"] = result
            summary = result.get("error", "")[:200]
            logger.info(f"{label} / {mode}: ok={result['ok']} probe_ran={result['probe_ran']} {summary}")

            keeper_client.send.cancel(
                args=CancelArgs(upkeep_id=upkeep_id),
                params=algokit_utils.CommonAppCallParams(
                    extra_fee=algokit_utils.AlgoAmount(micro_algo=1_000)
                ),
            )

    logger.info("")
    logger.info("| Probe | naive | fixed |")
    logger.info("|-------|-------|-------|")
    for label, _ in PROBES:
        naive = "works" if results[f"{label} / naive"]["ok"] else "fails"
        fixed = "works" if results[f"{label} / fixed"]["ok"] else "fails"
        logger.info(f"| {label} | {naive} | {fixed} |")

    for label, _ in PROBES:
        naive_result = results[f"{label} / naive"]
        fixed_result = results[f"{label} / fixed"]
        assert not naive_result["ok"], (
            f"{label}: the naive shape (target app + box, nothing discovered) was "
            "expected to fail against an unreferenced resource -- if it did not, "
            "the resource_probe target stopped reaching for anything unavailable"
        )
        assert fixed_result["ok"], f"{label}: the fixed execute() was expected to succeed"
        assert fixed_result["probe_ran"], f"{label}: the fixed execute() succeeded but the probe never ran"

    logger.info("PASS: naive fails on every unreferenced resource, fixed discovers each one and succeeds")


if __name__ == "__main__":
    main()
