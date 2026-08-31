"""Deploy the rain hub on TestNet and open the Corvid dogfood rains.

Creates the hub (or wraps `--app-id`), bootstraps it, opens a daily SPLIT
rain gated to the TestNet Corvid minter, and registers one Arcron upkeep on
`draw()uint64` at an hourly cadence (each rain still enforces its own
interval). Idempotent: already-done steps are skipped.

Run:  poetry run python -m scripts.rain_testnet_deploy --network testnet --keeper-app-id 769891898
"""

import argparse
import logging
import os

import algokit_utils

from scripts import network as net
from scripts.keeper_e2e import _box_mbr, _selector
from smart_contracts.artifacts.keeper.keeper_client import KeeperClient, RegisterArgs
from smart_contracts.artifacts.rain.rain_client import (
    BootstrapArgs,
    CreateRainArgs,
    DepositArgs,
    RainClient,
)
from smart_contracts.keeper.contract import SKIP_AHEAD
from smart_contracts.rain.contract import APP_BASE_MBR, RAIN_BOX_MBR, SPLIT, WAVE
from smart_contracts.rain.deploy_config import deploy as deploy_rain

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

CORVID_TESTNET_MINTER = "WGSHC4TYKYBS6EX5V5E377BQDLKWIIPBCFOLZQZIXCKHFIEKRPBFOMW25A"
DRAW_SIGNATURE = "draw()uint64"
DAILY_ROUNDS = 30_857
HOURLY_ROUNDS = 1_286
DRIP_MICROALGO = 50_000
POT_SEED_MICROALGO = 100_000
FEE_PER_EXECUTION = 4_000
EXECUTIONS_TO_FUND = 20
UPKEEP_FUNDING_MICROALGO = FEE_PER_EXECUTION * EXECUTIONS_TO_FUND


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


def resolve_keeper_app_id(
    parser: argparse.ArgumentParser, keeper_app_id: int | None, network: str
) -> int:
    """The keeper to register the draw upkeep on: --keeper-app-id, else ARCRON_KEEPER_APP_ID.

    Deliberately no default, matching keeper_bot.resolve_app_id: a hardcoded
    id silently breaks on LocalNet, and once this script lives in arcron-rain
    the constant would sit in a second repository that cannot correctly
    change it.
    """
    if keeper_app_id is not None:
        return keeper_app_id
    from_env = os.environ.get("ARCRON_KEEPER_APP_ID")
    if from_env:
        return int(from_env)
    parser.error(
        f"--keeper-app-id (or ARCRON_KEEPER_APP_ID) is required on {network}: "
        "there is no canonical Arcron deployment to default to"
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    net.add_network_argument(parser)
    parser.add_argument("--app-id", type=int, default=None, help="wrap an existing hub")
    parser.add_argument(
        "--keeper-app-id",
        type=int,
        default=None,
        help="Arcron keeper app id (default: ARCRON_KEEPER_APP_ID)",
    )
    args = parser.parse_args(argv)

    algorand = net.connect(args.network)
    deployer = algorand.account.from_environment("DEPLOYER")
    keeper_app_id = resolve_keeper_app_id(parser, args.keeper_app_id, args.network)

    logger.info("── Hub ──")
    rain: RainClient = deploy_rain(args.app_id)
    state = rain.state.global_state
    if state.bootstrapped == 0:
        rain.send.bootstrap(
            args=BootstrapArgs(
                mbr_payment=_payment(algorand, deployer.address, rain.app_address, APP_BASE_MBR)
            )
        )
        logger.info(f"  Bootstrapped hub {rain.app_id}")
    else:
        logger.info(f"  Hub {rain.app_id} already bootstrapped")

    next_id = int(rain.state.global_state.next_rain_id)
    if next_id == 0:
        split_id = rain.send.create_rain(
            args=CreateRainArgs(
                mbr_payment=_payment(algorand, deployer.address, rain.app_address, RAIN_BOX_MBR),
                label=_label("Corvid daily"),
                gate_creator=CORVID_TESTNET_MINTER,
                prize_asset=0,
                drip=DRIP_MICROALGO,
                interval_rounds=DAILY_ROUNDS,
                mode=SPLIT,
                wave_cap=0,
            )
        ).abi_return
        wave_id = rain.send.create_rain(
            args=CreateRainArgs(
                mbr_payment=_payment(algorand, deployer.address, rain.app_address, RAIN_BOX_MBR),
                label=_label("Corvid GM"),
                gate_creator=CORVID_TESTNET_MINTER,
                prize_asset=0,
                drip=DRIP_MICROALGO,
                interval_rounds=DAILY_ROUNDS,
                mode=WAVE,
                wave_cap=10,
            )
        ).abi_return
        logger.info(f"  Opened SPLIT rain {split_id} and WAVE rain {wave_id}")
        rain.send.deposit(
            args=DepositArgs(
                payment=_payment(
                    algorand, deployer.address, rain.app_address, POT_SEED_MICROALGO
                ),
                rain_id=split_id,
            )
        )
        logger.info(f"  Seeded SPLIT pot with {POT_SEED_MICROALGO} µALGO")
    else:
        logger.info(f"  Hub already has {next_id} rain(s); not opening more")
        split_id = 1

    logger.info("── Upkeep ──")
    keeper = KeeperClient(
        algorand=algorand,
        app_id=keeper_app_id,
        default_sender=deployer.address,
        default_signer=deployer.signer,
    )
    # Register a fresh hourly upkeep if this hub has none we can see from
    # this run. The caller records the id in js/src/rain.ts.
    registered = keeper.send.register(
        args=RegisterArgs(
            mbr_payment=_payment(
                algorand, deployer.address, keeper.app_address, _box_mbr([_selector(DRAW_SIGNATURE)])
            ),
            funding_payment=_payment(
                algorand, deployer.address, keeper.app_address, UPKEEP_FUNDING_MICROALGO
            ),
            target_app=rain.app_id,
            call_args=[_selector(DRAW_SIGNATURE)],
            interval_rounds=HOURLY_ROUNDS,
            fee_per_execution=FEE_PER_EXECUTION,
            policy=SKIP_AHEAD,
            fee_cap=0,
            fee_asset=0,
            asset_fee=0,
        )
    )
    logger.info(f"  Registered upkeep {registered.abi_return} → draw() on hub {rain.app_id}")
    logger.info("Record in js/src/rain.ts TESTNET_RAIN:")
    logger.info(f"  appId: {rain.app_id}")
    logger.info(f"  upkeepId: {registered.abi_return}")
    logger.info(f"  Corvid SPLIT rain id: {split_id}")


if __name__ == "__main__":
    main()
