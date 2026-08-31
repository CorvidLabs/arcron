"""Prove a ONE draw can pick somebody who is not the caller, on a real chain.

`resolve` reads the committed round's block seed, takes it modulo the live
ticket count, and credits whoever that index points at. Every test of that is
either a mock with a planted seed (`tests/test_rain.py`) or a live run with a
single ticket (`scripts/rain_testnet_live_proof.py`), and `seed % 1` is zero
for every seed there has ever been. So on a real chain the draw has fired and
has never once *chosen*:

  * the index box at any offset but zero has never been read,
  * the winner's ticket box has never been a box the caller does not own,
    which is the one the resolving transaction has to reference without
    knowing the winner until it reads the seed,
  * and the modulo has never been exercised against a seed nobody planted.

This enters a ONE rain from several accounts and resolves until an index other
than zero comes up, checking every draw against the seed independently: the
expected index is recomputed here from `algod.block_info`, so a contract that
agreed with itself but not with the chain would fail rather than pass quietly.

Costs real TestNet ALGO. Players are funded from the deployer and closed back
to it at the end, but each ticket's box minimum stays in the hub permanently:
there is no `leave`.

Run:  poetry run python -m scripts.rain_one_draw --network testnet
      poetry run python -m scripts.rain_one_draw --network testnet --players 3
"""

from __future__ import annotations

import argparse
import base64
import logging
import os
from pathlib import Path

import algokit_utils

from scripts import network as net
from smart_contracts.artifacts.rain.rain_client import (
    AllocationOfArgs,
    ClaimArgs,
    DepositArgs,
    EnterArgs,
    RainClient,
    RainOfArgs,
    ResolveArgs,
)
from smart_contracts.rain.contract import INDEX_MBR, ONE, TICKET_MBR

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

HUB = 770_130_162
RAIN_ID = 3
EXPLORER = "https://testnet.explorer.perawallet.app"

#: What each player needs: the account minimum, its two boxes, and fees.
PLAYER_FUNDING = 260_000
#: `draw` scans four rains and can write two of them; the budget is tight.
DRAW_FEE = algokit_utils.AlgoAmount(micro_algo=9_000)
INNER = algokit_utils.AlgoAmount(micro_algo=2_000)
#: Leave the deployer able to pay for the closes and claims at the end.
RESERVE = 150_000

#: Throwaway keys, outside the repository so they cannot be committed.
DEFAULT_PLAYERS_FILE = os.path.join(
    os.path.expanduser("~"), ".config", "arcron", "rain-one-draw-players.txt"
)


def _pay(algorand, sender: str, receiver: str, amount: int):
    return algorand.create_transaction.payment(
        algokit_utils.PaymentParams(
            sender=sender,
            receiver=receiver,
            amount=algokit_utils.AlgoAmount(micro_algo=amount),
        )
    )


def seed_index(algod, commit_round: int, tickets: int) -> int:
    """The index `resolve` must return, computed from the chain, not the app.

    `op.Block.blk_seed` is the first 32 bytes of the block's seed and the
    contract reads a uint64 from offset 0, so this is the same arithmetic
    reached by a different route. Agreeing with the contract is the point.
    """
    block = algod.block_info(commit_round)
    seed = block.get("block", block)["seed"]
    raw = base64.b64decode(seed) if isinstance(seed, str) else seed
    return int.from_bytes(raw[:8], "big") % tickets


def index_holder(algod, hub: int, rain_id: int, index: int) -> str:
    """Who the lottery index box says is at this offset."""
    from algosdk import encoding

    name = b"n" + rain_id.to_bytes(8, "big") + index.to_bytes(8, "big")
    raw = base64.b64decode(algod.application_box_by_name(hub, name)["value"])
    return encoding.encode_address(raw)


def check_allocations(
    baseline: dict[str, int], after: dict[str, int], winner: str, locked: int
) -> None:
    """Exactly one account gained, it gained the locked prize, and it won.

    Deltas, not absolutes. A ONE ticket's credit persists until it is claimed,
    so an account that won an earlier draw and never collected still shows a
    balance; the first version of this compared absolutes and accused the
    holder of a stale 50,000 credit of winning a draw it did not win.
    """
    gained = {who: after[who] - baseline.get(who, 0) for who in after}
    if gained.get(winner, 0) != locked:
        raise SystemExit(
            f"winner {winner} gained {gained.get(winner, 0)}, expected exactly {locked}"
        )
    for who, delta in gained.items():
        if who != winner and delta != 0:
            raise SystemExit(f"{who} gained {delta} without winning")


def _exists(algod, address: str) -> bool:
    try:
        algod.account_info(address)
        return True
    except Exception:
        return False


def _load_or_make_players(algorand, count: int, path: Path) -> list:
    """Throwaway signers, written down so a crashed run can be cleaned up.

    The first version generated these in memory. It then failed an assertion
    before the close-out step, and the mnemonics went with the process: two
    funded accounts and an unclaimable prize, stranded for good. They hold a
    fraction of a TestNet ALGO and exist for one run, but they have to outlive
    a traceback, so they go to a file outside the repository.
    """
    from algosdk import account as algo_account, mnemonic as algo_mnemonic

    path.parent.mkdir(parents=True, exist_ok=True)
    words: list[str] = []
    if path.exists():
        words = [line for line in path.read_text().splitlines() if line.strip()]
        logger.info(f"  reusing {len(words)} player(s) from {path}")
    while len(words) < count:
        private_key, _ = algo_account.generate_account()
        words.append(algo_mnemonic.from_private_key(private_key))
    path.write_text("\n".join(words) + "\n")
    path.chmod(0o600)
    return [algorand.account.from_mnemonic(phrase) for phrase in words[:count]]


def _rec(rain: RainClient, rain_id: int):
    return rain.send.rain_of(args=RainOfArgs(rain_id=rain_id)).abi_return


def _owed(rain: RainClient, rain_id: int, who: str) -> int:
    return int(
        rain.send.allocation_of(args=AllocationOfArgs(rain_id=rain_id, who=who)).abi_return or 0
    )


def _fire(algorand, rain: RainClient, rain_id: int, poker) -> object:
    """Call `draw` until this rain is the one that fired.

    `draw` walks DRAW_SCAN rains from a rolling cursor, so a hub with more
    rains than the scan width will not always reach the one you care about.
    That is the hub's scheduling property, not a failure, so this retries.
    """
    for attempt in range(4):
        rain.send.draw(params=algokit_utils.CommonAppCallParams(extra_fee=DRAW_FEE))
        rec = _rec(rain, rain_id)
        if rec.prize_locked > 0:
            return rec
        logger.info(f"  cursor missed rain {rain_id} (scan {attempt + 1})")
    raise SystemExit(f"rain {rain_id} never fired after four draws; is it due and funded?")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    net.add_network_argument(parser)
    parser.add_argument("--hub", type=int, default=HUB)
    parser.add_argument("--rain-id", type=int, default=RAIN_ID)
    parser.add_argument("--players", type=int, default=2,
                        help="fresh accounts to add; the rain's existing tickets still count")
    parser.add_argument("--cycles", type=int, default=8,
                        help="most draws to run while waiting for a non-zero index")
    parser.add_argument("--pot", type=int, default=300_000, help="µALGO to deposit")
    parser.add_argument(
        "--players-file", type=Path, default=Path(DEFAULT_PLAYERS_FILE),
        help="where the throwaway player mnemonics live between runs. Never in the repo.",
    )
    args = parser.parse_args(argv)

    algorand = net.connect(args.network)
    algod = algorand.client.algod
    net.assert_network(algod, args.network)
    funder = algorand.account.from_environment("DEPLOYER")
    rain = RainClient(
        algorand=algorand, app_id=args.hub,
        default_sender=funder.address, default_signer=funder.signer,
    )

    rec = _rec(rain, args.rain_id)
    if rec.mode != ONE:
        raise SystemExit(f"rain {args.rain_id} is mode {rec.mode}, not ONE")
    if rec.prize_locked > 0:
        raise SystemExit(
            f"rain {args.rain_id} already has a draw open at commit round "
            f"{rec.commit_round}. Resolve or abandon it first."
        )

    info = algod.account_info(funder.address)
    spendable = info["amount"] - info["min-balance"]
    needed = args.players * PLAYER_FUNDING + args.pot + RESERVE
    logger.info(f"funder {funder.address[:12]}…  {spendable} µALGO spendable, needs {needed}")
    if spendable < needed:
        raise SystemExit(f"not enough: {spendable} < {needed} µALGO")

    logger.info(f"── Rain {args.rain_id} on hub {args.hub}: "
                f"{rec.tickets} ticket(s), drip {rec.drip}, interval {rec.interval_rounds} ──")

    players = _load_or_make_players(algorand, args.players, args.players_file)
    for who in players:
        algorand.account.set_signer(who.address, who.signer)
        held = algod.account_info(who.address).get("amount", 0) if _exists(algod, who.address) else 0
        if held < PLAYER_FUNDING:
            algorand.send.payment(
                algokit_utils.PaymentParams(
                    sender=funder.address, receiver=who.address,
                    amount=algokit_utils.AlgoAmount(micro_algo=PLAYER_FUNDING - held),
                )
            )
        try:
            rain.send.enter(
                args=EnterArgs(
                    mbr_payment=_pay(
                        algorand, who.address, rain.app_address, TICKET_MBR + INDEX_MBR
                    ),
                    rain_id=args.rain_id,
                    gate_asset=0,
                ),
                params=algokit_utils.CommonAppCallParams(sender=who.address),
            )
            logger.info(f"  {who.address[:12]}… entered")
        except Exception as error:
            if "Already entered" not in str(error):
                raise
            logger.info(f"  {who.address[:12]}… already in, reused")

    rain.send.deposit(
        args=DepositArgs(
            payment=_pay(algorand, funder.address, rain.app_address, args.pot),
            rain_id=args.rain_id,
        )
    )
    rec = _rec(rain, args.rain_id)
    logger.info(f"  pot {rec.pot}, {rec.tickets} tickets")

    everyone = [funder.address] + [p.address for p in players]
    # Snapshot before the first draw: the funder's ticket predates this run and
    # may already hold unclaimed credit from a draw it won weeks ago.
    baseline = {who: _owed(rain, args.rain_id, who) for who in everyone}
    logger.info(f"  allocations before: { {w[:8]: a for w, a in baseline.items()} }")
    seen: list[int] = []

    for cycle in range(args.cycles):
        rec = _rec(rain, args.rain_id)
        if rec.pot < rec.drip:
            logger.info("  pot exhausted")
            break
        due = rec.last_rain_round + rec.interval_rounds
        net.wait_for_round(algorand, due, poker=funder)
        rec = _fire(algorand, rain, args.rain_id, funder)
        tickets = int(rec.tickets)
        commit = int(rec.commit_round)
        locked = int(rec.prize_locked)
        logger.info(f"── draw {cycle + 1}: locked {locked} for one of {tickets}, "
                    f"commit round {commit} ──")

        net.wait_for_round(algorand, commit + 1, poker=funder)
        expected = seed_index(algod, commit, tickets)
        result = rain.send.resolve(args=ResolveArgs(rain_id=args.rain_id))
        got = int(result.abi_return)
        tx_id = result.tx_ids[0]
        if got != expected:
            raise SystemExit(
                f"resolve returned index {got}; the block seed at {commit} says {expected}"
            )
        winner = index_holder(algod, args.hub, args.rain_id, got)
        logger.info(f"  index {got}/{tickets} -> {winner[:12]}…  {EXPLORER}/tx/{tx_id}")

        after = {who: _owed(rain, args.rain_id, who) for who in everyone}
        check_allocations(baseline, after, winner, locked)
        baseline = after
        seen.append(got)
        if got != 0:
            logger.info("  ** a non-zero index: the path that had never run on a chain **")
            break

    logger.info("── Claim, then close the players back to the funder ──")
    for who in everyone:
        amount = _owed(rain, args.rain_id, who)
        if amount == 0:
            continue
        rain.send.claim(
            args=ClaimArgs(rain_id=args.rain_id, gate_asset=0),
            params=algokit_utils.CommonAppCallParams(sender=who, extra_fee=INNER),
        )
        logger.info(f"  {who[:12]}… claimed {amount} µALGO")

    for who in players:
        algorand.send.payment(
            algokit_utils.PaymentParams(
                sender=who.address, receiver=funder.address,
                amount=algokit_utils.AlgoAmount(micro_algo=0),
                close_remainder_to=funder.address,
            )
        )
        logger.info(f"  {who.address[:12]}… closed back to the funder")
    if args.players_file.exists():
        args.players_file.unlink()

    logger.info("")
    logger.info(f"Indices drawn: {seen}")
    if not any(i != 0 for i in seen):
        raise SystemExit(
            f"every draw came up index 0 in {len(seen)} tries. Not a failure, but the "
            f"non-zero path is still unproven; run it again."
        )
    logger.info("A ONE draw picked an account that was not the caller, on TestNet.")
    logger.info(f"  Hub {args.hub}, rain {args.rain_id}, {EXPLORER}/application/{args.hub}")


if __name__ == "__main__":
    main()
