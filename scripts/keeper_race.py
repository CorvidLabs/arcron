"""Make two keepers collide on purpose, and record what losing cost.

Arcron's economic argument is that competition between keepers holds the fee
below the ceiling, and that competing is safe because a keeper that loses a
race pays nothing: Algorand rejects a failing transaction at validation, so it
never reaches a block and its sender is never charged. Both halves had only
ever been shown by construction. One keeper serviced TestNet and won
everything by default, and `scripts/keeper_e2e.py` proves the cost of losing
with a crafted transaction rather than with two keepers that genuinely
collided.

This stages the real thing. It registers a fast upkeep, waits for it to come
due, and starts two unmodified `scripts/keeper_bot.py` processes signing as
different accounts, both aligned to the same wall-clock barrier so they scan
the same round and reach for the same upkeep. Then it reports, from chain data
rather than from either bot's opinion:

  * which keeper's execution landed, read out of the block it landed in;
  * that the loser's transaction is in no block at all;
  * that the loser's balance moved by exactly zero.

It exits non-zero when no collision happened, because a run in which the two
keepers politely took turns proves nothing and should not read as a pass.

Run:  poetry run python -m scripts.keeper_race --network localnet
      poetry run python -m scripts.keeper_race --network testnet \\
          --app-id 769891898 --target-app 769891902
"""

import argparse
import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass

import algokit_utils
from algosdk import mnemonic

from scripts import keeper_bot, network as net
from smart_contracts.artifacts.keeper.keeper_client import (
    CancelArgs,
    KeeperClient,
    RegisterArgs,
)
from smart_contracts.keeper.contract import BOX_MBR_FIXED

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# The fastest cadence the contract allows, so the upkeep comes due while two
# bots are alive rather than in six hours.
RACE_INTERVAL_ROUNDS = 10
RACE_FEE_MICROALGO = 4_000
RACE_EXECUTIONS = 4
# What a keeper needs before it will start at all, plus room to lose a few.
KEEPER_FLOOR_MICROALGO = 300_000
# Long enough that two processes started together always land on the same
# mark, short enough that a run is not mostly waiting.
DEFAULT_BARRIER_SECONDS = 30
# How long to let a keeper run before giving up on it.
BOT_TIMEOUT_SECONDS = 180


@dataclass
class Outcome:
    """What one keeper did, read back out of its own JSON log."""

    address: str
    events: list[dict]
    log: str

    def of(self, event: str, upkeep_id: int) -> dict | None:
        for entry in self.events:
            if entry.get("event") == event and entry.get("upkeep_id") == upkeep_id:
                return entry
        return None


def _mnemonic_for(account) -> str:
    return mnemonic.from_private_key(account.private_key)


def _resolve_keepers(algorand, network: str) -> list:
    """Two accounts that will both sign executions.

    On LocalNet both are created and funded from KMD, so a race needs no
    configuration at all. Elsewhere they are KEEPER_MNEMONIC and
    KEEPER_2_MNEMONIC, which are the same two secrets the two scheduled
    workflows use, so a race here exercises the accounts that actually keep.
    """
    keepers = []
    for name in ("KEEPER", "KEEPER_2"):
        try:
            keepers.append(algorand.account.from_environment(name))
        except Exception as cause:
            raise SystemExit(
                f"No {name}_MNEMONIC. A race needs two accounts: one keeper cannot "
                f"race itself, and two processes signing as the same address would "
                f"collide on the transaction id rather than on the upkeep. "
                f"docs/hosting.md has what to run."
            ) from cause
    if keepers[0].address == keepers[1].address:
        raise SystemExit(
            f"KEEPER and KEEPER_2 are the same account ({keepers[0].address}). "
            f"That is a queue, not a race."
        )
    if network == net.LOCALNET:
        for account in keepers:
            algorand.account.ensure_funded_from_environment(
                account.address,
                algokit_utils.AlgoAmount(micro_algo=3_000_000),
                min_funding_increment=algokit_utils.AlgoAmount(micro_algo=3_000_000),
            )
    return keepers


def _register_race_upkeep(algorand, client: KeeperClient, creator, target_app: int) -> int:
    """A fast, cheap upkeep that exists only to be fought over."""
    from algosdk import abi

    # Pulse.tick, the standard demo target. Encoded here rather than imported
    # so this script does not need the target's client.
    import hashlib

    selector = hashlib.new("sha512_256", b"tick()uint64").digest()[:4]
    encoded = abi.ABIType.from_string("byte[][]").encode([list(selector)])
    box_mbr = BOX_MBR_FIXED + 400 * len(encoded)

    def payment(amount: int):
        return algorand.create_transaction.payment(
            algokit_utils.PaymentParams(
                sender=creator.address,
                receiver=client.app_address,
                amount=algokit_utils.AlgoAmount(micro_algo=amount),
            )
        )

    return client.send.register(
        args=RegisterArgs(
            mbr_payment=payment(box_mbr),
            funding_payment=payment(RACE_FEE_MICROALGO * RACE_EXECUTIONS),
            target_app=target_app,
            call_args=[selector],
            interval_rounds=RACE_INTERVAL_ROUNDS,
            fee_per_execution=RACE_FEE_MICROALGO,
            policy=keeper_bot.SKIP_AHEAD,
            fee_cap=0,
            fee_asset=0,
            asset_fee=0,
        )
    ).abi_return


def _launch(network: str, app_id: int, account, barrier: int) -> subprocess.Popen:
    environment = dict(os.environ)
    environment["KEEPER_MNEMONIC"] = _mnemonic_for(account)
    # The bot prefers KEEPER; make sure nothing else in the environment can
    # quietly make both processes sign as the same account.
    environment.pop("DEPLOYER_MNEMONIC", None)
    return subprocess.Popen(
        [
            sys.executable, "-m", "scripts.keeper_bot",
            "--once",
            "--network", network,
            "--app-id", str(app_id),
            "--no-state",
            "--log-format", "json",
            "--align", str(barrier),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=environment,
    )


def _collect(account, process: subprocess.Popen) -> Outcome:
    try:
        log = process.communicate(timeout=BOT_TIMEOUT_SECONDS)[0] or ""
    except subprocess.TimeoutExpired:
        # A bot that will not finish must not leave a signing process running
        # after this script has reported and gone.
        process.kill()
        log = (process.communicate()[0] or "") + "\nkilled: took too long"
    events = []
    for line in log.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
    return Outcome(address=account.address, events=events, log=log)


def _landed(algorand, tx_id: str | None) -> bool | None:
    """Is this transaction in a block? None when nothing can say.

    An indexer only ever holds committed transactions, so a lookup that comes
    back empty is the cleanest available statement that a transaction was
    thrown away rather than merely missed.
    """
    if not tx_id:
        return None
    try:
        indexer = algorand.client.indexer
        indexer.transaction(tx_id)
        return True
    except Exception as exc:
        return False if "no transaction found" in str(exc).lower() else None


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    net.add_network_argument(parser)
    parser.add_argument("--app-id", type=int, default=None, help="Keeper app id")
    parser.add_argument(
        "--target-app",
        type=int,
        default=None,
        help="app the raced upkeep calls (default: deploy Pulse, LocalNet only)",
    )
    parser.add_argument(
        "--upkeep-id",
        type=int,
        default=None,
        help="race over an upkeep that already exists, instead of registering one",
    )
    parser.add_argument(
        "--barrier",
        type=int,
        default=DEFAULT_BARRIER_SECONDS,
        help="the shared wall-clock barrier both keepers wait for (default: %(default)s)",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="leave the registered upkeep in place instead of cancelling it",
    )
    args = parser.parse_args(argv)

    algorand = net.connect(args.network)
    algod = algorand.client.algod
    if args.app_id is None and args.network == net.LOCALNET:
        # A throwaway chain has no canonical deployment to name, so make one.
        from smart_contracts.keeper.deploy_config import deploy as deploy_keeper

        app_id = deploy_keeper().app_id
    else:
        app_id = keeper_bot.resolve_app_id(parser, args.app_id, args.network)
    creator = algorand.account.from_environment("DEPLOYER")
    keepers = _resolve_keepers(algorand, args.network)
    client = KeeperClient(
        algorand=algorand,
        app_id=app_id,
        default_sender=creator.address,
        default_signer=creator.signer,
    )

    for keeper in keepers:
        balance = algod.account_info(keeper.address)["amount"]
        logger.info(f"Keeper {keeper.address}: {balance} µALGO")
        if balance < KEEPER_FLOOR_MICROALGO:
            raise SystemExit(
                f"Keeper {keeper.address} holds {balance} µALGO, too little to take "
                f"part. Fund it above {KEEPER_FLOOR_MICROALGO} µALGO."
            )

    # ------------------------------------------------------------------
    if args.upkeep_id is not None:
        upkeep_id = args.upkeep_id
    else:
        target_app = args.target_app
        if target_app is None:
            if args.network != net.LOCALNET:
                parser.error(
                    "--target-app is required off LocalNet: a race needs something "
                    "real to call, and this script will not guess at an app id"
                )
            from smart_contracts.pulse.deploy_config import deploy as deploy_pulse

            target_app = deploy_pulse().app_id
        upkeep_id = _register_race_upkeep(algorand, client, creator, target_app)
        logger.info(
            f"Upkeep {upkeep_id} registered against app {target_app}, "
            f"every {RACE_INTERVAL_ROUNDS} rounds at {RACE_FEE_MICROALGO} µALGO"
        )

    before = keeper_bot.read_upkeep(algod, app_id, upkeep_id)
    if before is None:
        raise SystemExit(f"Upkeep {upkeep_id} does not exist on app {app_id}")
    logger.info(f"Waiting for round {before.next_execution_round}…")
    net.wait_for_round(algorand, before.next_execution_round, poker=creator)
    before = keeper_bot.read_upkeep(algod, app_id, upkeep_id) or before
    balances_before = {k.address: algod.account_info(k.address)["amount"] for k in keepers}

    # ------------------------------------------------------------------
    logger.info(f"── Two keepers, one due upkeep, {args.barrier}s barrier ──")
    processes = [(k, _launch(args.network, app_id, k, args.barrier)) for k in keepers]
    outcomes = [_collect(keeper, process) for keeper, process in processes]

    for outcome in outcomes:
        logger.info(f"── {outcome.address} ──")
        for line in outcome.log.splitlines():
            logger.info(f"   {line}")

    # ------------------------------------------------------------------
    logger.info("── What the chain says ──")
    after = keeper_bot.read_upkeep(algod, app_id, upkeep_id)
    winners = [o for o in outcomes if o.of("executed", upkeep_id)]
    losers = [o for o in outcomes if o.of("race_lost", upkeep_id)]

    if after is not None:
        landed_in = after.last_serviced_round
        from_block = keeper_bot.find_winner(algod, app_id, upkeep_id, landed_in)
        logger.info(
            f"   upkeep {upkeep_id}: executed {after.times_executed} time(s), "
            f"last serviced in round {landed_in}"
        )
        logger.info(f"   the block at {landed_in} names {from_block or 'nobody'}")

    for outcome in outcomes:
        moved = algod.account_info(outcome.address)["amount"] - balances_before[outcome.address]
        role = (
            "won" if outcome in winners else "lost" if outcome in losers else "did not compete"
        )
        logger.info(f"   {outcome.address} {role}, balance moved {moved:+d} µALGO")

    if not (len(winners) == 1 and len(losers) == 1):
        logger.warning(
            f"No collision: {len(winners)} execution(s) and {len(losers)} lost race(s). "
            f"The two keepers did not reach for upkeep {upkeep_id} in the same round. "
            f"Run it again; on a public network this takes a few attempts."
        )
        _cleanup(client, upkeep_id, args.keep)
        raise SystemExit(1)

    winner, loser = winners[0], losers[0]
    lost = loser.of("race_lost", upkeep_id) or {}
    won = winner.of("executed", upkeep_id) or {}
    spent = lost.get("spent")
    logger.info("")
    logger.info(f"   winner {winner.address}")
    logger.info(f"     collected {won.get('fee_collected')} µALGO in {won.get('tx_id')}")
    logger.info(f"   loser  {loser.address}")
    logger.info(f"     forfeited {lost.get('fee_forgone')} µALGO and spent {spent} µALGO")
    logger.info(f"     its transaction {lost.get('tx_id')} is in no block")

    assert spent == 0, f"the losing keeper paid {spent} µALGO, which it should not have"
    assert lost.get("winner") in (None, winner.address), (
        f"the loser named {lost.get('winner')} as the winner, but "
        f"{winner.address} is who executed it"
    )
    on_chain = _landed(algorand, lost.get("tx_id"))
    if on_chain is None:
        logger.info("   (no indexer answered, so the loser's transaction was not looked up)")
    else:
        assert on_chain is False, "the losing transaction reached a block after all"
        logger.info("   an indexer has never heard of it, which is the whole claim")

    _cleanup(client, upkeep_id, args.keep)
    logger.info("")
    logger.info(f"A real race happened on {args.network}, and losing it cost {spent} µALGO.")


def _cleanup(client: KeeperClient, upkeep_id: int, keep: bool) -> None:
    if keep:
        logger.info(f"Leaving upkeep {upkeep_id} registered (--keep)")
        return
    try:
        client.send.cancel(
            args=CancelArgs(upkeep_id=upkeep_id),
            params=algokit_utils.CommonAppCallParams(
                extra_fee=algokit_utils.AlgoAmount(micro_algo=1_000)
            ),
        )
        logger.info(f"Cancelled upkeep {upkeep_id}; escrow and box deposit refunded")
    except Exception as exc:
        logger.warning(f"Could not cancel upkeep {upkeep_id}: {exc}")


if __name__ == "__main__":
    main()
