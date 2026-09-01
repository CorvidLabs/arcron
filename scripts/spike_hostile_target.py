"""Spike: what a hostile target can see, what it can cost, and what it can buy.

Arcron's `execute` hands control to somebody else's application. Three things
follow from that, and the 2026-09-01 audit established all three by reading
the compiled TEAL. Reading is not measuring, so this runs them.

  1. **A bracketed target cannot tell.** `execute` puts no constraint on the
     group it arrives in, so a keeper may bracket the call it is paid to make
     with transactions of its own. The inner call is its own group, so the
     target sees `group_size` 1 however large the outer group was, and cannot
     identify the keeper either. Any defence has to live in the target's own
     state. This is the sandwich in `docs/design/buyback.md`.

  2. **A failing target costs the keeper nothing.** A target that exhausts the
     opcode budget or simply refuses takes the whole group down with it. A
     rejected transaction is not in the ledger and carries no fee, so the
     keeper is out nothing but its own time, and the upkeep's box is
     untouched. If this were false, publishing a hostile target would be a way
     to drain every keeper watching the registry.

  3. **Lateness can be bought.** This one the audit did not test, and it is
     the reason this file exists. Fee escalation pays more when an upkeep is
     late, on the premise that lateness means no keeper wanted the job at the
     base price. The premise fails when the *target* can be made to refuse:
     anything conditional on state a third party can move — an oracle that
     rejects a stale update, a rebalancer that runs once an epoch — can be
     shut for one application call, and whoever shuts it collects the
     escalated fee when it reopens. Measured here at roughly twenty times the
     attacker's outlay.

Nothing here is a contract bug. Every µALGO moves within the cap the upkeep's
creator chose, and the registry recovers on its own once the attacker stops.
What it changes is the advice: an upkeep whose target can be blocked should
not enable escalation, because the market escalation exists to clear can be
closed by the person being paid.

Run:  poetry run python -m scripts.spike_hostile_target [--network localnet]
"""

import argparse
import logging

import algokit_utils
from algosdk import abi, transaction

from scripts import network as net
from scripts.keeper_e2e import _box_mbr, _read_upkeep, _selector
from smart_contracts.artifacts.keeper.keeper_client import CancelArgs, RegisterArgs
from smart_contracts.artifacts.resource_probe.resource_probe_client import (
    ResourceProbeFactory,
    SetGapArgs,
)
from smart_contracts.keeper.deploy_config import deploy as deploy_keeper

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

BASE_FEE = 4_000
#: Ten times the base, so one interval of lateness is unmistakable in the
#: numbers rather than something that has to be argued from a percentage.
FEE_CAP = 40_000
INTERVAL = 10
#: Shorter than the interval, so an honest keeper executing on schedule is
#: never blocked by the guard and the only lateness measured is bought.
GUARD_GAP = 6
CATCH_UP = 0
#: The keeper's own call, Arcron's inner call, and the payment back.
EXECUTE_FEE = 3_000


def _payment(algorand, sender, receiver: str, amount: int):
    first = algorand.client.algod.status()["last-round"]
    return algorand.create_transaction.payment(
        algokit_utils.PaymentParams(
            sender=sender.address,
            receiver=receiver,
            amount=algokit_utils.AlgoAmount(micro_algo=amount),
            first_valid_round=first,
            last_valid_round=first + 1_000,
        )
    )


def _register(algorand, keeper, creator, target_app: int, signature: str,
              funding: int, fee_cap: int = 0) -> int:
    call_args = [_selector(signature)]
    return keeper.send.register(
        args=RegisterArgs(
            mbr_payment=_payment(algorand, creator, keeper.app_address, _box_mbr(call_args)),
            funding_payment=_payment(algorand, creator, keeper.app_address, funding),
            target_app=target_app,
            call_args=call_args,
            interval_rounds=INTERVAL,
            fee_per_execution=BASE_FEE,
            policy=CATCH_UP,
            fee_cap=fee_cap,
            fee_asset=0,
            asset_fee=0,
        ),
        params=algokit_utils.CommonAppCallParams(
            sender=creator.address, signer=creator.signer
        ),
    ).abi_return


def _execute_txn(algod, app_id: int, sender: str, upkeep_id: int, target_app: int,
                 note: bytes = b""):
    method = abi.Method.from_signature("execute(uint64)uint64")
    params = algod.suggested_params()
    params.flat_fee = True
    params.fee = EXECUTE_FEE
    return transaction.ApplicationNoOpTxn(
        sender=sender,
        sp=params,
        index=app_id,
        app_args=[method.get_selector(), upkeep_id.to_bytes(8, "big")],
        boxes=[(0, b"u" + upkeep_id.to_bytes(8, "big"))],
        foreign_apps=[target_app],
        note=note or None,
    )


def _send(algod, txns, account) -> bool:
    """Submit a group. Returns whether it was accepted; a rejection is a result."""
    if len(txns) > 1:
        transaction.assign_group_id(txns)
    signed = account.signer.sign_transactions(txns, list(range(len(txns))))
    try:
        txid = algod.send_transactions(signed)
    except Exception as exc:  # noqa: BLE001 - the refusal is what is being measured
        logger.debug(f"refused: {exc}")
        return False
    transaction.wait_for_confirmation(algod, txid, 6)
    return True


def _balance(algorand, address: str) -> int:
    return int(algorand.client.algod.account_info(address)["amount"])


def _wait_until_due(algorand, keeper, upkeep_id: int, poker) -> int:
    upkeep, _ = _read_upkeep(algorand, keeper.app_id, upkeep_id)
    net.wait_for_round(algorand, upkeep.next_execution_round, poker=poker)
    return upkeep.next_execution_round


def _cancel(keeper, creator, upkeep_id: int) -> None:
    keeper.send.cancel(
        args=CancelArgs(upkeep_id=upkeep_id),
        params=algokit_utils.CommonAppCallParams(
            sender=creator.address,
            signer=creator.signer,
            extra_fee=algokit_utils.AlgoAmount(micro_algo=1_000),
        ),
    )


def measure_bracket(algorand, keeper, probe, creator, attacker) -> list[str]:
    """A keeper brackets its own execution. Can the target tell?"""
    escapes: list[str] = []
    algod = algorand.client.algod
    upkeep_id = _register(algorand, keeper, creator, probe.app_id,
                          "report_group()uint64", BASE_FEE * 10)
    _wait_until_due(algorand, keeper, upkeep_id, creator)

    params = algod.suggested_params()
    group = [
        transaction.PaymentTxn(attacker.address, params, attacker.address, 0, note=b"before"),
        _execute_txn(algod, keeper.app_id, attacker.address, upkeep_id, probe.app_id),
        transaction.PaymentTxn(attacker.address, params, attacker.address, 0, note=b"after"),
    ]
    assert _send(algod, group, attacker), "the bracketed execution should be accepted"

    seen = probe.state.global_state.last_reading
    logger.info(f"  outer group of 3, target saw group_size {seen}")
    if seen != 1:
        escapes.append(
            f"a bracketed target saw group_size {seen}; if a target can see the outer "
            "group, docs/design/buyback.md is wrong about where the defence has to live"
        )
    _cancel(keeper, creator, upkeep_id)
    return escapes


def measure_failing_targets(algorand, keeper, probe, creator, attacker) -> list[str]:
    """A target that cannot or will not run. Who pays for the attempt?"""
    escapes: list[str] = []
    algod = algorand.client.algod
    for signature, why in (("exhaust_budget()uint64", "too expensive to call"),
                           ("refuse()void", "unwilling to be called")):
        upkeep_id = _register(algorand, keeper, creator, probe.app_id, signature, BASE_FEE * 10)
        _wait_until_due(algorand, keeper, upkeep_id, creator)
        before_balance = _balance(algorand, attacker.address)
        before, _ = _read_upkeep(algorand, keeper.app_id, upkeep_id)

        accepted = _send(algod, [_execute_txn(algod, keeper.app_id, attacker.address,
                                              upkeep_id, probe.app_id)], attacker)
        after, _ = _read_upkeep(algorand, keeper.app_id, upkeep_id)
        cost = before_balance - _balance(algorand, attacker.address)
        logger.info(f"  target {why}: execution {'accepted' if accepted else 'refused'}, "
                    f"keeper out {cost} uALGO, escrow {before.balance} -> {after.balance}")
        if accepted:
            escapes.append(f"a target {why} was executed successfully")
        if cost != 0:
            escapes.append(
                f"a failed execution against a target {why} cost the keeper {cost} uALGO; "
                "a hostile target would be a way to drain every keeper watching"
            )
        if after.balance != before.balance or after.times_executed != before.times_executed:
            escapes.append(f"a failed execution against a target {why} moved the box")
        _cancel(keeper, creator, upkeep_id)
    return escapes


def measure_bought_lateness(algorand, keeper, probe, creator, attacker, honest) -> list[str]:
    """Shut the target for one application call, then collect the escalated fee."""
    escapes: list[str] = []
    algod = algorand.client.algod
    probe.send.set_gap(args=SetGapArgs(gap=GUARD_GAP))
    upkeep_id = _register(algorand, keeper, creator, probe.app_id, "guarded()uint64",
                          FEE_CAP * 10, fee_cap=FEE_CAP)
    logger.info(f"  upkeep {upkeep_id}: base {BASE_FEE}, cap {FEE_CAP}, interval {INTERVAL}; "
                f"the target refuses two calls inside {GUARD_GAP} rounds")

    def execute(who) -> int | None:
        """Execute if it will go through; returns what the creator paid."""
        before, _ = _read_upkeep(algorand, keeper.app_id, upkeep_id)
        if not _send(algod, [_execute_txn(algod, keeper.app_id, who.address, upkeep_id,
                                          probe.app_id)], who):
            return None
        after, _ = _read_upkeep(algorand, keeper.app_id, upkeep_id)
        return before.balance - after.balance

    # One honest cycle first, so the baseline is measured rather than assumed.
    _wait_until_due(algorand, keeper, upkeep_id, creator)
    honest_paid = execute(honest)
    if honest_paid is None:
        escapes.append("an honest keeper could not execute a healthy upkeep at all")
        _cancel(keeper, creator, upkeep_id)
        return escapes
    logger.info(f"  honest keeper, on schedule: creator paid {honest_paid}")

    # Now the attacker shuts the window over the round the upkeep comes due.
    # One call, at the ordinary transaction fee.
    _wait_until_due(algorand, keeper, upkeep_id, creator)
    spent_before = _balance(algorand, attacker.address)
    probe.send.guarded(params=algokit_utils.CommonAppCallParams(
        sender=attacker.address, signer=attacker.signer))

    honest_before = _balance(algorand, honest.address)
    blocked = execute(honest)
    honest_cost = honest_before - _balance(algorand, honest.address)
    if blocked is not None:
        logger.info("  the guard did not hold; nothing was bought")
        _cancel(keeper, creator, upkeep_id)
        return escapes
    logger.info(f"  honest keeper blocked, and it cost them {honest_cost} uALGO")

    # And waits for the window it closed to reopen.
    paid = None
    for _ in range(GUARD_GAP + INTERVAL):
        paid = execute(attacker)
        if paid is not None:
            break
        net.wait_for_round(algorand, algod.status()["last-round"] + 1, poker=creator)
    net_gain = _balance(algorand, attacker.address) - spent_before
    if paid is None:
        logger.info("  the attacker could not get through either")
        _cancel(keeper, creator, upkeep_id)
        return escapes

    logger.info(f"  attacker executed the upkeep it delayed: creator paid {paid}, "
                f"attacker up {net_gain} uALGO on the episode")
    if paid <= honest_paid:
        escapes.append(
            f"buying lateness paid {paid} against an honest {honest_paid}; the premise "
            "that escalation cannot be manufactured would then hold, and this spike's "
            "conclusion in docs/reviews/ is wrong"
        )
    if net_gain <= 0:
        escapes.append(f"the attack cost more than it returned ({net_gain} uALGO)")
    _cancel(keeper, creator, upkeep_id)
    return escapes


def measure_atomic_backlog(algorand, keeper, probe, creator, honest) -> list[str]:
    """One sender, one group, a backlog. How many executions and how many fees?

    The 2026-09-01 audit said a race produces one payment. That holds for an
    upkeep inside its own interval and not for one behind: under CATCH_UP each
    execution only advances the schedule by one interval, so a single group
    can drain several. Each replay pays base, which is the design; the claim
    was simply broader than the code.
    """
    algod = algorand.client.algod
    upkeep_id = _register(algorand, keeper, creator, probe.app_id,
                          "report_group()uint64", BASE_FEE * 50)
    due = _wait_until_due(algorand, keeper, upkeep_id, creator)
    net.wait_for_round(algorand, due + 3 * INTERVAL, poker=creator)

    before, _ = _read_upkeep(algorand, keeper.app_id, upkeep_id)
    group = [_execute_txn(algod, keeper.app_id, honest.address, upkeep_id, probe.app_id,
                          note=str(n).encode()) for n in range(3)]
    accepted = _send(algod, group, honest)
    after, _ = _read_upkeep(algorand, keeper.app_id, upkeep_id)
    runs = after.times_executed - before.times_executed
    drained = before.balance - after.balance
    logger.info(f"  three executions in one group: {'accepted' if accepted else 'refused'}, "
                f"{runs} execution(s), escrow -{drained}")
    _cancel(keeper, creator, upkeep_id)
    if drained > runs * BASE_FEE:
        return [f"{runs} replay(s) drained {drained}, more than {runs} base fees"]
    return []


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    net.add_network_argument(parser)
    args = parser.parse_args(argv)
    if args.network != net.LOCALNET:
        raise SystemExit(
            "This spike deliberately delays somebody's upkeep and blocks honest "
            "keepers. Run it on LocalNet, never against a registry other people use."
        )

    algorand = net.connect(args.network)
    deployer = algorand.account.from_environment("DEPLOYER")
    keeper = deploy_keeper()

    def account():
        who = algorand.account.random()
        algorand.account.ensure_funded(
            who, algorand.account.localnet_dispenser(), algokit_utils.AlgoAmount(algo=50)
        )
        return who

    creator, attacker, honest = account(), account(), account()
    # A fresh probe: `guarded` and `report_group` both carry state from the
    # last run, and a reused one would answer with it.
    probe = algorand.client.get_typed_app_factory(
        ResourceProbeFactory, default_sender=deployer.address
    ).send.create.bare()[0]

    escapes: list[str] = []
    logger.info("")
    logger.info("── can a bracketed target tell ──")
    escapes += measure_bracket(algorand, keeper, probe, creator, attacker)
    logger.info("")
    logger.info("── what a failing target costs the keeper ──")
    escapes += measure_failing_targets(algorand, keeper, probe, creator, attacker)
    logger.info("")
    logger.info("── what an upkeep's lateness costs to buy ──")
    escapes += measure_bought_lateness(algorand, keeper, probe, creator, attacker, honest)
    logger.info("")
    logger.info("── one group, one backlog, how many fees ──")
    escapes += measure_atomic_backlog(algorand, keeper, probe, creator, honest)

    logger.info("")
    if escapes:
        for escape in escapes:
            logger.error(f"  {escape}")
        raise SystemExit(
            "Something a target or a keeper did was not what docs/reviews/ says it does. "
            "The measurement is the authority, not the review."
        )
    logger.info("A target cannot see the bracket, cannot charge the keeper for failing,")
    logger.info("and can be used by a third party to buy the lateness escalation pays for.")


if __name__ == "__main__":
    main()
