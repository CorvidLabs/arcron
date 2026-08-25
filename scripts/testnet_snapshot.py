"""Record what a keeper deployment looks like right now, as JSON.

The public site builds from committed snapshots rather than from live calls,
so a page about the network is reproducible: it renders the same way in CI, on
a laptop with no network, and years from now. This writes that snapshot.

It is evidence, not marketing. Everything here is a number anybody can check
against algod for themselves, and the snapshot records the app, the round and
the bytecode hash needed to do it:

    poetry run python -m scripts.testnet_snapshot --network testnet \\
        --app-id 769823086 --out ../site/public/arcron/evidence/testnet.json
"""

import argparse
import base64
import datetime
import hashlib
import json
import logging
import pathlib
import sys

from algosdk.logic import get_application_address

from scripts import network as net
from scripts.keeper_bot import HEAD_BYTES, Upkeep, effective_fee, scan_upkeeps

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1


def _upkeep_json(upkeep: Upkeep, current_round: int) -> dict:
    """One upkeep, with the derived numbers a reader would otherwise compute."""
    fee = effective_fee(upkeep, current_round)
    return {
        "id": upkeep.upkeep_id,
        "targetApp": upkeep.target_app,
        "intervalRounds": upkeep.interval_rounds,
        "nextExecutionRound": upkeep.next_execution_round,
        "timesExecuted": upkeep.times_executed,
        "policy": "catch-up" if upkeep.policy == 0 else "skip-ahead",
        "feePerExecutionMicroAlgos": upkeep.fee_per_execution,
        "feeCapMicroAlgos": upkeep.fee_cap,
        "effectiveFeeMicroAlgos": fee,
        "balanceMicroAlgos": upkeep.balance,
        # What the escrow can still pay for at the fee it would be charged now.
        "executionsFunded": upkeep.balance // fee if fee > 0 else None,
        "dueInRounds": max(0, upkeep.next_execution_round - current_round),
        "feeAsset": upkeep.fee_asset or None,
        "assetFee": upkeep.asset_fee or None,
        "assetBalance": upkeep.asset_balance or None,
    }


def snapshot(algorand, app_id: int, network_name: str) -> dict:
    algod = algorand.client.algod
    status = algod.status()
    current_round = status["last-round"]

    application = algod.application_info(app_id)
    approval = base64.b64decode(application["params"]["approval-program"])
    clear = base64.b64decode(application["params"]["clear-state-program"])

    address = get_application_address(app_id)
    account = algod.account_info(address)

    upkeeps = scan_upkeeps(algod, app_id)
    escrowed = sum(upkeep.balance for upkeep in upkeeps)
    spendable = account["amount"] - account["min-balance"]

    return {
        "schemaVersion": SCHEMA_VERSION,
        "network": network_name,
        "appId": app_id,
        "appAddress": address,
        "capturedAt": datetime.datetime.now(datetime.timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "capturedAtRound": current_round,
        "contract": {
            "approvalBytes": len(approval),
            "clearBytes": len(clear),
            # The same digest `scripts/verify_build.py` prints, so a reader can
            # tie this snapshot to a commit without trusting the page.
            "sha256": hashlib.sha256(approval + b"\x00" + clear).hexdigest(),
            "structHeadBytes": HEAD_BYTES,
        },
        "account": {
            "microAlgos": account["amount"],
            "minBalanceMicroAlgos": account["min-balance"],
            "spendableMicroAlgos": spendable,
        },
        "registry": {
            "upkeeps": len(upkeeps),
            "totalExecutions": sum(upkeep.times_executed for upkeep in upkeeps),
            "totalEscrowedMicroAlgos": escrowed,
            # The invariant the console shows: the app must be able to pay out
            # every µALGO it holds in escrow.
            "solvent": spendable >= escrowed,
            "entries": [_upkeep_json(upkeep, current_round) for upkeep in sorted(upkeeps, key=lambda u: u.upkeep_id)],
        },
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    net.add_network_argument(parser)
    parser.add_argument("--app-id", type=int, required=True, help="keeper app to record")
    parser.add_argument("--out", type=pathlib.Path, help="write here (default: stdout)")
    arguments = parser.parse_args(argv)

    algorand = net.connect(arguments.network)
    recorded = snapshot(algorand, arguments.app_id, arguments.network)

    rendered = json.dumps(recorded, indent=2) + "\n"
    if arguments.out is None:
        sys.stdout.write(rendered)
    else:
        arguments.out.parent.mkdir(parents=True, exist_ok=True)
        arguments.out.write_text(rendered)
        registry = recorded["registry"]
        logger.info(
            f"Wrote {arguments.out}: app {recorded['appId']} at round "
            f"{recorded['capturedAtRound']}, {registry['upkeeps']} upkeeps, "
            f"{registry['totalExecutions']} executions, solvent={registry['solvent']}"
        )


if __name__ == "__main__":
    main()
