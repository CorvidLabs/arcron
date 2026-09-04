"""Spike: what a hostile target can see, what it can cost, and what it can buy.

Arcron's `execute` hands control to somebody else's application. Three things
follow from that, and the 2026-09-01 audit established all three by reading
the compiled TEAL. Reading is not measuring, so this runs them.

  1. **A bracketed target cannot tell.** `execute` puts no constraint on the
     group it arrives in, so a keeper may bracket the call it is paid to make
     with transactions of its own. The inner call is its own group, so the
     target sees `group_size` 1 however large the outer group was, and cannot
     identify the keeper either. Any defence has to live in the target's own
     state. This is the sandwich: the target cannot see the outer group.

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

Nothing here is a contract bug: every µALGO moves within the cap the upkeep's
creator chose. What it changes is the advice. An upkeep whose target can be
blocked should not enable escalation, because the market escalation exists to
clear can be closed by the person being paid — and, as `measure_no_self_heal`
shows, a target whose cooldown is longer than the upkeep's interval closes it
permanently with nobody attacking anything.

**Every figure this prints is one run's.** LocalNet advances a round per
transaction, so which round an execution lands on moves between runs, and the
fee moves with it: `excess` is measured in rounds, so a run that lands two
rounds later pays a different escalated fee. What does not move is the
property each measurement asserts — blocked, shut out, emptied, above base —
and those are what the assertions below check. Quote the properties; treat
the numbers as a sample.

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
SKIP_AHEAD = 1
#: Longer than the interval, which is the configuration that never recovers.
LONG_GAP = 15
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
              funding: int, fee_cap: int = 0, policy: int = CATCH_UP) -> int:
    call_args = [_selector(signature)]
    return keeper.send.register(
        args=RegisterArgs(
            mbr_payment=_payment(algorand, creator, keeper.app_address, _box_mbr(call_args)),
            funding_payment=_payment(algorand, creator, keeper.app_address, funding),
            target_app=target_app,
            call_args=call_args,
            interval_rounds=INTERVAL,
            fee_per_execution=BASE_FEE,
            policy=policy,
            fee_cap=fee_cap,
            fee_asset=0,
            asset_fee=0,
        ),
        params=algokit_utils.CommonAppCallParams(
            sender=creator.address, signer=creator.signer
        ),
    ).abi_return


TOP_UP_SIGNATURE = "top_up(uint64,pay)uint64"


def _call(algod, app_id: int, sender: str, signature: str, args, *, boxes=None, fee=1_000):
    """One ABI method call, for the methods this spike needs beside `execute`."""
    params = algod.suggested_params()
    params.flat_fee = True
    params.fee = fee
    return transaction.ApplicationNoOpTxn(
        sender=sender, sp=params, index=app_id,
        app_args=[abi.Method.from_signature(signature).get_selector(), *args],
        boxes=boxes or [],
    )


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


#: What the AVM says when it rejects a group. Anything else that goes wrong is
#: infrastructure, and infrastructure must not read as a refusal: a dead node
#: would otherwise make every "the target could not be executed" measurement
#: below pass without a chain having answered anything.
REJECTION_MARKERS = ("logic eval error", "rejected by logic", "assert failed", "err opcode")


def _send(algod, txns, account) -> bool:
    """Submit a group. Returns whether it was accepted; a rejection is a result.

    Only an AVM rejection counts as False. A refused connection, a 403 from a
    node, a malformed group: those raise, because a spike that reads them as
    "the chain said no" is a spike that passes when nothing was measured.
    """
    if len(txns) > 1:
        transaction.assign_group_id(txns)
    signed = account.signer.sign_transactions(txns, list(range(len(txns))))
    try:
        txid = algod.send_transactions(signed)
    except Exception as exc:
        text = str(exc).lower()
        if not any(marker in text for marker in REJECTION_MARKERS):
            raise
        logger.debug(f"rejected by the AVM: {exc}")
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
            "group, the sandwich defence cannot live in the target"
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
        _cancel(keeper, creator, upkeep_id)
        return ["an honest keeper could not execute a healthy upkeep at all"]
    logger.info(f"  honest keeper, at the first due round (LocalNet lands it a round or "
                f"two late, so this is already above the {BASE_FEE} base): "
                f"creator paid {honest_paid}")

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
        _cancel(keeper, creator, upkeep_id)
        return [
            "the guard did not hold, so nothing was bought and nothing was measured. "
            "This used to log and return green, which is how a spike stops being "
            "evidence: the finding it exists to reproduce silently went unreproduced"
        ]
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
        _cancel(keeper, creator, upkeep_id)
        return ["the attacker never got through, so the attack was not measured this run"]

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


def measure_no_self_heal(algorand, keeper, probe, creator, honest) -> list[str]:
    """A cooldown longer than the interval, and nobody has to keep paying for it.

    The first version of this spike used a gap of 6 against an interval of 10,
    and on that setup the upkeep recovers: the attacker stops, the schedule
    re-phases, honest keepers are back on base within two cycles. The
    verification in docs/reviews/ wrote that down as a limit of the attack.
    It is a limit of that configuration.

    Point the same upkeep at a target whose cooldown is *longer* than the
    interval and the recovery never happens. Every successful execution
    re-arms the guard past the next due round, so the upkeep is late again by
    its own schedule, `due > last_serviced` stays true, and the fee stays
    escalated for as long as the escrow lasts. Under SKIP_AHEAD nobody has to
    send another blocking transaction: the upkeep blocks itself, and the
    keeper that happens to be waiting collects the difference every cycle.

    Which makes this the more likely shape in the wild, not the more exotic
    one: "an oracle that refuses a stale update, a rebalancer that runs once
    an epoch" is exactly a cooldown, and a creator who sets a cadence shorter
    than that epoch has built it by accident.
    """
    probe.send.set_gap(args=SetGapArgs(gap=LONG_GAP))
    upkeep_id = _register(algorand, keeper, creator, probe.app_id, "guarded()uint64",
                          FEE_CAP * 20, fee_cap=FEE_CAP, policy=SKIP_AHEAD)
    algod = algorand.client.algod
    fees: list[int] = []
    for _ in range(4):
        upkeep, _ = _read_upkeep(algorand, keeper.app_id, upkeep_id)
        net.wait_for_round(algorand, upkeep.next_execution_round, poker=creator)
        for _ in range(LONG_GAP + INTERVAL):
            before, _ = _read_upkeep(algorand, keeper.app_id, upkeep_id)
            if _send(algod, [_execute_txn(algod, keeper.app_id, honest.address, upkeep_id,
                                          probe.app_id)], honest):
                after, _ = _read_upkeep(algorand, keeper.app_id, upkeep_id)
                fees.append(before.balance - after.balance)
                break
            net.wait_for_round(algorand, algod.status()["last-round"] + 1, poker=creator)
    probe.send.set_gap(args=SetGapArgs(gap=GUARD_GAP))
    _cancel(keeper, creator, upkeep_id)

    logger.info(f"  cooldown {LONG_GAP} > interval {INTERVAL}, SKIP_AHEAD, no attacker at all: "
                f"fees {fees}")
    if len(fees) < 4:
        return ["the upkeep could not be executed four times, so nothing was measured"]
    # The document's claim is that the fee never comes back down, so *every*
    # cycle after the baseline has to stay above base. An earlier version
    # escaped only when all of them recovered, which would have passed on a
    # single escalated cycle followed by three at base — the opposite result
    # reported as a confirmation.
    recovered = [fee for fee in fees[1:] if fee <= BASE_FEE]
    if recovered:
        return [
            f"{len(recovered)} of {len(fees) - 1} cycles came back to base ({recovered}), "
            "so a cooldown longer than the interval does recover, and the correction "
            "in docs/reviews/ overstates it"
        ]
    return []


def measure_sibling_blocker(algorand, keeper, probe, creator, attacker, honest) -> list[str]:
    """Block with an upkeep of your own, and Arcron pays you to do it.

    The cheap version of buying lateness sends an ordinary application call
    for 1,000 uALGO. The cheaper version registers a second upkeep against the
    same target: its execution trips the guard on a schedule, the attacker
    executes it themselves so the base fee comes straight back, and no
    blocking transaction is ever sent by hand.

    It also walks through the defence `docs/integrating.md` recommends. A
    target told to `assert Txn.sender == keeper_app.address` refuses a raw
    call and accepts this one, because the inner sender is the keeper app
    either way, and a permissionless registry cannot stop anyone registering
    a second upkeep.
    """
    algod = algorand.client.algod
    victim = _register(algorand, keeper, creator, probe.app_id, "guarded()uint64",
                       FEE_CAP * 20, fee_cap=FEE_CAP)
    blocker = _register(algorand, keeper, attacker, probe.app_id, "guarded()uint64",
                        BASE_FEE * 20)
    logger.info(f"  victim upkeep {victim} (cap {FEE_CAP}), attacker's blocker {blocker} "
                f"(no cap), same target")

    def execute(who, uid):
        before, _ = _read_upkeep(algorand, keeper.app_id, uid)
        if not _send(algod, [_execute_txn(algod, keeper.app_id, who.address, uid,
                                          probe.app_id)], who):
            return None
        after, _ = _read_upkeep(algorand, keeper.app_id, uid)
        return before.balance - after.balance

    started = _balance(algorand, attacker.address)
    victim_start = _read_upkeep(algorand, keeper.app_id, victim)[0].balance
    excluded, paid_to_attacker = 0, []
    for _ in range(4):
        upkeep, _ = _read_upkeep(algorand, keeper.app_id, victim)
        net.wait_for_round(algorand, upkeep.next_execution_round, poker=creator)
        execute(attacker, blocker)          # trips the guard, and pays the attacker
        if execute(honest, victim) is not None:
            continue                        # an honest keeper got through this cycle
        excluded += 1
        for _ in range(GUARD_GAP + INTERVAL):
            took = execute(attacker, victim)
            if took is not None:
                paid_to_attacker.append(took)
                break
            net.wait_for_round(algorand, algod.status()["last-round"] + 1, poker=creator)

    victim_spent = victim_start - _read_upkeep(algorand, keeper.app_id, victim)[0].balance
    net_gain = _balance(algorand, attacker.address) - started
    logger.info(f"  honest keepers shut out of {excluded} of 4 cycles; victim spent "
                f"{victim_spent} where four base fees are {4 * BASE_FEE}")
    logger.info(f"  attacker collected {paid_to_attacker}, up {net_gain} uALGO net of its "
                f"own escrow float and every fee")
    for uid, who in ((victim, creator), (blocker, attacker)):
        _cancel(keeper, who, uid)

    if excluded == 0:
        return ["the sibling upkeep blocked nobody, so this variant was not measured"]
    # `net_gain` on its own is not enough: executing the blocker pays the
    # attacker its own base fee every cycle, so a run in which the victim was
    # never taken at all still finishes positive. The claim is that the
    # attacker collected an *escalated* fee from somebody else's upkeep, and
    # that is what has to be asserted.
    if not paid_to_attacker:
        return ["the attacker never executed the victim, so it collected nothing from it"]
    if max(paid_to_attacker) <= BASE_FEE:
        return [
            f"the most the attacker took from the victim was {max(paid_to_attacker)}, no "
            f"more than the {BASE_FEE} base fee, so nothing was extracted by blocking"
        ]
    if net_gain <= 0:
        return [f"blocking with an upkeep of your own lost {net_gain} uALGO, so it is not "
                "the cheaper variant docs/reviews/ says it is"]
    return []


def measure_escrow_bound(algorand, keeper, probe, creator, attacker, honest) -> list[str]:
    """Composed with the fallback decline, the ceiling is the escrow, not the cap.

    `execute` drops to the base fee when the escalated one is more than the
    upkeep holds, which keeps a starving upkeep executable. Buy the lateness
    on an upkeep in that state and the fallback is what you collect, which may
    not cover the block. Top the escrow up to the cap in the same group and
    you collect the cap instead, of which only the shortfall was yours: the
    upkeep is emptied in one execution, and the attacker's take is whatever it
    was still holding.

    So the per-episode bound the first draft of docs/reviews/ gave, `cap -
    base`, is the bound on the *plain* attack only. Kimi 3 found this
    composition during the branch review and proved it in a scratch file; it
    belongs here, asserted, rather than in a file nobody runs again.
    """
    algod = algorand.client.algod
    probe.send.set_gap(args=SetGapArgs(gap=GUARD_GAP))
    # Funded with exactly the cap and run once, so the escrow sits below the
    # escalated fee and the fallback is what an ordinary keeper would get.
    upkeep_id = _register(algorand, keeper, creator, probe.app_id, "guarded()uint64",
                          FEE_CAP, fee_cap=FEE_CAP)
    _wait_until_due(algorand, keeper, upkeep_id, creator)
    if not _send(algod, [_execute_txn(algod, keeper.app_id, honest.address, upkeep_id,
                                      probe.app_id)], honest):
        _cancel(keeper, creator, upkeep_id)
        return ["the first honest execution was refused, so nothing was measured"]

    _wait_until_due(algorand, keeper, upkeep_id, creator)
    net.wait_for_round(algorand, algod.status()["last-round"] + INTERVAL, poker=creator)
    held = _read_upkeep(algorand, keeper.app_id, upkeep_id)[0].balance
    if held >= FEE_CAP:
        _cancel(keeper, creator, upkeep_id)
        return [f"the escrow held {held}, at or above the cap {FEE_CAP}, so the fallback "
                "this composes with was never reachable and nothing was measured"]

    started = _balance(algorand, attacker.address)
    params = algod.suggested_params()
    shortfall = FEE_CAP - held
    group = [
        transaction.PaymentTxn(attacker.address, params, keeper.app_address, shortfall),
        _call(algod, keeper.app_id, attacker.address, TOP_UP_SIGNATURE,
              [upkeep_id.to_bytes(8, "big")], boxes=[(0, b"u" + upkeep_id.to_bytes(8, "big"))]),
        _execute_txn(algod, keeper.app_id, attacker.address, upkeep_id, probe.app_id),
    ]
    if not _send(algod, group, attacker):
        _cancel(keeper, creator, upkeep_id)
        return ["the top-up-and-execute group was refused, so the composition is not "
                "possible and docs/reviews/ overstates the bound"]

    left = _read_upkeep(algorand, keeper.app_id, upkeep_id)[0].balance
    net_gain = _balance(algorand, attacker.address) - started
    logger.info(f"  escrow held {held} under a cap of {FEE_CAP}: the fallback would have paid "
                f"{BASE_FEE}")
    logger.info(f"  topped up {shortfall} in the same group, took the cap: escrow -> {left}, "
                f"attacker up {net_gain} uALGO")
    _cancel(keeper, creator, upkeep_id)
    if left != 0:
        return [f"the escrow was not emptied ({left} left), so the bound is not the escrow"]
    if net_gain <= BASE_FEE:
        return [f"the composition cleared {net_gain}, no better than the {BASE_FEE} fallback"]
    return []


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
    if not accepted:
        return ["the three-execution group was refused, so the backlog claim went unmeasured"]
    if runs < 2:
        return [
            f"one group produced {runs} execution(s). The audit's claim that a race "
            "yields one payment would then hold, and the verification's correction of "
            "it in docs/reviews/ is wrong"
        ]
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
    logger.info("── a cooldown longer than the interval never recovers ──")
    escapes += measure_no_self_heal(algorand, keeper, probe, creator, honest)
    logger.info("")
    logger.info("── blocking with an upkeep of your own, which Arcron pays for ──")
    escapes += measure_sibling_blocker(algorand, keeper, probe, creator, attacker, honest)
    logger.info("")
    logger.info("── composed with the fallback decline, the bound is the escrow ──")
    escapes += measure_escrow_bound(algorand, keeper, probe, creator, attacker, honest)
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
    logger.info("A cooldown longer than the interval buys it for nobody in particular, for good.")


if __name__ == "__main__":
    main()
