"""Every attack any review ever found, run against a real chain.

Four independent security reviews produced a set of findings, each of which is
now fixed. A fix without a standing attack behind it is a claim, and this
repository has already shipped three claims that a later reviewer disproved.
So each finding gets a named attack here, and each one has to keep failing.

What belongs here rather than in `tests/`: anything the mocked unit suite
cannot decide. `algorand-python-testing` records inner transactions instead of
executing them and does not enforce minimum balances, so a whole class of bug
is invisible to it by construction. `deadman` shipped exactly such a bug and it
was found on a chain. Anything provable in mocks belongs in `tests/`, where it
runs on every commit; anything that needs real senders, real groups, real
minimum balances or real reverts belongs here.

Each attack states what it tries, who found it, and what must refuse it. An
attack that starts succeeding is a regression with a name attached.

Run:  poetry run python -m scripts.attacks --network localnet
"""

import argparse
import logging

import algokit_utils
from algosdk import transaction

from scripts import network as net
from scripts.keeper_e2e import _box_mbr, _encode_args, _quiet, _selector
from smart_contracts.artifacts.keeper.keeper_client import (
    CancelArgs,
    KeeperClient,
    RegisterArgs,
)
from smart_contracts.keeper.contract import MIN_INTERVAL_ROUNDS, MIN_UPKEEP_FEE
from smart_contracts.keeper.deploy_config import deploy as deploy_keeper
from smart_contracts.pulse.deploy_config import deploy as deploy_pulse

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

CALL_SIGNATURE = "tick()uint64"
FUNDING = MIN_UPKEEP_FEE * 4


class Refused(Exception):
    """The chain rejected the attack, which is the outcome each one wants."""


def _attempt(label: str, found_by: str, expect: str, call) -> str | None:
    """Run one attack. Returns a failure description, or None if properly refused.

    `expect` is the guard that must do the refusing. Checking it is the whole
    point: three of the four attacks here were originally refused for reasons
    that had nothing to do with what they were testing, and one of them passed
    with the guard it exists to test removed from the contract. A refusal is
    not evidence unless it is the right refusal.
    """
    try:
        with _quiet():
            call()
    except Exception as exc:
        message = str(exc).replace("\n", " ")
        if expect.lower() not in message.lower():
            logger.error(f"  ✘ WRONG REASON: {label}")
            logger.error(f"      expected the refusal to name: {expect}")
            logger.error(f"      got: {message[:300]}")
            return f"{label}: refused, but not by {expect!r}"
        logger.info(f"  ✔ refused by {expect!r}: {label}")
        return None
    logger.error(f"  ✘ ACCEPTED: {label}")
    logger.error(f"      found by {found_by}; this is a regression, not a new finding")
    return f"{label} (found by {found_by})"


def _signed_payment(algorand, sender, receiver: str, amount: int, first_valid: int, **extra):
    """A payment carrying its own signer, so a group can mix authorisers.

    Without this the typed client signs every transaction in the group with
    its own default signer, and the network rejects the group for a signature
    mismatch before any contract logic runs. An attack that is refused for
    the wrong reason proves nothing, and this one was: it passed with the
    guard it exists to test removed.
    """
    from algosdk.atomic_transaction_composer import TransactionWithSigner

    return TransactionWithSigner(
        _payment(algorand, sender, receiver, amount, first_valid, **extra),
        sender.signer,
    )


def _payment(algorand, sender, receiver: str, amount: int, first_valid: int, **extra):
    return algorand.create_transaction.payment(
        algokit_utils.PaymentParams(
            sender=sender.address,
            receiver=receiver,
            amount=algokit_utils.AlgoAmount(micro_algo=amount),
            first_valid_round=first_valid,
            last_valid_round=first_valid + 1_000,
            **extra,
        )
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    net.add_network_argument(parser)
    args = parser.parse_args(argv)

    algorand = net.connect(args.network)
    algod = algorand.client.algod
    deployer = algorand.account.from_environment("DEPLOYER")
    keeper = deploy_keeper()
    pulse = deploy_pulse()

    # A second funded account, so an attack can be signed by somebody who is
    # not the one paying. That distinction is the whole of attack 1 and it is
    # not expressible in the mocked suite at all.
    stranger = algorand.account.random()
    for account in (stranger,):
        algorand.send.payment(
            algokit_utils.PaymentParams(
                sender=deployer.address,
                receiver=account.address,
                amount=algokit_utils.AlgoAmount(micro_algo=5_000_000),
            )
        )

    call_args = [_selector(CALL_SIGNATURE)]
    mbr = _box_mbr(call_args)
    failures: list[str] = []

    logger.info("")
    logger.info("── keeper: who pays and who owns ──")

    # Fable 5 proved this on a chain: `register` checked the receiver, the
    # rekey, the close and the amount of each payment, and never the sender.
    # The creator is the app call's sender and `cancel` pays the creator, so a
    # victim could fund an upkeep an attacker owned. Every check anyone is
    # taught to make passed, because the receiver and app id really were right.
    def victim_funds_attackers_upkeep() -> None:
        first_valid = algod.status()["last-round"]
        attacker_client = KeeperClient(
            algorand=algorand, app_id=keeper.app_id,
            default_sender=stranger.address, default_signer=stranger.signer,
        )
        attacker_client.send.register(
            args=RegisterArgs(
                # Both legs paid by the deployer; the app call signed by the
                # stranger, who would own the upkeep and could cancel it.
                mbr_payment=_signed_payment(
                    algorand, deployer, keeper.app_address, mbr, first_valid
                ),
                funding_payment=_signed_payment(
                    algorand, deployer, keeper.app_address, FUNDING, first_valid
                ),
                target_app=pulse.app_id, call_args=call_args,
                interval_rounds=MIN_INTERVAL_ROUNDS, fee_per_execution=MIN_UPKEEP_FEE,
                policy=0, fee_cap=0, fee_asset=0, asset_fee=0,
            )
        )

    failures.append(
        _attempt(
            "a victim's payments funding an attacker's upkeep",
            "Fable 5, on chain",
            "must come from the caller",
            victim_funds_attackers_upkeep,
        )
    )

    # Not here: the rekey and close asserts on register's payment legs.
    #
    # They are real and they are tested, in tests/test_keeper.py, where 37
    # cases cover every gtxn argument every contract accepts. What I could not
    # do was make them fail *here* for their own reason: a group whose payment
    # carries a poisoned field is rejected by the network for the poisoning
    # before the contract's assert is reached, so the refusal names a
    # signature or a balance rather than the guard.
    #
    # A refusal for the wrong reason is worse than no test, because it reads
    # as evidence. Three of the four attacks in the first version of this file
    # were exactly that, and one of them passed with the guard it existed to
    # test deleted from the contract. So these are left out rather than left
    # in and believed.

    logger.info("")
    logger.info("── keeper: whose escrow is it ──")

    # Register one honestly, then try to take it back as somebody else.
    first_valid = algod.status()["last-round"]
    owner_client = KeeperClient(
        algorand=algorand, app_id=keeper.app_id,
        default_sender=deployer.address, default_signer=deployer.signer,
    )
    upkeep_id = owner_client.send.register(
        args=RegisterArgs(
            mbr_payment=_payment(algorand, deployer, keeper.app_address, mbr, first_valid),
            funding_payment=_payment(algorand, deployer, keeper.app_address, FUNDING, first_valid),
            target_app=pulse.app_id, call_args=call_args,
            interval_rounds=MIN_INTERVAL_ROUNDS, fee_per_execution=MIN_UPKEEP_FEE,
            policy=0, fee_cap=0, fee_asset=0, asset_fee=0,
        )
    ).abi_return

    def stranger_cancels() -> None:
        KeeperClient(
            algorand=algorand, app_id=keeper.app_id,
            default_sender=stranger.address, default_signer=stranger.signer,
        ).send.cancel(
            args=CancelArgs(upkeep_id=upkeep_id),
            params=algokit_utils.CommonAppCallParams(
                extra_fee=algokit_utils.AlgoAmount(micro_algo=1_000)
            ),
        )

    failures.append(
        _attempt(
            "a stranger cancelling somebody else's upkeep",
            "the original design",
            "Only the creator can cancel",
            stranger_cancels,
        )
    )

    # Tidy up so the run is repeatable on the same chain.
    owner_client.send.cancel(
        args=CancelArgs(upkeep_id=upkeep_id),
        params=algokit_utils.CommonAppCallParams(
            extra_fee=algokit_utils.AlgoAmount(micro_algo=1_000)
        ),
    )

    logger.info("")
    real = [f for f in failures if f is not None]
    if real:
        logger.error(f"{len(real)} attack(s) succeeded:")
        for failure in real:
            logger.error(f"  {failure}")
        raise SystemExit(
            "An attack that a review found and a fix closed is working again. "
            "Each of these has a name and a finder; start with the commit that "
            "touched the contract it targets."
        )
    logger.info(f"All {len(failures)} attacks refused on {args.network}.")
    logger.info("  Each one is a finding somebody had to make; none of them is theoretical.")


if __name__ == "__main__":
    main()
