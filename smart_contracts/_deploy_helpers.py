"""Shared deployment steps, so there is one of each rather than one per contract.

Every app account must hold the base account minimum balance before it can
send anything. An app that books money above that line has promised what it
cannot pay: the inner payment drops the account below its own minimum and
reverts, after the contract has already recorded the obligation.

The keeper's deploy config funded this from the beginning. None of the example
contracts did, so the default AlgoKit path produced apps that worked until the
last party tried to leave. `deadman` had exactly that, it was found on a chain
rather than by reading, and the fix was applied there and not carried to the
siblings with the same shape. Three independent reviewers named that
carry-across failure as the dominant pattern in this repository, so this lives
in one place and is called from each config rather than pasted into each.
"""

import logging

import algokit_utils

logger = logging.getLogger(__name__)

# What every Algorand account must hold before it can send anything.
APP_BASE_MBR = 100_000


def fund_base_mbr(
    algorand: algokit_utils.AlgorandClient,
    app_address: str,
    deployer: algokit_utils.SigningAccount,
    *,
    label: str,
) -> None:
    """Give an app account its base minimum balance. Idempotent.

    Reads the balance first so a redeploy against an existing app does not
    send again, and so this is safe to call from any config unconditionally.
    """
    balance = algorand.client.algod.account_info(app_address)["amount"]
    if balance >= APP_BASE_MBR:
        return
    algorand.send.payment(
        algokit_utils.PaymentParams(
            amount=algokit_utils.AlgoAmount(micro_algo=APP_BASE_MBR - balance),
            sender=deployer.address,
            receiver=app_address,
        )
    )
    logger.info(f"Funded {label} app account with {APP_BASE_MBR} µALGO base MBR")
