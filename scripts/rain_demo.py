"""A pot that rains a little, often, on everyone who entered.

Hub shape: bootstrap, create one SPLIT rain, enter, deposit, let a keeper
call `draw()`, claim. The scheduled call does accounting only.

Run:  poetry run python -m scripts.rain_demo [--network localnet]
"""

import argparse
import logging

import algokit_utils

from scripts import keeper_bot, network as net
from scripts.keeper_e2e import _assert, _box_mbr, _quiet, _read_upkeep, _selector
from smart_contracts.artifacts.keeper.keeper_client import CancelArgs, RegisterArgs
from smart_contracts.artifacts.rain.rain_client import (
    AllocationOfArgs,
    BootstrapArgs,
    ClaimArgs,
    CreateRainArgs,
    DepositArgs,
    EnterArgs,
    RainFactory,
    RainOfArgs,
)
from smart_contracts.keeper.contract import SKIP_AHEAD
from smart_contracts.keeper.deploy_config import deploy as deploy_keeper
from smart_contracts.rain.contract import (
    APP_BASE_MBR,
    RAIN_BOX_MBR,
    SPLIT,
    TICKET_MBR,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

ZERO_ADDRESS = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAY5HFKQ"
DRAW_SIGNATURE = "draw()uint64"
INTERVAL_ROUNDS = 10
FEE = 4_000
POT = 1_000_000
DRIP = 100_000
PLAYERS = 3


def _label(text: str) -> bytes:
    return text.encode()[:32].ljust(32, b"\x00")


def _payment(algorand, sender: str, receiver: str, amount: int):
    return algorand.create_transaction.payment(
        algokit_utils.PaymentParams(
            sender=sender,
            receiver=receiver,
            amount=algokit_utils.AlgoAmount(micro_algo=amount),
        )
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    net.add_network_argument(parser)
    args = parser.parse_args(argv)

    algorand = net.connect(args.network)
    founder = algorand.account.from_environment("DEPLOYER")
    algod = algorand.client.algod
    keeper_client = deploy_keeper()

    def fund(who, amount: int) -> None:
        algorand.send.payment(
            algokit_utils.PaymentParams(
                sender=founder.address,
                receiver=who.address,
                amount=algokit_utils.AlgoAmount(micro_algo=amount),
            )
        )

    rain, _ = algorand.client.get_typed_app_factory(
        RainFactory, default_sender=founder.address
    ).send.create.bare()
    rain.send.bootstrap(
        args=BootstrapArgs(
            mbr_payment=_payment(algorand, founder.address, rain.app_address, APP_BASE_MBR)
        )
    )
    rain_id = rain.send.create_rain(
        args=CreateRainArgs(
            mbr_payment=_payment(algorand, founder.address, rain.app_address, RAIN_BOX_MBR),
            label=_label("demo"),
            gate_creator=ZERO_ADDRESS,
            prize_asset=0,
            drip=DRIP,
            interval_rounds=INTERVAL_ROUNDS,
            mode=SPLIT,
            wave_cap=0,
        )
    ).abi_return
    logger.info(f"  Rain {rain_id} on hub {rain.app_id}")

    players = [algorand.account.random() for _ in range(PLAYERS)]
    for who in players:
        fund(who, 500_000)
        rain.send.enter(
            args=EnterArgs(
                mbr_payment=_payment(algorand, who.address, rain.app_address, TICKET_MBR),
                rain_id=rain_id,
                gate_asset=0,
            ),
            params=algokit_utils.CommonAppCallParams(sender=who.address),
        )

    rain.send.deposit(
        args=DepositArgs(
            payment=_payment(algorand, founder.address, rain.app_address, POT),
            rain_id=rain_id,
        )
    )

    register = keeper_client.send.register(
        args=RegisterArgs(
            mbr_payment=_payment(
                algorand,
                founder.address,
                keeper_client.app_address,
                _box_mbr([_selector(DRAW_SIGNATURE)]),
            ),
            funding_payment=_payment(
                algorand, founder.address, keeper_client.app_address, FEE * 20
            ),
            target_app=rain.app_id,
            call_args=[_selector(DRAW_SIGNATURE)],
            interval_rounds=INTERVAL_ROUNDS,
            fee_per_execution=FEE,
            policy=SKIP_AHEAD,
            fee_cap=0,
            fee_asset=0,
            asset_fee=0,
        )
    )
    upkeep_id = register.abi_return
    rec, _ = _read_upkeep(algorand, keeper_client.app_id, upkeep_id)
    due = rec.next_execution_round
    logger.info(f"  Upkeep {upkeep_id} due at {due}")

    with _quiet():
        # LocalNet is dev mode: a block is only produced per transaction, so
        # polling for a round that nothing is advancing never returns. This is
        # what wait_for_round's poker is for.
        net.wait_for_round(algorand, due, poker=founder)
        keeper_bot.main(
            ["--once", "--network", args.network, "--app-id", str(keeper_client.app_id)]
        )

    rec = rain.send.rain_of(args=RainOfArgs(rain_id=rain_id)).abi_return
    share = DRIP // PLAYERS
    _assert("pot shrank by the paid slice", rec.pot, POT - share * PLAYERS)
    _assert("each ticket is owed the share", rec.cumulative, share)

    for who in players:
        owed = rain.send.allocation_of(
            args=AllocationOfArgs(rain_id=rain_id, who=who.address)
        ).abi_return
        _assert("allocation", owed, share)
        claimed = rain.send.claim(
            args=ClaimArgs(rain_id=rain_id, gate_asset=0),
            params=algokit_utils.CommonAppCallParams(
                sender=who.address,
                # The payout is an inner transaction sent with Fee: 0, so the
                # group has to carry it.
                extra_fee=algokit_utils.AlgoAmount(micro_algo=1_000),
            ),
        ).abi_return
        _assert("claim", claimed, share)

    keeper_client.send.cancel(
        args=CancelArgs(upkeep_id=upkeep_id),
        # cancel refunds escrow and box MBR by inner payment, also Fee: 0.
        params=algokit_utils.CommonAppCallParams(
            extra_fee=algokit_utils.AlgoAmount(micro_algo=1_000)
        ),
    )
    logger.info("Rain hub demo passed.")
    logger.info(f"  Hub {rain.app_id}, keeper {keeper_client.app_id}, upkeep {upkeep_id}")


if __name__ == "__main__":
    main()
