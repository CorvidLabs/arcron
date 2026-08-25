"""Minimal example: register an upkeep on the keeper network.

Points the canonical TestNet keeper app at a method on YOUR app, escrowing
ALGO to pay keepers for executions. After `interval_rounds` pass, any keeper
bot (e.g. `poetry run python -m scripts.keeper_bot`) will execute it.

Your method must fit the v1 call shape: a NoOp ABI method taking exactly one
application argument — the standard "tick/settle/harvest" hook. No arg of its
own? Give it a dummy like `tick()uint64`; the selector alone is the call data.

Requires .env.testnet (ALGOD_SERVER etc. + DEPLOYER_MNEMONIC).
Run:  poetry run python -m examples.register_upkeep
"""

import hashlib
import logging

import algokit_utils
from dotenv import load_dotenv

load_dotenv(".env.testnet")

from smart_contracts.artifacts.keeper.keeper_client import (  # noqa: E402
    KeeperClient,
    RegisterArgs,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# --- Configure these -------------------------------------------------------
KEEPER_APP_ID = 769823086  # canonical TestNet keeper app (alpha-1)
TARGET_APP_ID = 769823097  # your app (Pulse demo target shown here)
METHOD_SIGNATURE = "tick()uint64"  # your method
INTERVAL_ROUNDS = 100  # execute at most every ~5 minutes
FEE_PER_EXECUTION = 4_000  # µALGO paid to the keeper per execution (min 4,000)
FUNDING = 40_000  # µALGO escrowed; here: 10 executions
# ----------------------------------------------------------------------------


def main() -> None:
    algorand = algokit_utils.AlgorandClient.from_environment()
    algorand.set_suggested_params_cache_timeout(0)  # public TestNet is slow
    deployer = algorand.account.from_environment("DEPLOYER")
    algod = algorand.client.algod

    client = KeeperClient(
        algorand=algorand,
        app_id=KEEPER_APP_ID,
        default_sender=deployer.address,
        default_signer=deployer.signer,
    )

    call_data = hashlib.new("sha512_256", METHOD_SIGNATURE.encode()).digest()[:4]
    # Box MBR: 9-byte name plus the 84-byte-plus-call-data encoded Upkeep.
    mbr = 2_500 + 400 * (93 + len(call_data))

    # Public TestNet endpoints can be slow enough that default validity
    # windows expire before simulate; pin an explicit, generous window.
    first_valid = algod.status()["last-round"]
    window = algokit_utils.CommonAppCallParams(
        first_valid_round=first_valid, last_valid_round=first_valid + 1_000
    )
    payment = lambda amount: algorand.create_transaction.payment(  # noqa: E731
        algokit_utils.PaymentParams(
            sender=deployer.address,
            receiver=client.app_address,
            amount=algokit_utils.AlgoAmount(micro_algo=amount),
            first_valid_round=first_valid,
            last_valid_round=first_valid + 1_000,
        )
    )

    response = client.send.register(
        args=RegisterArgs(
            mbr_payment=payment(mbr),
            funding_payment=payment(FUNDING),
            target_app=TARGET_APP_ID,
            call_data=call_data,
            interval_rounds=INTERVAL_ROUNDS,
            fee_per_execution=FEE_PER_EXECUTION,
        ),
        params=window,
    )
    logger.info(
        f"Registered upkeep {response.abi_return} on keeper app {KEEPER_APP_ID}; "
        f"due in ~{INTERVAL_ROUNDS} rounds, funded for "
        f"{FUNDING // FEE_PER_EXECUTION} executions"
    )


if __name__ == "__main__":
    main()
