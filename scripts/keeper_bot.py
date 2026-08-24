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
import json
import logging
import os
import signal
import time
from dataclasses import dataclass
from types import FrameType

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
DEFAULT_APP_ID = 769802474
# Covers the two inner transactions (app call + keeper payment); the outer
# fee is the standard 1,000 µALGO.
EXTRA_FEE_MICROALGO = 2_000
# First delay after an algod/endpoint error; it doubles up to the cap, so a
# node that is down does not get hammered and a blip costs almost nothing.
ERROR_RETRY_SECONDS = 5
MAX_ERROR_RETRY_SECONDS = 60


class Emitter:
    """Human lines by default; one JSON object per line for log shipping.

    A keeper's logs have to answer "did upkeep N fire, and when?" months
    later, so every event carries the round and the upkeep it concerns.
    """

    def __init__(self, as_json: bool = False) -> None:
        self.as_json = as_json

    def __call__(self, event: str, message: str, level: int = logging.INFO, **fields) -> None:
        if self.as_json:
            logger.log(level, json.dumps({"event": event, **fields}, default=str))
        else:
            logger.log(level, message)


emit = Emitter()


class Shutdown:
    """SIGTERM means finish what you are doing, then stop.

    A redeploy must not abandon a half-signed execution, so the flag is
    checked between upkeeps rather than interrupting one.
    """

    def __init__(self) -> None:
        self.requested = False

    def install(self) -> None:
        for received in (signal.SIGTERM, signal.SIGINT):
            signal.signal(received, self._request)

    def _request(self, signum: int, frame: FrameType | None) -> None:
        if self.requested:  # a second signal means "now"
            raise KeyboardInterrupt
        self.requested = True
        emit(
            "shutdown_requested",
            f"Signal {signum} received; finishing the current scan then exiting",
            signal=signum,
        )


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
    parser.add_argument(
        "--log-format",
        choices=("text", "json"),
        default=os.environ.get("ARCHON_LOG_FORMAT", "text"),
        help="one JSON object per line for log shipping (default: %(default)s)",
    )
    args = parser.parse_args(argv)

    global emit
    as_json = args.log_format == "json"
    if as_json:
        # A log shipper parses the whole line, so it must be only the object.
        for handler in logging.getLogger().handlers:
            handler.setFormatter(logging.Formatter("%(message)s"))
    emit = Emitter(as_json=as_json)
    shutdown = Shutdown()
    shutdown.install()

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
    emit(
        "started",
        f"Keeper {keeper.address} servicing app {app_id}",
        keeper=keeper.address,
        app_id=app_id,
        network=args.network,
    )

    # Upkeeps that failed this run. A failed execution is free — Algorand
    # rejects it before it reaches a block, so nothing is spent — but retrying
    # a target that keeps rejecting wastes a round-trip every round and
    # crowds the transaction pool, so it is skipped for the rest of the run.
    failed: set[int] = set()
    error_delay = ERROR_RETRY_SECONDS
    while True:
        if shutdown.requested:
            emit("stopped", "Shutting down cleanly")
            return
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
            error_delay = ERROR_RETRY_SECONDS
            emit(
                "scan",
                f"Round {current}: {len(upkeeps)} upkeeps, {len(due)} due",
                round=current,
                upkeeps=len(upkeeps),
                due=len(due),
                skipped=len(failed),
            )
            for upkeep in due:
                if shutdown.requested:
                    break
                try:
                    response = client.send.execute(
                        args=ExecuteArgs(upkeep_id=upkeep.upkeep_id),
                        params=algokit_utils.CommonAppCallParams(
                            extra_fee=algokit_utils.AlgoAmount(
                                micro_algo=EXTRA_FEE_MICROALGO
                            )
                        ),
                    )
                    emit(
                        "executed",
                        f"Executed upkeep {upkeep.upkeep_id} "
                        f"(target app {upkeep.target_app}); "
                        f"+{upkeep.fee_per_execution} µALGO, "
                        f"next due round {response.abi_return}",
                        round=current,
                        upkeep_id=upkeep.upkeep_id,
                        target_app=upkeep.target_app,
                        fee_collected=upkeep.fee_per_execution,
                        escrow_remaining=upkeep.balance - upkeep.fee_per_execution,
                        next_due_round=response.abi_return,
                        tx_id=response.tx_id,
                    )
                except Exception as exc:
                    failed.add(upkeep.upkeep_id)
                    emit(
                        "execute_failed",
                        f"Upkeep {upkeep.upkeep_id} failed (no fee charged); "
                        f"skipping it for this run: {exc}",
                        level=logging.WARNING,
                        round=current,
                        upkeep_id=upkeep.upkeep_id,
                        reason=str(exc)[:400],
                    )
            if args.once:
                return
            algod.status_after_block(current + 1)
        except KeyboardInterrupt:
            emit("stopped", "Interrupted; exiting")
            return
        except Exception as exc:
            if args.once:
                raise
            emit(
                "scan_failed",
                f"{exc}; retrying in {error_delay}s",
                level=logging.WARNING,
                reason=str(exc)[:400],
                retry_in_seconds=error_delay,
            )
            time.sleep(error_delay)
            # Back off while the node is unhappy, but recover quickly once it
            # answers again.
            error_delay = min(error_delay * 2, MAX_ERROR_RETRY_SECONDS)


if __name__ == "__main__":
    main()
