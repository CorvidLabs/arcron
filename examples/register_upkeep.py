"""Minimal example: register an upkeep on the keeper network.

Points the canonical TestNet keeper app at a method on YOUR app, escrowing
ALGO to pay keepers for executions. After `interval_rounds` pass, any keeper
bot (e.g. `poetry run python -m scripts.keeper_bot`) will execute it.

Your method is a NoOp ABI method. The simplest shape is a no-argument hook
like `tick()uint64`, where the selector is the whole call. A method taking
arguments works too: put the selector first and each ARC-4 encoded argument
after it, up to MAX_CALL_ARGS entries in total.

Requires .env.testnet (ALGOD_SERVER etc. + DEPLOYER_MNEMONIC).
Run:  poetry run python -m examples.register_upkeep
"""

import hashlib
import logging

import algokit_utils
from dotenv import load_dotenv

load_dotenv(".env.testnet")

from algosdk import abi  # noqa: E402

from smart_contracts.artifacts.keeper.keeper_client import (  # noqa: E402
    KeeperClient,
    RegisterArgs,
)
from smart_contracts.keeper.contract import (  # noqa: E402
    BOX_MBR_FIXED,
    MIN_UPKEEP_FEE,
    SKIP_AHEAD,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# --- Configure these -------------------------------------------------------
KEEPER_APP_ID = 769891898  # canonical TestNet keeper app (alpha-2)
TARGET_APP_ID = 769891902  # your app (Pulse demo target shown here)
METHOD_SIGNATURE = "tick()uint64"  # your method
INTERVAL_ROUNDS = 100  # execute at most every ~5 minutes
FEE_PER_EXECUTION = MIN_UPKEEP_FEE  # µALGO paid to the keeper per execution
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

    selector = hashlib.new("sha512_256", METHOD_SIGNATURE.encode()).digest()[:4]
    # The contract stores the whole argument list, so the box grows with it.
    # A no-argument hook is just the selector. Taking the constant from the
    # contract rather than restating it here is what keeps the two in step.
    call_args = [selector]
    encoded = abi.ABIType.from_string("byte[][]").encode([list(a) for a in call_args])
    mbr = BOX_MBR_FIXED + 400 * len(encoded)

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
            call_args=call_args,
            interval_rounds=INTERVAL_ROUNDS,
            fee_per_execution=FEE_PER_EXECUTION,
            # SKIP_AHEAD drops a missed backlog and keeps the cadence. CATCH_UP
            # replays every interval that was missed, one per call, which costs
            # a fee each time. Pick deliberately: it is the difference between
            # "run this hourly" and "never miss an hour".
            policy=SKIP_AHEAD,
            # No escalation: the fee never rises above fee_per_execution. Set a
            # ceiling above it to let a late upkeep bid more for attention.
            fee_cap=0,
            # ALGO only. A non-zero fee_asset adds an ASA bonus on top.
            fee_asset=0,
            asset_fee=0,
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
