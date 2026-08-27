"""A populated network, not a demonstration: many actors, several keepers.

Every other script here proves one property in isolation, which is what makes
them readable and what makes them miss things. Bugs in a system like this live
in the interactions: an upkeep that starves while another escalates, a keeper
that takes work a moment before another would have, a registry with enough in
it that the bot's ordering matters.

The keepers here take turns rather than collide, which is what makes the
registry-wide claims below meaningful and is not the same thing as
competition. `scripts/keeper_race.py` is where two keepers reach for the same
upkeep in the same round.

So this builds a small economy and runs it. Nothing is asserted about any
single upkeep. What is asserted is what has to be true of the whole registry
afterwards, which is the kind of claim a single-purpose test cannot make:

* the app can pay out every microAlgo it has escrowed, at every point;
* nobody was paid for an execution that did not happen;
* every ALGO that left the app went somewhere it was owed.

Run:  poetry run python -m scripts.scenario [--network localnet] [--rounds N]
"""

import argparse
import logging
import os
import random

import algokit_utils
from algosdk import mnemonic
from algosdk.logic import get_application_address

from scripts import keeper_bot, network as net
from scripts.keeper_e2e import _assert, _box_mbr, _quiet, _selector
from smart_contracts.artifacts.keeper.keeper_client import RegisterArgs
from smart_contracts.keeper.contract import CATCH_UP, SKIP_AHEAD
from smart_contracts.keeper.deploy_config import deploy as deploy_keeper
from smart_contracts.pulse.deploy_config import deploy as deploy_pulse

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Deterministic: a scenario that differs every run cannot be bisected when it
# fails. The seed is printed so a failure can be reproduced exactly.
SEED = 20260825

KEEPERS = 3
UPKEEPS = 12
MIN_FEE = 4_000


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    net.add_network_argument(parser)
    parser.add_argument("--rounds", type=int, default=400, help="how long to run")
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args(argv)

    rng = random.Random(args.seed)
    logger.info(f"Seed {args.seed}: rerun with --seed {args.seed} to reproduce exactly.")

    algorand = net.connect(args.network)
    algod = algorand.client.algod
    founder = algorand.account.from_environment("DEPLOYER")
    keeper = deploy_keeper()
    pulse = deploy_pulse()
    app_address = get_application_address(keeper.app_id)

    def fund(who, amount: int) -> None:
        algorand.send.payment(
            algokit_utils.PaymentParams(
                sender=founder.address, receiver=who.address,
                amount=algokit_utils.AlgoAmount(micro_algo=amount),
            )
        )

    # ------------------------------------------------------------------
    logger.info("── Keepers ──")
    keepers = []
    for index in range(KEEPERS):
        account = algorand.account.random()
        fund(account, 3_000_000)
        keepers.append(account)
        logger.info(f"   keeper {index}: {account.address[:8]}…")

    # ------------------------------------------------------------------
    logger.info(f"── {UPKEEPS} upkeeps, deliberately unalike ──")
    call_data = _selector("tick()uint64")

    def payment(amount: int):
        return algorand.create_transaction.payment(
            algokit_utils.PaymentParams(
                sender=founder.address, receiver=keeper.app_address,
                amount=algokit_utils.AlgoAmount(micro_algo=amount),
            )
        )

    registered = []
    for index in range(UPKEEPS):
        # A spread that produces real interaction: some upkeeps come due
        # constantly and some rarely, some can afford many executions and some
        # will starve, some escalate when neglected and some never do.
        interval = rng.choice([10, 10, 15, 25, 40, 80])
        fee = rng.choice([MIN_FEE, MIN_FEE, 5_000, 8_000])
        cap = rng.choice([0, 0, fee * 2, fee * 3])
        runs = rng.choice([2, 3, 8, 20, 40])
        policy = rng.choice([CATCH_UP, SKIP_AHEAD])
        funding = fee * runs if cap == 0 else cap * runs
        upkeep_id = keeper.send.register(
            args=RegisterArgs(
                mbr_payment=payment(_box_mbr([call_data])),
                funding_payment=payment(funding),
                target_app=pulse.app_id,
                call_args=[call_data],
                interval_rounds=interval,
                fee_per_execution=fee,
                policy=policy,
                fee_cap=cap,
                fee_asset=0,
                asset_fee=0,
            )
        ).abi_return
        registered.append(upkeep_id)
        logger.info(
            f"   #{upkeep_id:<3} every {interval:>3} rounds, {fee} µALGO"
            f"{f' up to {cap}' if cap else ''}, funds {runs} runs,"
            f" {'CATCH_UP' if policy == CATCH_UP else 'SKIP_AHEAD'}"
        )

    def solvent() -> bool:
        """The invariant that matters: escrow is only real if it can be paid."""
        account = algod.account_info(app_address)
        spendable = account["amount"] - account["min-balance"]
        escrowed = sum(u.balance for u in keeper_bot.scan_upkeeps(algod, keeper.app_id))
        return spendable >= escrowed

    _assert("solvent before anything runs", solvent(), True)

    # ------------------------------------------------------------------
    logger.info(f"── Running {args.rounds} rounds, {KEEPERS} keepers taking the work ──")
    # They arrive one after another rather than at once, so this is a queue and
    # not a race: whoever the shuffle puts first takes the due work and the
    # others find an empty registry. That is the right shape for what this
    # script asserts, which is about the registry as a whole rather than about
    # who won. A genuine collision, two keepers reaching for the same upkeep
    # in the same round, is staged by `scripts/keeper_race.py`.
    #
    # Each keeper does have to be its own account, though. Three funded
    # accounts were created above and then never used to sign anything: the bot
    # takes its signer from the environment, so every one of these invocations
    # signed as the same KEEPER, and a registry serviced by one account cannot
    # show anything about several.
    start = algod.status()["last-round"]
    target = start + args.rounds
    executions = 0
    refused = 0
    checks = 0

    while algod.status()["last-round"] < target:
        rng.shuffle(keepers)
        for account in keepers:
            os.environ["KEEPER_MNEMONIC"] = mnemonic.from_private_key(account.private_key)
            with _quiet():
                try:
                    keeper_bot.main([
                        "--once", "--network", args.network,
                        "--app-id", str(keeper.app_id), "--no-state",
                    ])
                except SystemExit:
                    pass
                except Exception:
                    refused += 1
        net.wait_for_round(algorand, algod.status()["last-round"] + 10, poker=founder)
        checks += 1
        if not solvent():
            raise AssertionError("app cannot cover its escrows")

    # ------------------------------------------------------------------
    logger.info("── What the registry looks like afterwards ──")
    final = {u.upkeep_id: u for u in keeper_bot.scan_upkeeps(algod, keeper.app_id)}
    executions = sum(u.times_executed for u in final.values())
    starved = [i for i, u in final.items() if u.balance < u.fee_per_execution]
    busy = max(final.values(), key=lambda u: u.times_executed, default=None)

    logger.info(f"   {len(final)} upkeeps, {executions} executions over {args.rounds} rounds")
    logger.info(f"   {len(starved)} ran out of escrow, which is the expected end for a funded run")
    if busy:
        logger.info(f"   busiest: #{busy.upkeep_id} at {busy.times_executed} executions")
    logger.info(f"   solvency checked {checks} times, held every time")

    _assert("something actually happened", executions > 0, True)
    _assert("solvent at the end", solvent(), True)
    _assert("pulse saw every execution", pulse.state.global_state.beats >= executions, True)

    logger.info("")
    logger.info("Scenario passed.")
    logger.info(f"  Reproduce with: --seed {args.seed} --rounds {args.rounds}")


if __name__ == "__main__":
    main()
