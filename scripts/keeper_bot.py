"""Permissionless keeper bot for the Keeper network.

Scans the Keeper app's upkeep boxes each round, executes every upkeep that
is due and funded, and collects the per-execution fees. Loops block-by-block
by default, or runs a single scan with --once.

Picks its network with --network (or ARCHON_NETWORK), loading .env.localnet
or .env.testnet. Signs as the account from KEEPER_MNEMONIC if set, else
DEPLOYER_MNEMONIC; execution fees are paid to that account. On LocalNet both
come from KMD, so no mnemonic is needed.

Run:  poetry run python -m scripts.keeper_bot [--once] [--network N] [--app-id N]
"""

import argparse
import base64
import logging
import os
import time
from dataclasses import dataclass

import algokit_utils

from scripts import network as net
from smart_contracts.artifacts.keeper.keeper_client import (
    ExecuteArgs,
    KeeperClient,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# The canonical TestNet Keeper app (see README); override with --app-id or
# KEEPER_APP_ID. LocalNet has no canonical app — pass one.
DEFAULT_APP_ID = 769772891
# Covers the two inner transactions (app call + keeper payment); the outer
# fee is the standard 1,000 µALGO.
EXTRA_FEE_MICROALGO = 2_000
# Delay before retrying after an algod/endpoint error.
ERROR_RETRY_SECONDS = 5


@dataclass
class Upkeep:
    upkeep_id: int
    target_app: int
    interval_rounds: int
    next_execution_round: int
    fee_per_execution: int
    balance: int
    times_executed: int


def _as_bytes(value: object) -> bytes:
    # algosdk returns box names/values as bytes or base64 str depending on
    # version; accept both.
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    return base64.b64decode(value)  # type: ignore[arg-type]


def _decode_upkeep(upkeep_id: int, raw: bytes) -> Upkeep:
    """Decode a box value of the contract's Upkeep ARC-4 struct.

    ABI head/tail layout (see smart_contracts/keeper/contract.py): a 32-byte
    creator, then the static fields inline, with the dynamic call_data in the
    tail (the offset at bytes [40:42] points to it; the bot doesn't need it —
    the contract stores and sends it itself).
    """
    return Upkeep(
        upkeep_id=upkeep_id,
        target_app=int.from_bytes(raw[32:40], "big"),
        interval_rounds=int.from_bytes(raw[42:50], "big"),
        next_execution_round=int.from_bytes(raw[50:58], "big"),
        fee_per_execution=int.from_bytes(raw[58:66], "big"),
        balance=int.from_bytes(raw[66:74], "big"),
        times_executed=int.from_bytes(raw[74:82], "big"),
    )


def scan_upkeeps(algod, app_id: int) -> list[Upkeep]:
    upkeeps: list[Upkeep] = []
    token: str | None = None
    while True:  # paginate the box list
        kwargs = {"next": token} if token else {}
        page = algod.application_boxes(app_id, **kwargs)
        for box in page["boxes"]:
            name = _as_bytes(box["name"])
            if name[:1] != b"u":
                continue
            raw = _as_bytes(algod.application_box_by_name(app_id, name)["value"])
            upkeeps.append(_decode_upkeep(int.from_bytes(name[1:9], "big"), raw))
        token = page.get("next-token") or None
        if not token:
            return upkeeps


def resolve_app_id(parser: argparse.ArgumentParser, app_id: int | None, network: str) -> int:
    """The app to service: --app-id, else KEEPER_APP_ID, else the TestNet app."""
    if app_id is not None:
        return app_id
    from_env = os.environ.get("KEEPER_APP_ID")
    if from_env:
        return int(from_env)
    if network == net.TESTNET:
        return DEFAULT_APP_ID
    parser.error(
        f"--app-id (or KEEPER_APP_ID) is required on {network}; there is no "
        f"canonical app id off TestNet"
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--once", action="store_true", help="run a single scan, then exit"
    )
    net.add_network_argument(parser)
    parser.add_argument(
        "--app-id",
        type=int,
        default=None,
        help="Keeper app id (default: KEEPER_APP_ID, else the TestNet app)",
    )
    args = parser.parse_args(argv)

    algorand = net.connect(args.network)
    app_id = resolve_app_id(parser, args.app_id, args.network)
    try:
        keeper = algorand.account.from_environment("KEEPER")
    except Exception:
        keeper = algorand.account.from_environment("DEPLOYER")
    algod = algorand.client.algod
    client = KeeperClient(
        algorand=algorand,
        app_id=app_id,
        default_sender=keeper.address,
        default_signer=keeper.signer,
    )
    logger.info(f"Keeper {keeper.address} servicing app {app_id}")

    # Upkeeps that failed this run. A failed execution is free — Algorand
    # rejects it before it reaches a block, so nothing is spent — but retrying
    # a target that keeps rejecting wastes a round-trip every round and
    # crowds the transaction pool, so it is skipped for the rest of the run.
    failed: set[int] = set()
    while True:
        try:
            current = algod.status()["last-round"]
            upkeeps = scan_upkeeps(algod, app_id)
            due = [
                u
                for u in upkeeps
                if u.upkeep_id not in failed
                and current >= u.next_execution_round
                and u.balance >= u.fee_per_execution
            ]
            logger.info(
                f"Round {current}: {len(upkeeps)} upkeeps, {len(due)} due"
            )
            for upkeep in due:
                try:
                    response = client.send.execute(
                        args=ExecuteArgs(upkeep_id=upkeep.upkeep_id),
                        params=algokit_utils.CommonAppCallParams(
                            extra_fee=algokit_utils.AlgoAmount(
                                micro_algo=EXTRA_FEE_MICROALGO
                            )
                        ),
                    )
                    logger.info(
                        f"Executed upkeep {upkeep.upkeep_id} "
                        f"(target app {upkeep.target_app}); "
                        f"+{upkeep.fee_per_execution} µALGO, "
                        f"next due round {response.abi_return}"
                    )
                except Exception as exc:
                    failed.add(upkeep.upkeep_id)
                    logger.warning(
                        f"Upkeep {upkeep.upkeep_id} failed (no fee charged); "
                        f"skipping it for this run: {exc}"
                    )
            if args.once:
                return
            algod.status_after_block(current + 1)
        except Exception as exc:
            if args.once:
                raise
            logger.warning(f"{exc}; retrying in {ERROR_RETRY_SECONDS}s")
            time.sleep(ERROR_RETRY_SECONDS)


if __name__ == "__main__":
    main()
