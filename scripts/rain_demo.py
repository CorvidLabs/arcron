"""A pot that pays a random ticket holder on a schedule, end to end.

Shows the shape that makes this work under Arcron's v1 call shape: the
scheduled call does accounting only, and the step that needs a resource (the
beacon read) happens in a transaction a participant sends for themselves,
attaching the reference a keeper could not.

Run:  poetry run python -m scripts.rain_demo [--network localnet]
"""

import argparse
import hashlib
import logging

import algokit_utils

from scripts import keeper_bot, network as net
from scripts.keeper_e2e import _assert, _box_mbr, _quiet, _read_upkeep, _selector
from smart_contracts.artifacts.beacon_stub.beacon_stub_client import BeaconStubFactory
from smart_contracts.artifacts.keeper.keeper_client import CancelArgs, RegisterArgs
from smart_contracts.artifacts.rain.rain_client import (
    AllocationOfArgs,
    ClaimArgs,
    ConfigureArgs,
    DepositArgs,
    EnterArgs,
    RainClient,
    RainFactory,
)
from smart_contracts.keeper.contract import SKIP_AHEAD
from smart_contracts.keeper.deploy_config import deploy as deploy_keeper
from smart_contracts.rain.contract import ALLOCATION_MBR, BEACON_DELAY, TICKET_MBR

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# algosdk's encoding of the all-zero address, which means 'no gate'.
ZERO_ADDRESS = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAY5HFKQ"

DRAW_SIGNATURE = "draw()uint64"
INTERVAL_ROUNDS = 10
FEE = 4_000
POT = 1_000_000
PLAYERS = 3


def _expected_ticket(commit_round: int, tickets: int) -> int:
    """What the stub beacon will say, computed independently.

    The stub is sha256(itob(round)), deterministic on purpose, so the winner
    can be predicted here and compared against the one the contract picks.
    """
    digest = hashlib.sha256(commit_round.to_bytes(8, "big")).digest()
    return int.from_bytes(digest[:8], "big") % tickets


def show(algorand, app_id: int, network: str) -> int:
    """Print what a rain deployment's fairness actually rests on.

    `configure` accepts any non-zero `beacon_app`, runs once, and rain has no
    update path, so that one value decides every draw the instance will ever
    run. A deployer who points it at a contract they control picks every
    winner. Participants can check, and could not before: nothing surfaced the
    value anywhere they would look.
    """
    import base64

    state = algorand.client.algod.application_info(app_id)["params"].get("global-state", [])
    read = {}
    for entry in state:
        key = base64.b64decode(entry["key"]).decode(errors="replace")
        value = entry["value"]
        read[key] = value.get("uint", 0) if value.get("type") == 2 else value.get("bytes", "")

    beacon = int(read.get("beacon_app", 0))
    expected = net.FOUNDATION_BEACON.get(network)
    logger.info(f"rain app {app_id} on {network}")
    logger.info(f"  beacon_app    {beacon}")
    if expected is not None and beacon == expected:
        logger.info("                The Algorand Foundation randomness beacon.")
    else:
        logger.warning("                NOT a beacon this tool recognises.")
        logger.warning("                Whoever deployed this chose it, it cannot be changed,")
        logger.warning("                and it decides every draw. Ask them why before entering.")
    logger.info(f"  prize_asset   {int(read.get('prize_asset', 0)) or 'ALGO'}")
    logger.info(f"  tickets       {int(read.get('tickets', 0))}")
    return 0 if expected is not None and beacon == expected else 1


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    net.add_network_argument(parser)
    parser.add_argument(
        "--show",
        type=int,
        metavar="APP_ID",
        help="inspect a deployed rain app's beacon and gate instead of running the demo",
    )
    args = parser.parse_args(argv)

    if args.show:
        raise SystemExit(show(net.connect(args.network), args.show, args.network))

    algorand = net.connect(args.network)
    host = algorand.account.from_environment("DEPLOYER")
    algod = algorand.client.algod
    keeper_client = deploy_keeper()

    # ------------------------------------------------------------------
    logger.info("── 1. A pot, a beacon and some players ──")
    beacon, _ = algorand.client.get_typed_app_factory(
        BeaconStubFactory, default_sender=host.address
    ).send.create.bare()
    rain, _ = algorand.client.get_typed_app_factory(
        RainFactory, default_sender=host.address
    ).send.create.bare()
    algorand.send.payment(
        algokit_utils.PaymentParams(
            sender=host.address,
            receiver=rain.app_address,
            amount=algokit_utils.AlgoAmount(micro_algo=200_000),
        )
    )
    # Open entry, ALGO prize: the original shape. scripts/community_rain_demo.py
    # runs the gated, asset-paying one.
    rain.send.configure(
        args=ConfigureArgs(beacon_app=beacon.app_id, gate_creator=ZERO_ADDRESS, prize_asset=0)
    )
    logger.info(f"  Rain app {rain.app_id}, beacon stub {beacon.app_id}")

    players = []
    for index in range(PLAYERS):
        player = algorand.account.random()
        algorand.send.payment(
            algokit_utils.PaymentParams(
                sender=host.address,
                receiver=player.address,
                amount=algokit_utils.AlgoAmount(micro_algo=500_000),
            )
        )
        client = RainClient(
            algorand=algorand,
            app_id=rain.app_id,
            default_sender=player.address,
            default_signer=player.signer,
        )
        ticket = client.send.enter(
            args=EnterArgs(
                mbr_payment=algorand.create_transaction.payment(
                    algokit_utils.PaymentParams(
                        sender=player.address,
                        receiver=rain.app_address,
                        amount=algokit_utils.AlgoAmount(micro_algo=TICKET_MBR),
                    )
                ),
                # Ungated, so nothing is checked and asset 0 stands for none.
                gate_asset=0,
            )
        ).abi_return
        players.append((player, client))
        logger.info(f"  Player {index} holds ticket {ticket}")

    rain.send.deposit(
        args=DepositArgs(
            payment=algorand.create_transaction.payment(
                algokit_utils.PaymentParams(
                    sender=host.address,
                    receiver=rain.app_address,
                    amount=algokit_utils.AlgoAmount(micro_algo=POT),
                )
            )
        )
    )
    _assert("pot", rain.state.global_state.pot, POT)

    # ------------------------------------------------------------------
    logger.info("── 2. An upkeep points a keeper at draw() ──")
    call_data = _selector(DRAW_SIGNATURE)

    def payment(amount: int):
        return algorand.create_transaction.payment(
            algokit_utils.PaymentParams(
                sender=host.address,
                receiver=keeper_client.app_address,
                amount=algokit_utils.AlgoAmount(micro_algo=amount),
            )
        )

    upkeep_id = keeper_client.send.register(
        args=RegisterArgs(
            mbr_payment=payment(_box_mbr([call_data])),
            funding_payment=payment(FEE * 4),
            target_app=rain.app_id,
            call_args=[call_data],
            interval_rounds=INTERVAL_ROUNDS,
            fee_per_execution=FEE,
            # A missed day's draw is not worth replaying; only the latest matters.
            policy=SKIP_AHEAD,
            fee_cap=0,
            fee_asset=0,
            asset_fee=0,
        )
    ).abi_return
    upkeep, _ = _read_upkeep(algorand, keeper_client.app_id, upkeep_id)

    # ------------------------------------------------------------------
    logger.info("── 3. The keeper opens a draw, accounting only ──")
    net.wait_for_round(algorand, upkeep.next_execution_round, poker=host)
    keeper_bot.main(
        ["--once", "--network", args.network, "--app-id", str(keeper_client.app_id)]
    )
    state = rain.state.global_state
    _assert("draw id", state.draw_id, 1)
    _assert("draw open", state.draw_open, 1)
    _assert("prize locked", state.prize, POT - ALLOCATION_MBR)
    _assert("pot emptied into the prize", state.pot, 0)
    commit_round = state.commit_round
    logger.info(f"  Draw 1 open; the beacon decides it at round {commit_round}")

    # Nobody can know the winner yet: the deciding round has not happened.
    assert commit_round > algod.status()["last-round"], "the draw was decided too early"

    # ------------------------------------------------------------------
    logger.info("── 4. A participant resolves it, supplying the beacon ──")
    net.wait_for_round(algorand, commit_round + 1, poker=host)
    _, resolver = players[0]
    winner = resolver.send.resolve(
        params=algokit_utils.CommonAppCallParams(
            extra_fee=algokit_utils.AlgoAmount(micro_algo=1_000),
            app_references=[beacon.app_id],
        )
    ).abi_return
    expected_ticket = _expected_ticket(commit_round, PLAYERS)
    expected_winner = players[expected_ticket][0].address
    _assert("winning ticket (predicted independently)", winner, expected_winner)
    logger.info(f"  Ticket {expected_ticket} won: {winner}")

    # ------------------------------------------------------------------
    logger.info("── 5. The winner pulls the prize ──")
    winning_client = next(client for player, client in players if player.address == winner)
    _assert(
        "allocated",
        rain.send.allocation_of(args=AllocationOfArgs(who=winner)).abi_return,
        POT - ALLOCATION_MBR,
    )
    before = algod.account_info(winner)["amount"]
    claimed = winning_client.send.claim(
        # Ungated draw: `gate_creator` is the zero address, so `claim` never
        # reads this. Asset 0 is the conventional "no asset" reference.
        args=ClaimArgs(gate_asset=0),
        params=algokit_utils.CommonAppCallParams(
            extra_fee=algokit_utils.AlgoAmount(micro_algo=1_000)
        )
    ).abi_return
    _assert("claimed", claimed, POT - ALLOCATION_MBR)
    _assert(
        "paid out (net of the 2,000 µALGO the winner spent claiming)",
        algod.account_info(winner)["amount"] - before,
        POT - ALLOCATION_MBR - 2_000,
    )
    _assert(
        "nothing left allocated",
        rain.send.allocation_of(args=AllocationOfArgs(who=winner)).abi_return,
        0,
    )
    _assert("the reservation returned to the pot", rain.state.global_state.pot, ALLOCATION_MBR)

    # ------------------------------------------------------------------
    logger.info("── 6. A quiet cadence is uneventful ──")
    # The pot is empty again, so the scheduled call must do nothing at all;
    # a failure here would trip keeper backoff and stop the draw forever.
    after, _ = _read_upkeep(algorand, keeper_client.app_id, upkeep_id)
    net.wait_for_round(algorand, after.next_execution_round, poker=host)
    keeper_bot.main(
        ["--once", "--network", args.network, "--app-id", str(keeper_client.app_id)]
    )
    _assert("still one draw", rain.state.global_state.draw_id, 1)
    _assert("no draw left open", rain.state.global_state.draw_open, 0)
    drained, _ = _read_upkeep(algorand, keeper_client.app_id, upkeep_id)
    _assert("the keeper was still paid", drained.times_executed, 2)

    with _quiet():
        keeper_client.send.cancel(
            args=CancelArgs(upkeep_id=upkeep_id),
            params=algokit_utils.CommonAppCallParams(
                extra_fee=algokit_utils.AlgoAmount(micro_algo=1_000)
            ),
        )

    logger.info("")
    logger.info(f"Rain demo passed on {args.network} ✔")
    logger.info(f"  Rain app {rain.app_id}, keeper app {keeper_client.app_id}")
    logger.info(f"  Draw 1: ticket {expected_ticket} of {PLAYERS} won {POT - ALLOCATION_MBR} µALGO")
    logger.info("  The scheduled call never touched the beacon, the pot, or a player.")


if __name__ == "__main__":
    main()
