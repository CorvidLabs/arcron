"""A timed release, end to end, driven by a real keeper.

Schedules content for a round in the near future, points an Archon upkeep at
`publish()`, and lets the keeper bot fire it. Along the way it demonstrates the
three properties that make this worth caring about, and the one it cannot have:

* a keeper arriving *before* the release round is refused, and pays nothing;
* at the release round, a keeper who is not the author publishes it;
* afterwards nobody — author included — can alter or re-publish it;
* the content was readable the whole time, because that is how public chains
  work. What is guaranteed is the publication event, not secrecy.

Run:  poetry run python -m scripts.embargo_demo [--network localnet]
"""

import argparse
import logging

import algokit_utils

from scripts import keeper_bot, network as net
from scripts.keeper_e2e import _assert, _box_mbr, _expect_failure, _read_upkeep, _selector, _quiet
from smart_contracts.artifacts.embargo.embargo_client import ScheduleArgs
from smart_contracts.artifacts.keeper.keeper_client import (
    CancelArgs,
    ExecuteArgs,
    RegisterArgs,
)
from smart_contracts.artifacts.embargo.embargo_client import EmbargoFactory
from smart_contracts.embargo.contract import BOX_MBR_FIXED, CONTENT_KEY
from smart_contracts.keeper.contract import SKIP_AHEAD
from smart_contracts.keeper.deploy_config import deploy as deploy_keeper

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

CONTENT = b"Embargoed until the release round: the board voted 7-2 to approve."
PUBLISH_SIGNATURE = "publish()uint64"
INTERVAL_ROUNDS = 10
FEE = 4_000
# Far enough out that the upkeep comes due once *before* the embargo lifts.
ROUNDS_UNTIL_RELEASE = 25


def _fresh_embargo(algorand, author):
    """A new instance per run: one scheduled release per app, by design.

    `schedule` is deliberately callable once, so reusing an app would mean
    reusing a spent embargo. That is the contract working, not a bug to route
    around.
    """
    factory = algorand.client.get_typed_app_factory(
        EmbargoFactory, default_sender=author.address
    )
    client, _ = factory.send.create.bare()
    algorand.send.payment(
        algokit_utils.PaymentParams(
            sender=author.address,
            receiver=client.app_address,
            # The app account holds the content box, so it needs its own MBR.
            amount=algokit_utils.AlgoAmount(micro_algo=100_000),
        )
    )
    return client


def _read_content(algorand, app_id: int) -> bytes:
    from scripts.keeper_bot import _as_bytes

    return _as_bytes(
        algorand.client.algod.application_box_by_name(app_id, CONTENT_KEY)["value"]
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    net.add_network_argument(parser)
    args = parser.parse_args(argv)

    algorand = net.connect(args.network)
    author = algorand.account.from_environment("DEPLOYER")
    algod = algorand.client.algod

    keeper_client = deploy_keeper()
    embargo_client = _fresh_embargo(algorand, author)
    logger.info(f"Fresh embargo app {embargo_client.app_id}")

    # ------------------------------------------------------------------
    logger.info("── 1. The author schedules a release ──")
    release_round = algod.status()["last-round"] + ROUNDS_UNTIL_RELEASE
    mbr = BOX_MBR_FIXED + 400 * len(CONTENT)
    embargo_client.send.schedule(
        args=ScheduleArgs(
            mbr_payment=algorand.create_transaction.payment(
                algokit_utils.PaymentParams(
                    sender=author.address,
                    receiver=embargo_client.app_address,
                    amount=algokit_utils.AlgoAmount(micro_algo=mbr),
                )
            ),
            content=CONTENT,
            release_round=release_round,
        )
    )
    _assert("release round", embargo_client.state.global_state.release_round, release_round)
    _assert("published yet", embargo_client.send.is_published().abi_return, False)
    logger.info(f"  Scheduled for round {release_round}, MBR {mbr} µALGO")

    # The honest caveat, demonstrated rather than footnoted.
    logger.info(
        f"  Content is already readable by anyone: {_read_content(algorand, embargo_client.app_id)[:40]!r}…"
    )

    # ------------------------------------------------------------------
    logger.info("── 2. An upkeep points a keeper at publish() ──")
    call_data = _selector(PUBLISH_SIGNATURE)

    def payment(amount: int):
        return algorand.create_transaction.payment(
            algokit_utils.PaymentParams(
                sender=author.address,
                receiver=keeper_client.app_address,
                amount=algokit_utils.AlgoAmount(micro_algo=amount),
            )
        )

    upkeep_id = keeper_client.send.register(
        args=RegisterArgs(
            mbr_payment=payment(_box_mbr(call_data)),
            funding_payment=payment(FEE * 3),
            target_app=embargo_client.app_id,
            call_data=call_data,
            interval_rounds=INTERVAL_ROUNDS,
            fee_per_execution=FEE,
            # Publication happens once; a backlog of missed checks buys nothing.
            policy=SKIP_AHEAD,
            fee_cap=0,
        )
    ).abi_return
    upkeep, _ = _read_upkeep(algorand, keeper_client.app_id, upkeep_id)
    logger.info(f"  Upkeep {upkeep_id} first due at round {upkeep.next_execution_round}")
    assert upkeep.next_execution_round < release_round, (
        "this demo needs the upkeep to come due before the embargo lifts"
    )

    # ------------------------------------------------------------------
    logger.info("── 3. A keeper arrives early and is refused ──")
    net.wait_for_round(algorand, upkeep.next_execution_round, poker=author)
    keeper_balance_before = algod.account_info(author.address)["amount"]
    # The assert's message ("Embargo has not lifted") does not survive the app
    # boundary: a failure inside an inner call to another app comes back as a
    # program counter, because the source map belongs to that app. So this
    # matches on the embargo app failing, and checks the state that matters.
    _expect_failure(
        "publish before the release round",
        f"app={embargo_client.app_id}",
        lambda: keeper_client.send.execute(
            args=ExecuteArgs(upkeep_id=upkeep_id),
            params=algokit_utils.CommonAppCallParams(
                extra_fee=algokit_utils.AlgoAmount(
                    micro_algo=keeper_bot.EXTRA_FEE_MICROALGO
                )
            ),
        ),
    )
    _assert(
        "the early keeper paid",
        keeper_balance_before - algod.account_info(author.address)["amount"],
        0,
    )
    _assert("published yet", embargo_client.send.is_published().abi_return, False)

    # ------------------------------------------------------------------
    logger.info(f"── 4. Waiting for the release round {release_round} ──")
    net.wait_for_round(algorand, release_round, poker=author)
    _assert("rounds remaining", embargo_client.send.rounds_remaining().abi_return, 0)

    logger.info("── 5. A keeper who is not the author publishes it ──")
    # The bot signs as KEEPER if configured, else DEPLOYER; either way this is
    # the ordinary permissionless path, not a privileged one.
    keeper_bot.main(
        ["--once", "--network", args.network, "--app-id", str(keeper_client.app_id)]
    )
    _assert("published", embargo_client.send.is_published().abi_return, True)
    published_round = embargo_client.state.global_state.published_round
    assert published_round >= release_round, "published before it was allowed"
    logger.info(f"  Published at round {published_round}")

    # ------------------------------------------------------------------
    logger.info("── 6. It cannot be undone ──")
    _expect_failure(
        "publish twice",
        "Already published",
        lambda: embargo_client.send.publish(),
    )
    _expect_failure(
        "reschedule after the fact",
        "Already scheduled",
        lambda: embargo_client.send.schedule(
            args=ScheduleArgs(
                mbr_payment=algorand.create_transaction.payment(
                    algokit_utils.PaymentParams(
                        sender=author.address,
                        receiver=embargo_client.app_address,
                        amount=algokit_utils.AlgoAmount(micro_algo=mbr),
                    )
                ),
                content=b"Actually, never mind.",
                release_round=algod.status()["last-round"] + 100,
            )
        ),
    )
    _assert("content unchanged", _read_content(algorand, embargo_client.app_id), CONTENT)

    # Leave no upkeep behind: it has done its one job.
    with _quiet():
        keeper_client.send.cancel(
            args=CancelArgs(upkeep_id=upkeep_id),
            params=algokit_utils.CommonAppCallParams(
                extra_fee=algokit_utils.AlgoAmount(micro_algo=1_000)
            ),
        )

    logger.info("")
    logger.info(f"Timed release demo passed on {args.network} ✔")
    logger.info(f"  Embargo app {embargo_client.app_id}, keeper app {keeper_client.app_id}")
    logger.info(f"  Scheduled for {release_round}, published at {published_round} by a keeper")
    logger.info("  The author could not publish early, delay it, or take it back.")


if __name__ == "__main__":
    main()
