"""Recurring billing where the schedule is not a server you have to keep up.

A provider prices a period. Subscribers deposit. A keeper advances the billing
period on a cadence, and settlement moves the money afterwards, in transactions
the interested party sends themselves.

The run below exercises the case that matters: a subscriber who runs out
mid-way. They pay for what they can afford, lapse, and everybody else carries
on being billed. Nothing about one subscriber's balance can wedge the schedule.

Run:  poetry run python -m scripts.subscription_demo [--network localnet]
"""

import argparse
import logging

import algokit_utils

from scripts import keeper_bot, network as net
from scripts.keeper_e2e import _assert, _box_mbr, _read_upkeep, _selector
from smart_contracts.artifacts.keeper.keeper_client import RegisterArgs
from smart_contracts.artifacts.subscription.subscription_client import (
    CreateArgs,
    SetKeeperArgs,
    SettleArgs,
    SubscribeArgs,
    SubscriptionFactory,
)
from smart_contracts.keeper.contract import CATCH_UP
from smart_contracts.keeper.deploy_config import deploy as deploy_keeper
from smart_contracts.subscription.contract import SUBSCRIBER_BOX_MBR

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

CHARGE_SIGNATURE = "charge()uint64"
UPKEEP_INTERVAL = 10
FEE = 4_000
PRICE_PER_PERIOD = 50_000
# Enough for three periods, plus the box. Ada is deliberately given less.
GENEROUS_DEPOSIT = SUBSCRIBER_BOX_MBR + PRICE_PER_PERIOD * 6
THIN_DEPOSIT = SUBSCRIBER_BOX_MBR + PRICE_PER_PERIOD * 2


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    net.add_network_argument(parser)
    args = parser.parse_args(argv)

    algorand = net.connect(args.network)
    founder = algorand.account.from_environment("DEPLOYER")
    algod = algorand.client.algod
    keeper_client = deploy_keeper()

    provider = algorand.account.random()
    grace = algorand.account.random()
    ada = algorand.account.random()
    for who, amount in ((provider, 1_000_000), (grace, 2_000_000), (ada, 2_000_000)):
        algorand.send.payment(
            algokit_utils.PaymentParams(
                sender=founder.address,
                receiver=who.address,
                amount=algokit_utils.AlgoAmount(micro_algo=amount),
            )
        )

    # ------------------------------------------------------------------
    logger.info("── 1. A provider prices a period ──")
    subscription, _ = algorand.client.get_typed_app_factory(
        SubscriptionFactory, default_sender=founder.address
    ).send.create.create(
        args=CreateArgs(
            provider=provider.address,
            price_per_period=PRICE_PER_PERIOD,
            # A period may not be billed faster than the upkeep's own cadence.
            min_rounds_per_period=UPKEEP_INTERVAL,
        )
    )
    algorand.send.payment(
        algokit_utils.PaymentParams(
            sender=founder.address,
            receiver=subscription.app_address,
            amount=algokit_utils.AlgoAmount(micro_algo=300_000),
        )
    )
    subscription.send.set_keeper(args=SetKeeperArgs(keeper_app=keeper_client.app_id))
    _assert("price per period", subscription.state.global_state.price_per_period, PRICE_PER_PERIOD)

    # ------------------------------------------------------------------
    logger.info("── 2. Two subscribers, funded differently ──")

    def subscribe(who, amount: int) -> None:
        subscription.send.subscribe(
            args=SubscribeArgs(
                deposit=algorand.create_transaction.payment(
                    algokit_utils.PaymentParams(
                        sender=who.address,
                        receiver=subscription.app_address,
                        amount=algokit_utils.AlgoAmount(micro_algo=amount),
                    )
                )
            ),
            params=algokit_utils.CommonAppCallParams(sender=who.address, signer=who.signer),
        )

    subscribe(grace, GENEROUS_DEPOSIT)
    subscribe(ada, THIN_DEPOSIT)
    logger.info(f"   Grace deposited {GENEROUS_DEPOSIT} µALGO, Ada {THIN_DEPOSIT}")

    # ------------------------------------------------------------------
    logger.info("── 3. The billing period is put on a schedule ──")
    call_data = _selector(CHARGE_SIGNATURE)

    def payment(amount: int):
        return algorand.create_transaction.payment(
            algokit_utils.PaymentParams(
                sender=founder.address,
                receiver=keeper_client.app_address,
                amount=algokit_utils.AlgoAmount(micro_algo=amount),
            )
        )

    upkeep_id = keeper_client.send.register(
        args=RegisterArgs(
            mbr_payment=payment(_box_mbr([call_data])),
            funding_payment=payment(FEE * 6),
            target_app=subscription.app_id,
            call_args=[call_data],
            interval_rounds=UPKEEP_INTERVAL,
            fee_per_execution=FEE,
            # Every period must be billed. Skipping one is revenue nobody collects.
            policy=CATCH_UP,
            fee_cap=0,
            fee_asset=0,
            asset_fee=0,
        )
    ).abi_return

    def run_keeper() -> None:
        upkeep, _ = _read_upkeep(algorand, keeper_client.app_id, upkeep_id)
        net.wait_for_round(algorand, upkeep.next_execution_round, poker=founder)
        keeper_bot.main(
            ["--once", "--network", args.network, "--app-id", str(keeper_client.app_id)]
        )

    # ------------------------------------------------------------------
    logger.info("── 4. Four periods pass, driven by a keeper ──")
    for _ in range(4):
        run_keeper()
    _assert("periods billed", subscription.state.global_state.period, 4)
    _assert("moved by the scheduled call", subscription.state.global_state.provider_accrued, 0)
    logger.info("   The hook advanced the period four times and moved nothing. That is the design.")

    # ------------------------------------------------------------------
    logger.info("── 5. Settlement, where the money actually moves ──")
    for who, name in ((grace, "Grace"), (ada, "Ada")):
        billed = subscription.send.settle(
            args=SettleArgs(subscriber=who.address),
            params=algokit_utils.CommonAppCallParams(
                sender=provider.address, signer=provider.signer
            ),
        ).abi_return
        logger.info(f"   {name} billed for {billed} of 4 periods")

    # Grace could afford all four. Ada could afford two, and stops there.
    _assert("accrued to the provider", subscription.state.global_state.provider_accrued,
            PRICE_PER_PERIOD * 6)

    # ------------------------------------------------------------------
    logger.info("── 6. A lapsed subscriber does not wedge anybody ──")
    run_keeper()
    _assert("periods billed", subscription.state.global_state.period, 5)
    logger.info("   Ada is out of funds and the schedule did not notice.")

    # ------------------------------------------------------------------
    logger.info("── 7. The provider collects ──")
    before = algod.account_info(provider.address)["amount"]
    claimed = subscription.send.claim(
        params=algokit_utils.CommonAppCallParams(
            sender=provider.address, signer=provider.signer, extra_fee=algokit_utils.AlgoAmount(micro_algo=1_000)
        ),
    ).abi_return
    after = algod.account_info(provider.address)["amount"]
    _assert("claimed", claimed, PRICE_PER_PERIOD * 6)
    # The claim pays two fees: its own, and the inner payment's, which the
    # contract submits with fee=0 so the caller covers it.
    _assert("provider is better off by the claim, less both fees", after - before, claimed - 2_000)

    # ------------------------------------------------------------------
    logger.info("── 8. A lapsed subscriber can still leave ──")
    # Requiring a full catch-up trapped exactly the people most likely to
    # want out: Ada cannot afford what she owes, so she could never satisfy
    # it, so her box MBR was stranded permanently.
    before = algod.account_info(ada.address)["amount"]
    refunded = subscription.send.withdraw(
        params=algokit_utils.CommonAppCallParams(
            sender=ada.address,
            signer=ada.signer,
            extra_fee=algokit_utils.AlgoAmount(micro_algo=1_000),
        )
    ).abi_return
    after = algod.account_info(ada.address)["amount"]
    _assert("Ada got her box minimum balance back", refunded, SUBSCRIBER_BOX_MBR)
    _assert("and it actually reached her", after - before, refunded - 2_000)

    logger.info("")
    logger.info("Subscription demo passed.")
    logger.info(f"  Subscription app {subscription.app_id}, keeper app {keeper_client.app_id}")
    logger.info(f"  {subscription.state.global_state.period} periods billed by a keeper nobody controls")


if __name__ == "__main__":
    main()
