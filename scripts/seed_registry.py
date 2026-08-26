"""Register a set of upkeeps that exercises every feature of the contract.

A deployment with one upkeep proves very little. This registers a spread that
puts each behaviour on a real chain and leaves it there, so the registry is
evidence rather than a demo:

* a **burn-in** upkeep on a short cadence, which fires repeatedly within
  minutes and then goes dormant on purpose — proof that executions work under
  real block times rather than LocalNet's dev mode;
* a **catch-up** and a **skip-ahead** upkeep, the two scheduling policies;
* an **escalating** upkeep with a fee ceiling;
* a **multi-argument** upkeep, which nothing before #8 could call at all.

Each is funded for a stated number of runs, so what it costs is visible before
anything is sent. Nothing here is required for the network to work — it is
here to be watched.

Run:  poetry run python -m scripts.seed_registry --network testnet --app-id N --target N
      poetry run python -m scripts.seed_registry --network testnet --app-id N --target N --commit
"""

import argparse
import logging

import algokit_utils
from algosdk import abi

from scripts import keeper_bot, network as net
from smart_contracts.artifacts.keeper.keeper_client import KeeperClient, RegisterArgs
from smart_contracts.keeper.contract import (
    BOX_MBR_FIXED,
    CATCH_UP,
    MIN_INTERVAL_ROUNDS,
    MIN_UPKEEP_FEE,
    SKIP_AHEAD,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

SECONDS_PER_ROUND = 2.8
TICK = abi.Method.from_signature("tick()uint64").get_selector()
TICK_WITH = abi.Method.from_signature("tick_with(uint64,string)uint64").get_selector()


def _rounds(seconds: float) -> int:
    return max(MIN_INTERVAL_ROUNDS, int(seconds / SECONDS_PER_ROUND))


# label, interval, runs funded, policy, fee ceiling, call args
PLAN = [
    (
        "burn-in — fires every ~70s until its escrow is gone",
        25, 20, CATCH_UP, 0, [TICK],
    ),
    (
        # Shaped for the half-hourly cron keeper rather than for a laptop.
        # CATCH_UP on a 70 second cadence is what starved upkeep 18: a keeper
        # that checks every thirty minutes finds ~25 missed intervals waiting
        # and replays them all, one fee each, so the escrow is gone in a run
        # or two. SKIP_AHEAD on a ten minute cadence gives that same keeper
        # exactly one execution per visit and a runway measured in hours.
        "heartbeat — one execution per half-hourly keeper visit",
        _rounds(10 * 60), 24, SKIP_AHEAD, 0, [TICK],
    ),
    (
        "catch-up — replays every missed interval",
        _rounds(12 * 3600), 30, CATCH_UP, 0, [TICK],
    ),
    (
        "skip-ahead — drops the backlog, keeps the phase",
        _rounds(12 * 3600), 30, SKIP_AHEAD, 0, [TICK],
    ),
    (
        "escalating — fee climbs to 3x while it is late",
        # Funded at the ceiling, so fewer runs buy the same headroom.
        _rounds(12 * 3600), 15, SKIP_AHEAD, MIN_UPKEEP_FEE * 3, [TICK],
    ),
    (
        "multi-argument — unreachable before #8",
        _rounds(12 * 3600), 30, SKIP_AHEAD, 0,
        [TICK_WITH, (1).to_bytes(8, "big"), abi.ABIType.from_string("string").encode("arcron")],
    ),
]


def _encode_args(call_args: list[bytes]) -> bytes:
    return abi.ABIType.from_string("byte[][]").encode([list(a) for a in call_args])


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    net.add_network_argument(parser)
    parser.add_argument("--app-id", type=int, required=True, help="the keeper app")
    parser.add_argument("--target", type=int, required=True, help="the app to call (pulse)")
    parser.add_argument(
        "--commit", action="store_true", help="actually register; otherwise price it and stop"
    )
    parser.add_argument(
        "--only",
        help="register just the seeds whose label contains this text, case-insensitively"
    )
    args = parser.parse_args(argv)

    algorand = net.connect(args.network)
    deployer = algorand.account.from_environment("DEPLOYER")

    plan = PLAN
    if args.only:
        plan = [seed for seed in PLAN if args.only.lower() in seed[0].lower()]
        if not plan:
            raise SystemExit(
                f"--only {args.only!r} matched no seed. Labels are:\n  "
                + "\n  ".join(seed[0] for seed in PLAN)
            )

    total = 0
    logger.info("")
    logger.info(f"{'upkeep':<52} {'every':>10} {'runs':>5} {'costs':>9}")
    priced = []
    for label, interval, runs, policy, cap, call_args in plan:
        # Funding must cover a run at the price it can actually be charged.
        per_run = max(MIN_UPKEEP_FEE, cap)
        mbr = BOX_MBR_FIXED + 400 * len(_encode_args(call_args))
        cost = mbr + per_run * runs
        total += cost
        priced.append((label, interval, runs, policy, cap, call_args, mbr, per_run * runs))
        human = f"{interval * SECONDS_PER_ROUND / 3600:.1f}h" if interval > 1200 else f"{interval * SECONDS_PER_ROUND:.0f}s"
        logger.info(f"{label:<52} {human:>10} {runs:>5} {cost/1e6:>8.4f}A")
    logger.info(f"{'':<52} {'':>10} {'':>5} {total/1e6:>8.4f}A")

    info = algorand.client.algod.account_info(deployer.address)
    spendable = info["amount"] - info["min-balance"]
    logger.info("")
    logger.info(f"deployer has {spendable/1e6:.4f} ALGO spendable")
    if total > spendable:
        raise SystemExit(f"not enough: this plan needs {total/1e6:.4f} ALGO")
    if not args.commit:
        logger.info("Priced only. Pass --commit to register.")
        return

    client = KeeperClient(
        algorand=algorand, app_id=args.app_id,
        default_sender=deployer.address, default_signer=deployer.signer,
    )
    logger.info("")
    for label, interval, runs, policy, cap, call_args, mbr, escrow in priced:
        first_valid = algorand.client.algod.status()["last-round"]

        def payment(amount: int):
            return algorand.create_transaction.payment(
                algokit_utils.PaymentParams(
                    sender=deployer.address, receiver=client.app_address,
                    amount=algokit_utils.AlgoAmount(micro_algo=amount),
                    first_valid_round=first_valid, last_valid_round=first_valid + 1_000,
                )
            )

        upkeep_id = client.send.register(args=RegisterArgs(
            mbr_payment=payment(mbr), funding_payment=payment(escrow),
            target_app=args.target, call_args=call_args, interval_rounds=interval,
            fee_per_execution=MIN_UPKEEP_FEE, policy=policy, fee_cap=cap,
            fee_asset=0, asset_fee=0,
        )).abi_return
        logger.info(f"  upkeep {upkeep_id:>3}  {label}")

    after = algorand.client.algod.account_info(deployer.address)
    logger.info("")
    logger.info(f"deployer now has {(after['amount']-after['min-balance'])/1e6:.4f} ALGO spendable")


if __name__ == "__main__":
    main()
