"""A draw for the holders of an NFT collection, paying a token, run by nobody.

The same scheduled draw as `rain_demo.py`, wearing the shape a project would
actually want: only holders of your collection can enter, the prize is your
token rather than ALGO, anyone can refill the pot, and a keeper fires it on
whatever cadence you registered.

The part worth reading is the gate. A collection on Algorand is not one asset;
it is many assets sharing a creator. So the check cannot be "do you hold asset
X". It is "name an asset you hold, and let the contract confirm the collection
minted it":

    assert Txn.sender.is_opted_in(gate_asset)
    assert gate_asset.balance(Txn.sender) > 0
    assert gate_asset.creator == gate

That works because the entrant sends the transaction and supplies the asset
reference themselves. A scheduled call could not, which is why the gate lives
on `enter` and the scheduled call stays pure accounting.

Run:  poetry run python -m scripts.community_rain_demo [--network localnet]
"""

import argparse
import logging

import algokit_utils

from scripts import keeper_bot, network as net
from scripts.keeper_e2e import _assert, _box_mbr, _quiet, _read_upkeep, _selector
from scripts.rain_demo import _expected_ticket
from smart_contracts.artifacts.beacon_stub.beacon_stub_client import BeaconStubFactory
from smart_contracts.artifacts.keeper.keeper_client import RegisterArgs
from smart_contracts.artifacts.rain.rain_client import (
    ConfigureArgs,
    DepositAssetArgs,
    EnterArgs,
    OptInPrizeAssetArgs,
    RainClient,
    RainFactory,
)
from smart_contracts.keeper.contract import SKIP_AHEAD
from smart_contracts.keeper.deploy_config import deploy as deploy_keeper
from smart_contracts.rain.contract import (
    ASSET_OPT_IN_MBR,
    BEACON_DELAY,
    TICKET_MBR,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

DRAW_SIGNATURE = "draw()uint64"
INTERVAL_ROUNDS = 10
FEE = 4_000
COLLECTION_SIZE = 3
PRIZE_SUPPLY = 1_000_000
POT = 5_000


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

    # ------------------------------------------------------------------
    logger.info("── 1. A collection, and a token to give away ──")
    artist = algorand.account.random()
    fund(artist, 5_000_000)

    collection = []
    for index in range(COLLECTION_SIZE):
        created = algorand.send.asset_create(
            algokit_utils.AssetCreateParams(
                sender=artist.address, total=1, decimals=0,
                asset_name=f"Corvid #{index + 1}", unit_name="CORVID",
            )
        )
        collection.append(created.asset_id)
    logger.info(f"   {COLLECTION_SIZE} NFTs minted by {artist.address[:8]}…")

    # A separate creator, so "minted by the collection" is a real check rather
    # than "is an asset".
    outsider_artist = algorand.account.random()
    fund(outsider_artist, 1_000_000)
    impostor = algorand.send.asset_create(
        algokit_utils.AssetCreateParams(
            sender=outsider_artist.address, total=1, decimals=0,
            asset_name="Not Corvid", unit_name="FAKE",
        )
    ).asset_id

    prize_asset = algorand.send.asset_create(
        algokit_utils.AssetCreateParams(
            sender=artist.address, total=PRIZE_SUPPLY, decimals=0,
            asset_name="Corvid Points", unit_name="CPT",
        )
    ).asset_id
    logger.info(f"   Prize asset {prize_asset}, impostor asset {impostor}")

    # ------------------------------------------------------------------
    logger.info("── 2. A draw gated to the collection, paying the token ──")
    beacon, _ = algorand.client.get_typed_app_factory(
        BeaconStubFactory, default_sender=founder.address
    ).send.create.bare()

    rain, _ = algorand.client.get_typed_app_factory(
        RainFactory, default_sender=founder.address
    ).send.create.bare()
    algorand.send.payment(
        algokit_utils.PaymentParams(
            sender=founder.address,
            receiver=rain.app_address,
            amount=algokit_utils.AlgoAmount(micro_algo=500_000),
        )
    )
    rain.send.configure(
        args=ConfigureArgs(
            beacon_app=beacon.app_id,
            gate_creator=artist.address,
            prize_asset=prize_asset,
        )
    )
    _assert("gate is the collection's minter", rain.state.global_state.gate_creator, artist.address)
    _assert("prize is the token", rain.state.global_state.prize_asset, prize_asset)

    # ------------------------------------------------------------------
    logger.info("── 3. Anyone can pay for the app to hold the token ──")
    stranger = algorand.account.random()
    fund(stranger, 1_000_000)
    rain.send.opt_in_prize_asset(
        args=OptInPrizeAssetArgs(
            mbr_payment=algorand.create_transaction.payment(
                algokit_utils.PaymentParams(
                    sender=stranger.address,
                    receiver=rain.app_address,
                    amount=algokit_utils.AlgoAmount(micro_algo=ASSET_OPT_IN_MBR),
                )
            )
        ),
        params=algokit_utils.CommonAppCallParams(
            sender=stranger.address,
            signer=stranger.signer,
            extra_fee=algokit_utils.AlgoAmount(micro_algo=1_000),
        ),
    )
    logger.info("   A passer-by paid the 100,000 µALGO opt-in, not the creator.")

    # ------------------------------------------------------------------
    logger.info("── 4. Anyone can fill the pot ──")
    algorand.send.asset_opt_in(
        algokit_utils.AssetOptInParams(sender=stranger.address, asset_id=prize_asset)
    )
    algorand.send.asset_transfer(
        algokit_utils.AssetTransferParams(
            sender=artist.address, receiver=stranger.address,
            asset_id=prize_asset, amount=POT,
        )
    )
    pot = RainClient(
        algorand=algorand, app_id=rain.app_id,
        default_sender=stranger.address, default_signer=stranger.signer,
    ).send.deposit_asset(
        args=DepositAssetArgs(
            transfer=algorand.create_transaction.asset_transfer(
                algokit_utils.AssetTransferParams(
                    sender=stranger.address, receiver=rain.app_address,
                    asset_id=prize_asset, amount=POT,
                )
            )
        )
    ).abi_return
    _assert("pot, in tokens", pot, POT)
    logger.info(f"   The same passer-by funded {POT} tokens. Not the creator either.")

    # ------------------------------------------------------------------
    logger.info("── 5. Holders enter; an outsider cannot ──")
    holders = []
    for index in range(COLLECTION_SIZE):
        holder = algorand.account.random()
        fund(holder, 1_000_000)
        algorand.send.asset_opt_in(
            algokit_utils.AssetOptInParams(sender=holder.address, asset_id=collection[index])
        )
        algorand.send.asset_transfer(
            algokit_utils.AssetTransferParams(
                sender=artist.address, receiver=holder.address,
                asset_id=collection[index], amount=1,
            )
        )
        client = RainClient(
            algorand=algorand, app_id=rain.app_id,
            default_sender=holder.address, default_signer=holder.signer,
        )
        ticket = client.send.enter(
            args=EnterArgs(
                mbr_payment=algorand.create_transaction.payment(
                    algokit_utils.PaymentParams(
                        sender=holder.address, receiver=rain.app_address,
                        amount=algokit_utils.AlgoAmount(micro_algo=TICKET_MBR),
                    )
                ),
                gate_asset=collection[index],
            )
        ).abi_return
        holders.append((holder, client))
        logger.info(f"   Holder of NFT #{index + 1} took ticket {ticket}")

    outsider = algorand.account.random()
    fund(outsider, 1_000_000)
    algorand.send.asset_opt_in(
        algokit_utils.AssetOptInParams(sender=outsider.address, asset_id=impostor)
    )
    algorand.send.asset_transfer(
        algokit_utils.AssetTransferParams(
            sender=outsider_artist.address, receiver=outsider.address,
            asset_id=impostor, amount=1,
        )
    )
    outsider_client = RainClient(
        algorand=algorand, app_id=rain.app_id,
        default_sender=outsider.address, default_signer=outsider.signer,
    )
    rejected = False
    with _quiet():
        try:
            outsider_client.send.enter(
                args=EnterArgs(
                    mbr_payment=algorand.create_transaction.payment(
                        algokit_utils.PaymentParams(
                            sender=outsider.address, receiver=rain.app_address,
                            amount=algokit_utils.AlgoAmount(micro_algo=TICKET_MBR),
                        )
                    ),
                    gate_asset=impostor,
                )
            )
        except Exception:
            rejected = True
    _assert("an NFT from elsewhere buys nothing", rejected, True)

    # ------------------------------------------------------------------
    logger.info("── 6. A keeper opens the draw, accounting only ──")
    call_data = _selector(DRAW_SIGNATURE)

    def payment(amount: int):
        return algorand.create_transaction.payment(
            algokit_utils.PaymentParams(
                sender=founder.address, receiver=keeper_client.app_address,
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
            # A missed draw is not replayed: nobody wants yesterday's draw today.
            policy=SKIP_AHEAD,
            fee_cap=0,
            fee_asset=0,
            asset_fee=0,
        )
    ).abi_return

    upkeep, _ = _read_upkeep(algorand, keeper_client.app_id, upkeep_id)
    net.wait_for_round(algorand, upkeep.next_execution_round, poker=founder)
    keeper_bot.main(["--once", "--network", args.network, "--app-id", str(keeper_client.app_id)])

    state = rain.state.global_state
    _assert("a draw is open", state.draw_open, 1)
    _assert("the prize is locked", state.prize, POT)
    # The counter moved from pot to prize. What matters is that no token did:
    # the scheduled call did accounting and nothing else.
    app_holding = algod.account_asset_info(rain.app_address, prize_asset)["asset-holding"]["amount"]
    _assert("the app still holds every token", app_holding, POT)
    commit_round = state.commit_round

    # ------------------------------------------------------------------
    logger.info("── 7. A holder resolves it, attaching the beacon ──")
    net.wait_for_round(algorand, commit_round + BEACON_DELAY, poker=founder)
    winner_address = holders[0][1].send.resolve(
        params=algokit_utils.CommonAppCallParams(
            extra_fee=algokit_utils.AlgoAmount(micro_algo=2_000)
        )
    ).abi_return
    expected = _expected_ticket(commit_round, COLLECTION_SIZE)
    logger.info(f"   Ticket {expected} of {COLLECTION_SIZE} won")

    # ------------------------------------------------------------------
    logger.info("── 8. The winner pulls their tokens ──")
    winner, winner_client = holders[expected]
    _assert("the contract picked the ticket we predicted", winner_address, winner.address)
    algorand.send.asset_opt_in(
        algokit_utils.AssetOptInParams(sender=winner.address, asset_id=prize_asset)
    )
    claimed = winner_client.send.claim(
        params=algokit_utils.CommonAppCallParams(
            extra_fee=algokit_utils.AlgoAmount(micro_algo=1_000)
        )
    ).abi_return
    held = algod.account_asset_info(winner.address, prize_asset)["asset-holding"]["amount"]
    _assert("the whole pot was won", claimed, POT)
    _assert("the winner holds them", held, claimed)

    logger.info("")
    logger.info("Community rain demo passed.")
    logger.info(f"  Rain app {rain.app_id}, keeper app {keeper_client.app_id}")
    logger.info(f"  Gated to {COLLECTION_SIZE} NFTs by one creator; {claimed} tokens paid out")
    logger.info("  The opt-in and the pot were both funded by somebody who is not the creator.")


if __name__ == "__main__":
    main()
