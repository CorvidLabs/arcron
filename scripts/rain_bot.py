"""The second automated participant in a `rain` draw: it resolves, it claims,
it puts the prize back in.

`rain` is deliberately pull, not push (see `smart_contracts/rain/contract.py`):
`draw` only locks a prize and fixes a future beacon round, because that is all
a bare Arcron upkeep can do. Somebody still has to supply the beacon
(`resolve`) and the winner still has to pull their own prize (`claim`). On a
draw with only one real participant, "somebody" and "the winner" are the same
account, so this bot holds that account, watches one `rain` app, and does
three things, in order, every time it runs:

  1. If a draw is open and the beacon round has passed, call `resolve`,
     supplying the beacon app reference a scheduled call could not attach.
  2. If a draw's beacon window has closed unresolved, call `abandon` so the
     prize returns to the pot instead of sitting locked forever.
  3. If this account has anything allocated (`allocation_of`), `claim` it,
     then `deposit` the exact amount claimed straight back into the pot.
     `claim` and `deposit` are two transactions, not one atomic group, so
     what was claimed is recorded to a small local state file first (see
     `default_pending_path`) and only cleared once the deposit confirms;
     that way a crash between the two redeposits on the next run instead of
     stranding a won prize in this account's own wallet forever.

Step 3 is what "circulates" means here, concretely. The pot after a full
cycle is the pot before it: `draw` reserves the box MBR the winner's
allocation needs, `claim` releases it back to the pot when the box is
deleted, and this bot re-deposits the rest. **What does not circulate is
transaction fees.** `resolve`, `claim` and `deposit` are three signed
transactions this account pays for out of its own balance, on top of the
Arcron keeper fee `draw` itself pays out of the upkeep's escrow. Measured at
the registered cadence (`docs/releases.md` has the number), that is real
ALGO leaving the system every cycle, forever. It is the cost of running the
dogfood, not a bug, and nothing about this design amortises it away.

This bot never opens a draw itself; that is Arcron's job; a keeper executing
the registered upkeep calls `draw` on its own schedule. This bot only reacts
to a draw that already exists, which is why every action here is a no-op
check before it is a transaction: run it as often as you like, including
from a cron job that overlaps with itself, and a quiet round costs nothing.

Picks its network with --network (or ARCRON_NETWORK), loading .env.localnet
or .env.testnet. Signs as the account from RAIN_MNEMONIC if set, else
DEPLOYER_MNEMONIC; resolving costs a small fee, and only a won prize is ever
paid out, straight back to this same account. On LocalNet both come from
KMD, so no mnemonic is needed.

Run:  poetry run python -m scripts.rain_bot --once [--network N] [--app-id N]
"""

import argparse
import base64
import json
import logging
import os
import time
from pathlib import Path

import algokit_utils
from algosdk import encoding
from algosdk.error import AlgodHTTPError

from scripts import network as net
from scripts.keeper_bot import Emitter, Shutdown
from smart_contracts.artifacts.rain.rain_client import (
    ClaimArgs,
    DepositArgs,
    RainClient,
)
from smart_contracts.rain.contract import ALLOCATION_PREFIX, BEACON_WINDOW

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Extra fee, on top of the standard 1,000 µALGO outer fee, pooled for the one
# inner transaction `resolve` and `claim` each submit (a beacon call and a
# payment respectively). Matches scripts/rain_demo.py, proved sufficient
# there and against the real beacon (see docs/releases.md).
EXTRA_FEE_MICROALGO = 1_000
# What this account must hold before it refuses to start, rather than
# looking alive while unable to broadcast: the base account MBR plus one
# full cycle (resolve + claim + deposit, ~2,000 µALGO of fees each).
ACCOUNT_MBR_MICROALGO = 100_000
CYCLE_COST_MICROALGO = 3 * 2_000
HARD_MINIMUM_MICROALGO = ACCOUNT_MBR_MICROALGO + CYCLE_COST_MICROALGO
# Default warning floor: about 30 days of cycles at the registered two-hour
# cadence (roughly 360 cycles), below RAIN_ACCOUNT_FUNDING_MICROALGO in
# scripts/rain_testnet_deploy.py so a freshly funded account does not warn
# on its very first run.
LOW_BALANCE_MICROALGO = ACCOUNT_MBR_MICROALGO + 360 * CYCLE_COST_MICROALGO
# Between polls in loop mode. The registered cadence is hours; nothing here
# is time-critical enough to justify a block-by-block loop the way the
# Arcron keeper runs one, and busy-polling an idle draw is exactly the "loop
# hot" this bot must not do.
POLL_SECONDS_DEFAULT = 300

emit = Emitter()


def read_state(algod, app_id: int) -> dict:
    """This rain app's global state, decoded. A free algod read, no signing."""
    entries = algod.application_info(app_id)["params"].get("global-state", [])
    state: dict[str, int | str] = {}
    for entry in entries:
        key = base64.b64decode(entry["key"]).decode()
        value = entry["value"]
        state[key] = value.get("uint", 0) if value.get("type") == 2 else value.get("bytes", "")
    return state


def read_allocation(algod, app_id: int, address: str) -> int:
    """What `address` can claim right now. Zero if it has no allocation box.

    A free algod read: the same box `claim` deletes, read directly rather
    than through a simulated ABI call, so checking costs nothing and can be
    done every time this runs.
    """
    name = ALLOCATION_PREFIX + encoding.decode_address(address)
    try:
        raw = algod.application_box_by_name(app_id, name)["value"]
    except AlgodHTTPError as exc:
        if "box not found" in str(exc).lower():
            return 0
        raise
    if isinstance(raw, str):
        raw = base64.b64decode(raw)
    return int.from_bytes(raw, "big")


def default_pending_path(network: str, app_id: int) -> Path:
    """Where a claimed-but-not-yet-redeposited amount is remembered.

    Mirrors `scripts/keeper_backoff.py`'s state path convention: per network,
    per app, outside the repo so it is never committed. Exists for exactly
    one gap: `claim` and `deposit` are two separate transactions, not one
    atomic group, so a crash between them (a rate-limited node did this on
    a real run, see `docs/releases.md`) would otherwise strand a won prize
    in this account's own wallet. The next run's `allocation_of` reads zero,
    because the claim already happened, so nothing would tell it to
    redeposit money it no longer sees as owed to it, without this record.

    Like the backoff state, this is a stopgap that assumes a persistent
    filesystem between runs. A fresh container on every invocation (a
    scheduled CI job, as opposed to a long-lived host) loses it exactly the
    way `.github/workflows/keeper-bot.yml` already documents for backoff
    state; the fix is the same one recommended there, a long-running
    process with a real disk.
    """
    base = os.environ.get("XDG_STATE_HOME")
    root = Path(base) if base else Path.home() / ".local" / "state"
    return root / "arcron" / f"rain-bot-pending-{network}-{app_id}.json"


def _read_pending(path: Path | None) -> int:
    if path is None or not path.exists():
        return 0
    try:
        return int(json.loads(path.read_text()).get("pending_deposit", 0))
    except Exception:
        return 0


def _write_pending(path: Path | None, amount: int) -> None:
    if path is None:
        return
    if amount <= 0:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pending_deposit": amount}))


def should_resolve(state: dict, current_round: int, window: int = BEACON_WINDOW) -> bool:
    """A draw is open, its beacon round has passed, and the window has not."""
    if int(state.get("draw_open", 0)) != 1:
        return False
    commit_round = int(state["commit_round"])
    return current_round > commit_round and current_round <= commit_round + window


def should_abandon(state: dict, current_round: int, window: int = BEACON_WINDOW) -> bool:
    """A draw is open and the beacon can no longer answer for it.

    Defensive: at the registered two-hour cadence the beacon window (about
    1,000 rounds held short of the real ~1,512-round retention; see
    `smart_contracts/rain/contract.py`) closes roughly 46 minutes after a
    draw opens, well inside any reasonable polling interval. This exists so
    a bot that misses that window anyway (an outage, a stopped cron) heals
    itself on the next run instead of leaving the pot locked forever.
    """
    if int(state.get("draw_open", 0)) != 1:
        return False
    commit_round = int(state["commit_round"])
    return current_round > commit_round + window


def resolve_app_id(parser: argparse.ArgumentParser, app_id: int | None) -> int:
    if app_id is not None:
        return app_id
    from_env = os.environ.get("RAIN_APP_ID")
    if from_env:
        return int(from_env)
    parser.error("--app-id (or RAIN_APP_ID) is required: no rain app is canonical yet")


def guard_balance(algod, address: str, warn_below: int) -> int:
    balance = algod.account_info(address)["amount"]
    if balance < HARD_MINIMUM_MICROALGO:
        raise SystemExit(
            f"rain bot account {address} holds {balance} µALGO, below the "
            f"{HARD_MINIMUM_MICROALGO} µALGO needed to keep its account and pay for "
            f"one resolve/claim/deposit cycle ({CYCLE_COST_MICROALGO} µALGO). Fund it "
            f"before starting."
        )
    if balance < warn_below:
        cycles = (balance - ACCOUNT_MBR_MICROALGO) // CYCLE_COST_MICROALGO
        emit(
            "low_balance",
            f"rain bot balance {balance} µALGO is low: about {cycles} cycle(s) of "
            f"headroom. The prize it wins comes straight back out as a deposit; "
            f"only this account's own transaction fees ever draw it down.",
            level=logging.WARNING,
            balance=balance,
            cycles_remaining=cycles,
        )
    return balance


def scan_once(
    algod,
    client: "RainClient",
    app_id: int,
    address: str,
    pending_path: Path | None = None,
) -> bool:
    """One pass. Returns whether any transaction was sent.

    Every branch starts by asking whether there is anything to do, so a
    quiet run (no draw open, nothing allocated, nothing pending) signs
    nothing and costs nothing beyond the free reads above. Safe to call as
    often as you like, including back-to-back.
    """
    state = read_state(algod, app_id)
    current = algod.status()["last-round"]
    acted = False
    to_deposit = _read_pending(pending_path)

    if should_resolve(state, current):
        beacon_app = int(state["beacon_app"])
        winner = client.send.resolve(
            params=algokit_utils.CommonAppCallParams(
                extra_fee=algokit_utils.AlgoAmount(micro_algo=EXTRA_FEE_MICROALGO),
                app_references=[beacon_app],
            )
        ).abi_return
        emit(
            "resolved",
            f"Round {current}: resolved draw {state.get('draw_id')}; winner {winner}",
            round=current,
            app_id=app_id,
            draw_id=state.get("draw_id"),
            winner=str(winner),
        )
        acted = True
        state = read_state(algod, app_id)
    elif should_abandon(state, current):
        returned = client.send.abandon().abi_return
        emit(
            "abandoned",
            f"Round {current}: beacon window closed unresolved; {returned} µALGO "
            f"returned to the pot",
            level=logging.WARNING,
            round=current,
            app_id=app_id,
            returned=returned,
        )
        acted = True
        state = read_state(algod, app_id)

    allocation = read_allocation(algod, app_id, address)
    if allocation > 0:
        claimed = client.send.claim(
            args=ClaimArgs(gate_asset=0),
            params=algokit_utils.CommonAppCallParams(
                extra_fee=algokit_utils.AlgoAmount(micro_algo=EXTRA_FEE_MICROALGO)
            ),
        ).abi_return
        emit(
            "claimed",
            f"Round {current}: claimed {claimed} µALGO",
            round=current,
            app_id=app_id,
            claimed=claimed,
        )
        # Recorded before the deposit is attempted, not after: if the
        # deposit itself is what fails or times out, the amount must still
        # be here to retry next time. Cleared only once `deposit` confirms.
        to_deposit += claimed
        _write_pending(pending_path, to_deposit)
        acted = True

    if to_deposit > 0:
        new_pot = client.send.deposit(
            args=DepositArgs(
                payment=client.algorand.create_transaction.payment(
                    algokit_utils.PaymentParams(
                        sender=address,
                        receiver=client.app_address,
                        amount=algokit_utils.AlgoAmount(micro_algo=to_deposit),
                    )
                )
            )
        ).abi_return
        _write_pending(pending_path, 0)
        emit(
            "deposited",
            f"Round {current}: deposited {to_deposit} µALGO back into the pot "
            f"(pot now {new_pot})",
            round=current,
            app_id=app_id,
            deposited=to_deposit,
            pot=new_pot,
        )
        acted = True

    if not acted:
        emit(
            "idle",
            f"Round {current}: nothing to do (draw_open={state.get('draw_open', 0)})",
            round=current,
            app_id=app_id,
            draw_open=int(state.get("draw_open", 0)),
        )
    return acted


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="run a single scan, then exit")
    net.add_network_argument(parser)
    parser.add_argument(
        "--app-id", type=int, default=None, help="rain app id (default: RAIN_APP_ID)"
    )
    parser.add_argument(
        "--poll-seconds",
        type=int,
        default=int(os.environ.get("RAIN_POLL_SECONDS", POLL_SECONDS_DEFAULT)),
        help="seconds between scans in loop mode (default: %(default)s)",
    )
    parser.add_argument(
        "--min-balance",
        type=int,
        default=int(os.environ.get("RAIN_MIN_BALANCE", LOW_BALANCE_MICROALGO)),
        help="warn below this signer balance in µALGO (default: %(default)s)",
    )
    parser.add_argument(
        "--log-format",
        choices=("text", "json"),
        default=os.environ.get("ARCRON_LOG_FORMAT", "text"),
        help="one JSON object per line for log shipping (default: %(default)s)",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=None,
        help="where to persist a claimed-not-yet-deposited amount (default: under XDG_STATE_HOME)",
    )
    parser.add_argument(
        "--no-state",
        action="store_true",
        help="keep pending-deposit tracking in memory only; nothing is written to disk",
    )
    args = parser.parse_args(argv)

    global emit
    as_json = args.log_format == "json"
    if as_json:
        for handler in logging.getLogger().handlers:
            handler.setFormatter(logging.Formatter("%(message)s"))
    emit = Emitter(as_json=as_json)
    shutdown = Shutdown()
    shutdown.install()

    algorand = net.connect(args.network)
    app_id = resolve_app_id(parser, args.app_id)
    algod = algorand.client.algod

    try:
        bot = algorand.account.from_environment("RAIN")
    except Exception:
        bot = algorand.account.from_environment("DEPLOYER")

    balance = guard_balance(algod, bot.address, args.min_balance)
    client = RainClient(
        algorand=algorand,
        app_id=app_id,
        default_sender=bot.address,
        default_signer=bot.signer,
    )
    pending_path = (
        None if args.no_state else (args.state_file or default_pending_path(args.network, app_id))
    )
    emit(
        "started",
        f"rain bot {bot.address} watching app {app_id}",
        account=bot.address,
        app_id=app_id,
        network=args.network,
        balance=balance,
        pending_state=str(pending_path) if pending_path else "memory",
    )

    if args.once:
        scan_once(algod, client, app_id, bot.address, pending_path)
        return

    while True:
        if shutdown.requested:
            emit("stopped", "Shutting down cleanly")
            return
        try:
            scan_once(algod, client, app_id, bot.address, pending_path)
            guard_balance(algod, bot.address, args.min_balance)
        except KeyboardInterrupt:
            emit("stopped", "Interrupted; exiting")
            return
        except Exception as exc:
            emit(
                "scan_failed",
                f"{exc}; retrying in {args.poll_seconds}s",
                level=logging.WARNING,
                reason=str(exc)[:400],
            )
        for _ in range(args.poll_seconds):
            if shutdown.requested:
                break
            time.sleep(1)


if __name__ == "__main__":
    main()
