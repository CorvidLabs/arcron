"""TestNet demo for the Keeper network.

Funds the deployer via the TestNet dispenser if needed, deploys Keeper and
Pulse, registers an upkeep against Pulse's `tick`, waits for the due round,
executes it (as a permissionless keeper), and verifies Pulse.beats
incremented on-chain.

Requires .env.testnet (ALGOD_SERVER etc. + DEPLOYER_MNEMONIC).
Run:  poetry run python -m scripts.keeper_testnet_demo
"""

import hashlib
import logging
import time

import algokit_utils
from dotenv import load_dotenv

load_dotenv(".env.testnet")

from smart_contracts.artifacts.keeper.keeper_client import (  # noqa: E402
    ExecuteArgs,
    RegisterArgs,
)
from smart_contracts.keeper.deploy_config import deploy as deploy_keeper  # noqa: E402
from smart_contracts.pulse.deploy_config import deploy as deploy_pulse  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

INTERVAL_ROUNDS = 10
FEE = 4_000
FUNDING = 20_000  # five executions
MIN_DEPLOYER_BALANCE = 1_500_000  # 1.5 ALGO


def _selector(signature: str) -> bytes:
    return hashlib.new("sha512_256", signature.encode()).digest()[:4]


def _wait_for_round(algod, target_round: int) -> None:
    while True:
        status = algod.status()
        current = status["last-round"]
        if current >= target_round:
            return
        logger.info(f"  round {current}, waiting for {target_round}…")
        algod.status_after_block(current + 1)


def main() -> None:
    algorand = algokit_utils.AlgorandClient.from_environment()
    # TestNet indexer-backed deploys are slow; never reuse stale
    # suggested params between transactions.
    algorand.set_suggested_params_cache_timeout(0)
    deployer = algorand.account.from_environment("DEPLOYER")
    algod = algorand.client.algod
    logger.info(f"Deployer: {deployer.address}")

    # Fund via the TestNet dispenser if the deployer is low.
    balance = algod.account_info(deployer.address)["amount"]
    logger.info(f"Deployer balance: {balance / 1e6} ALGO")
    if balance < MIN_DEPLOYER_BALANCE:
        logger.info("Funding deployer via TestNet dispenser…")
        algokit_utils.TestNetDispenserApiClient().fund(
            deployer.address, 2_000_000
        )
        balance = algod.account_info(deployer.address)["amount"]
        logger.info(f"New balance: {balance / 1e6} ALGO")

    # Deploy both apps (idempotent).
    keeper_client = deploy_keeper()
    pulse_client = deploy_pulse()
    logger.info(f"Keeper app {keeper_client.app_id}, Pulse app {pulse_client.app_id}")

    # Register an upkeep against Pulse.tick. Public TestNet endpoints can be
    # slow enough that default validity windows expire before simulate; pin
    # an explicit, generous window instead.
    first_valid = algod.status()["last-round"]
    last_valid = first_valid + 1_000
    call_data = _selector("tick()uint64")
    mbr = 2_500 + 400 * (91 + len(call_data))
    mbr_payment = algorand.create_transaction.payment(
        algokit_utils.PaymentParams(
            sender=deployer.address,
            receiver=keeper_client.app_address,
            amount=algokit_utils.AlgoAmount(micro_algo=mbr),
            first_valid_round=first_valid,
            last_valid_round=last_valid,
        )
    )
    funding_payment = algorand.create_transaction.payment(
        algokit_utils.PaymentParams(
            sender=deployer.address,
            receiver=keeper_client.app_address,
            amount=algokit_utils.AlgoAmount(micro_algo=FUNDING),
            first_valid_round=first_valid,
            last_valid_round=last_valid,
        )
    )
    response = keeper_client.send.register(
        args=RegisterArgs(
            mbr_payment=mbr_payment,
            funding_payment=funding_payment,
            target_app=pulse_client.app_id,
            call_data=call_data,
            interval_rounds=INTERVAL_ROUNDS,
            fee_per_execution=FEE,
        ),
        params=algokit_utils.CommonAppCallParams(
            first_valid_round=first_valid,
            last_valid_round=last_valid,
        ),
    )
    upkeep_id = response.abi_return
    confirmed = response.confirmation.get("confirmed-round") or algod.status()[
        "last-round"
    ]
    due_round = confirmed + INTERVAL_ROUNDS
    logger.info(f"Registered upkeep {upkeep_id} in round {confirmed}, due at {due_round}")

    # Wait for the due round, then execute as a permissionless keeper.
    _wait_for_round(algod, due_round)
    logger.info("Due round reached; executing upkeep…")
    response = keeper_client.send.execute(
        args=ExecuteArgs(upkeep_id=upkeep_id),
        # Cover the two inner transactions (app call + keeper payment).
        params=algokit_utils.CommonAppCallParams(
            extra_fee=algokit_utils.AlgoAmount(micro_algo=2_000)
        ),
    )
    logger.info(
        f"Executed upkeep {upkeep_id}; next due round {response.abi_return}"
    )

    # Verify the heartbeat on-chain.
    beats = pulse_client.state.global_state.beats
    assert beats and beats >= 1, f"Pulse.beats should be >= 1, got {beats}"
    last_round = pulse_client.state.global_state.last_beat_round
    logger.info(f"Pulse.beats = {beats} (last beat round {last_round})")

    logger.info("TestNet demo passed ✔")
    logger.info(
        f"Inspect: https://testnet.explorer.perawallet.app/application/{keeper_client.app_id}"
    )


if __name__ == "__main__":
    main()
