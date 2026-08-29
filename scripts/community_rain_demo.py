"""A rain for the holders of an NFT collection, paying a token.

Hub shape: opt the app into the prize, create a gated SPLIT rain, prove the
gate (holder in, impostor out), fund with the token, draw, claim.

Run:  poetry run python -m scripts.community_rain_demo [--network localnet]
"""

import argparse
import logging

import algokit_utils

from scripts import network as net
from scripts.keeper_e2e import _assert
from smart_contracts.artifacts.rain.rain_client import (
    BootstrapArgs,
    ClaimArgs,
    CreateRainArgs,
    DepositAssetArgs,
    EnterArgs,
    OptInPrizeAssetArgs,
    RainFactory,
    RainOfArgs,
)
from smart_contracts.rain.contract import (
    APP_BASE_MBR,
    ASSET_OPT_IN_MBR,
    RAIN_BOX_MBR,
    SPLIT,
    TICKET_MBR,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

INTERVAL_ROUNDS = 10
DRIP = 100
POT = 1_000


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
    artist = algorand.account.random()
    holder = algorand.account.random()
    outsider = algorand.account.random()

    def fund(who, amount: int) -> None:
        algorand.send.payment(
            algokit_utils.PaymentParams(
                sender=founder.address,
                receiver=who.address,
                amount=algokit_utils.AlgoAmount(micro_algo=amount),
            )
        )

    for who in (artist, holder, outsider):
        fund(who, 2_000_000)

    nft = algorand.send.asset_create(
        algokit_utils.AssetCreateParams(
            sender=artist.address, total=1, decimals=0, asset_name="Corvid", unit_name="corvid1"
        )
    ).asset_id
    prize = algorand.send.asset_create(
        algokit_utils.AssetCreateParams(
            sender=founder.address,
            total=1_000_000,
            decimals=0,
            asset_name="Corvid Points",
            unit_name="CPT",
            manager="",
            freeze="",
            clawback="",
        )
    ).asset_id
    impostor = algorand.send.asset_create(
        algokit_utils.AssetCreateParams(
            sender=outsider.address, total=1, decimals=0, asset_name="Fake", unit_name="corvid1"
        )
    ).asset_id

    algorand.send.asset_opt_in(algokit_utils.AssetOptInParams(sender=holder.address, asset_id=nft))
    algorand.send.asset_transfer(
        algokit_utils.AssetTransferParams(
            sender=artist.address, receiver=holder.address, asset_id=nft, amount=1
        )
    )
    algorand.send.asset_opt_in(
        algokit_utils.AssetOptInParams(sender=outsider.address, asset_id=impostor)
    )
    algorand.send.asset_opt_in(algokit_utils.AssetOptInParams(sender=holder.address, asset_id=prize))

    rain, _ = algorand.client.get_typed_app_factory(
        RainFactory, default_sender=founder.address
    ).send.create.bare()
    rain.send.bootstrap(
        args=BootstrapArgs(
            mbr_payment=_payment(algorand, founder.address, rain.app_address, APP_BASE_MBR)
        )
    )
    rain.send.opt_in_prize_asset(
        args=OptInPrizeAssetArgs(
            prize=prize,
            mbr_payment=_payment(algorand, founder.address, rain.app_address, ASSET_OPT_IN_MBR),
        ),
        # The hub opts itself in with an inner transaction sent at fee=0, so
        # the group has to carry it.
        params=algokit_utils.CommonAppCallParams(
            extra_fee=algokit_utils.AlgoAmount(micro_algo=1_000)
        ),
    )
    rain_id = rain.send.create_rain(
        args=CreateRainArgs(
            mbr_payment=_payment(algorand, founder.address, rain.app_address, RAIN_BOX_MBR),
            label=_label("holders"),
            gate_creator=artist.address,
            prize_asset=prize,
            drip=DRIP,
            interval_rounds=INTERVAL_ROUNDS,
            mode=SPLIT,
            wave_cap=0,
        )
    ).abi_return

    rain.send.enter(
        args=EnterArgs(
            mbr_payment=_payment(algorand, holder.address, rain.app_address, TICKET_MBR),
            rain_id=rain_id,
            gate_asset=nft,
        ),
        params=algokit_utils.CommonAppCallParams(sender=holder.address),
    )
    try:
        rain.send.enter(
            args=EnterArgs(
                mbr_payment=_payment(algorand, outsider.address, rain.app_address, TICKET_MBR),
                rain_id=rain_id,
                gate_asset=impostor,
            ),
            params=algokit_utils.CommonAppCallParams(sender=outsider.address),
        )
        raise SystemExit("impostor was allowed to enter")
    except Exception as exc:
        if "not from the collection" not in str(exc):
            raise

    rain.send.deposit_asset(
        args=DepositAssetArgs(
            transfer=algorand.create_transaction.asset_transfer(
                algokit_utils.AssetTransferParams(
                    sender=founder.address,
                    receiver=rain.app_address,
                    asset_id=prize,
                    amount=POT,
                )
            ),
            rain_id=rain_id,
        )
    )

    # First fire waits one interval from create.
    start = algorand.client.algod.status()["last-round"]
    # LocalNet is dev mode: a block is only produced per transaction, so
    # polling for a round that nothing is advancing never returns.
    net.wait_for_round(algorand, start + INTERVAL_ROUNDS, poker=founder)
    rain.send.draw()
    rec = rain.send.rain_of(args=RainOfArgs(rain_id=rain_id)).abi_return
    _assert("one ticket took the drip", rec.pot, POT - DRIP)
    claimed = rain.send.claim(
        args=ClaimArgs(rain_id=rain_id, gate_asset=nft),
        params=algokit_utils.CommonAppCallParams(
            sender=holder.address,
            # The payout is an inner transaction sent at fee=0.
            extra_fee=algokit_utils.AlgoAmount(micro_algo=1_000),
        ),
    ).abi_return
    _assert("holder claimed the drip", claimed, DRIP)
    logger.info("Community rain demo passed.")
    logger.info(f"  Hub {rain.app_id}, rain {rain_id}, prize {prize}")


if __name__ == "__main__":
    main()
