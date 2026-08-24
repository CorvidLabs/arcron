"""End-to-end test for the Archon keeper network against a real node.

Runs on LocalNet by default and on TestNet with --network testnet. Unlike
the unit tests (algorand-python-testing mocks, where inner transactions are
recorded but never executed), this proves the behaviour that only a real AVM
can show:

  * the inner app call actually fires and the target app's state changes
  * the keeper is paid its fee from the escrow, atomically, in the same group
  * a *stranger* — not the upkeep's creator — can execute it (permissionless)
  * scheduling, top-ups, creator-only cancel, refunds
  * the not-due and insufficient-funding rejections
  * scripts/keeper_bot.py decodes real box bytes and executes a real upkeep

Run:  poetry run python -m scripts.keeper_e2e [--network localnet|testnet]
"""

import argparse
import contextlib
import hashlib
import logging

import algokit_utils
from algosdk import encoding

from scripts import keeper_bot, network as net
from smart_contracts.artifacts.keeper.keeper_client import (
    CancelArgs,
    ExecuteArgs,
    KeeperClient,
    KeeperFactory,
    RegisterArgs,
    TopUpArgs,
)
from smart_contracts.keeper.contract import BOX_MBR_FIXED
from smart_contracts.keeper.deploy_config import deploy as deploy_keeper
from smart_contracts.pulse.deploy_config import deploy as deploy_pulse

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

INTERVAL_ROUNDS = 10
FEE = 4_000
FUNDING = 12_000  # three executions
# Fee the keeper pays on the outer transaction: 1,000 µALGO base plus 2,000
# µALGO of extra fee covering the two inner transactions.
KEEPER_TXN_COST = 1_000 + keeper_bot.EXTRA_FEE_MICROALGO
MIN_DEPLOYER_BALANCE = 2_000_000
STRANGER_FUNDING = 500_000
CALL_SIGNATURE = "tick()uint64"
# Algorand's nominal block time, for reading a cadence as human time.
ROUND_SECONDS = 2.8
# Cadences a real user would pick, from a heartbeat to a daily settlement.
CADENCES = (
    ("30 seconds", 10),
    ("5 minutes", 107),
    ("1 hour", 1_286),
    ("1 day", 30_857),
)


def _selector(signature: str) -> bytes:
    return hashlib.new("sha512_256", signature.encode()).digest()[:4]


def _box_mbr(call_data: bytes) -> int:
    """What one upkeep box costs, per the contract's own constant."""
    return BOX_MBR_FIXED + 400 * len(call_data)


def _assert_solvent(algorand, keeper_client, app_id: int) -> None:
    """The app must be able to pay out every µALGO it has escrowed.

    Box MBR locks part of the app account, so escrow is only real if the
    spendable balance covers it. This is the invariant the undercharged MBR
    used to break — silently, until the last execution failed.
    """
    info = algorand.client.algod.account_info(keeper_client.app_address)
    spendable = info["amount"] - info["min-balance"]
    escrowed = sum(u.balance for u in keeper_bot.scan_upkeeps(algorand.client.algod, app_id))
    assert spendable >= escrowed, (
        f"app account is insolvent: {spendable} µALGO spendable but "
        f"{escrowed} µALGO escrowed"
    )
    logger.info(f"  ✔ solvent: {spendable} µALGO spendable ≥ {escrowed} µALGO escrowed")


def _balance(algorand, address: str) -> int:
    return algorand.client.algod.account_info(address)["amount"]


def _read_upkeep(algorand, app_id: int, upkeep_id: int):
    """Read one upkeep box, decoded with the keeper bot's own decoder."""
    name = b"u" + upkeep_id.to_bytes(8, "big")
    raw = keeper_bot._as_bytes(
        algorand.client.algod.application_box_by_name(app_id, name)["value"]
    )
    return keeper_bot._decode_upkeep(upkeep_id, raw), raw


def _box_exists(algorand, app_id: int, upkeep_id: int) -> bool:
    try:
        _read_upkeep(algorand, app_id, upkeep_id)
        return True
    except Exception:
        return False


@contextlib.contextmanager
def _quiet():
    """Mute algokit's own logging of a rejection we are deliberately causing."""
    previous = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(previous)


def _expect_failure(label: str, expected: str, call) -> None:
    """Assert `call` is rejected, with `expected` named in the error."""
    try:
        with _quiet():
            call()
    except Exception as exc:
        message = str(exc)
        assert expected.lower() in message.lower(), (
            f"{label}: expected {expected!r} in the rejection, got: {message}"
        )
        logger.info(f"  ✔ rejected: {expected}")
        return
    raise AssertionError(f"{label}: expected rejection ({expected}), but it succeeded")


def _assert(label: str, actual, expected) -> None:
    assert actual == expected, f"{label}: expected {expected}, got {actual}"
    logger.info(f"  ✔ {label} = {actual}")


def _fund_deployer(algorand, network: str, deployer) -> None:
    balance = _balance(algorand, deployer.address)
    logger.info(f"Deployer {deployer.address}: {balance / 1e6} ALGO")
    if balance >= MIN_DEPLOYER_BALANCE:
        return
    if network == net.LOCALNET:
        algorand.account.ensure_funded(
            deployer.address,
            algokit_utils.AlgoAmount(micro_algo=MIN_DEPLOYER_BALANCE),
            min_funding_increment=algokit_utils.AlgoAmount(algo=10),
        )
    else:
        logger.info("Funding deployer via the TestNet dispenser…")
        algokit_utils.TestNetDispenserApiClient().fund(deployer.address, 2_000_000)
    logger.info(f"Deployer balance now {_balance(algorand, deployer.address) / 1e6} ALGO")


def _register(algorand, keeper_client, deployer, target_app: int, funding: int) -> int:
    """Register an upkeep against Pulse.tick; returns the new upkeep id."""
    return _register_with_interval(
        algorand, keeper_client, deployer, target_app, funding, INTERVAL_ROUNDS
    )


def _register_with_interval(
    algorand,
    keeper_client,
    deployer,
    target_app: int,
    funding: int,
    interval: int,
    call_data: bytes | None = None,
) -> int:
    """Register an upkeep at an arbitrary cadence; returns the new upkeep id."""
    if call_data is None:
        call_data = _selector(CALL_SIGNATURE)
    first_valid = algorand.client.algod.status()["last-round"]
    last_valid = first_valid + 1_000

    def payment(amount: int):
        return algorand.create_transaction.payment(
            algokit_utils.PaymentParams(
                sender=deployer.address,
                receiver=keeper_client.app_address,
                amount=algokit_utils.AlgoAmount(micro_algo=amount),
                first_valid_round=first_valid,
                last_valid_round=last_valid,
            )
        )

    response = keeper_client.send.register(
        args=RegisterArgs(
            mbr_payment=payment(_box_mbr(call_data)),
            funding_payment=payment(funding),
            target_app=target_app,
            call_data=call_data,
            interval_rounds=interval,
            fee_per_execution=FEE,
        ),
        params=algokit_utils.CommonAppCallParams(
            first_valid_round=first_valid, last_valid_round=last_valid
        ),
    )
    return response.abi_return


def _human(rounds: int) -> str:
    seconds = rounds * ROUND_SECONDS
    if seconds < 90:
        return f"{seconds:.0f}s"
    if seconds < 3_600:
        return f"{seconds / 60:.0f}min"
    if seconds < 86_400:
        return f"{seconds / 3_600:.1f}h"
    return f"{seconds / 86_400:.1f}d"


def _first_line(message: str) -> str:
    return message.strip().splitlines()[0][:110]


def _assert_rejected_by_algod(rejection: str) -> None:
    """The rejection must come from the node, not from our own client.

    Without this, a local encoding mistake reads as "algod refused it" and the
    experiment quietly proves nothing.
    """
    marks = ("logic eval error", "rejected", "TransactionPool", "assert failed", "overspend")
    assert any(mark.lower() in rejection.lower() for mark in marks), (
        f"expected an algod rejection, got a client-side error: {_first_line(rejection)}"
    )


def _raw_execute(algorand, app_id: int, account, upkeep_id: int, target_app: int) -> str:
    """Broadcast `execute` straight to algod, with no simulate beforehand.

    The typed client simulates first, which means a doomed call never reaches
    the network — exactly what we need to bypass here. To learn what a losing
    keeper actually pays, the transaction has to be really broadcast.

    Returns the transaction id; raises whatever algod says on rejection.
    """
    from algosdk import abi, transaction

    method = abi.Method.from_signature("execute(uint64)uint64")
    params = algorand.client.algod.suggested_params()
    params.flat_fee = True
    params.fee = KEEPER_TXN_COST
    txn = transaction.ApplicationNoOpTxn(
        sender=account.address,
        sp=params,
        index=app_id,
        app_args=[method.get_selector(), upkeep_id.to_bytes(8, "big")],
        boxes=[(0, b"u" + upkeep_id.to_bytes(8, "big"))],
        foreign_apps=[target_app],
    )
    signed = account.signer.sign_transactions([txn], [0])
    # send_transactions encodes the signed objects; send_raw_transaction wants
    # bytes and would fail client-side, which would look like a rejection.
    return algorand.client.algod.send_transactions(signed)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    net.add_network_argument(parser)
    args = parser.parse_args(argv)
    network = args.network

    algorand = net.connect(network)
    deployer = algorand.account.from_environment("DEPLOYER")
    _fund_deployer(algorand, network, deployer)

    # ------------------------------------------------------------------
    logger.info("── 1. Deploy Keeper and Pulse ──")
    keeper_client = deploy_keeper()
    pulse_client = deploy_pulse()
    app_id = keeper_client.app_id
    logger.info(f"Keeper app {app_id}, Pulse app {pulse_client.app_id}")

    # A second account: the whole point of the network is that someone other
    # than the upkeep's creator can execute it and be paid for it.
    stranger = algorand.account.random()
    algorand.send.payment(
        algokit_utils.PaymentParams(
            sender=deployer.address,
            receiver=stranger.address,
            amount=algokit_utils.AlgoAmount(micro_algo=STRANGER_FUNDING),
        )
    )
    logger.info(f"Stranger keeper {stranger.address} funded with {STRANGER_FUNDING} µALGO")
    stranger_client = KeeperClient(
        algorand=algorand,
        app_id=app_id,
        default_sender=stranger.address,
        default_signer=stranger.signer,
    )

    # ------------------------------------------------------------------
    logger.info("── 2. Register an upkeep against Pulse.tick ──")
    escrow_before = _balance(algorand, keeper_client.app_address)
    upkeep_id = _register(algorand, keeper_client, deployer, pulse_client.app_id, FUNDING)
    registered_round = algorand.client.algod.status()["last-round"]
    upkeep, raw = _read_upkeep(algorand, app_id, upkeep_id)
    logger.info(f"Upkeep {upkeep_id} registered: {upkeep}")
    _assert("creator", encoding.encode_address(raw[:32]), deployer.address)
    _assert("target_app", upkeep.target_app, pulse_client.app_id)
    _assert("interval_rounds", upkeep.interval_rounds, INTERVAL_ROUNDS)
    _assert("fee_per_execution", upkeep.fee_per_execution, FEE)
    _assert("balance", upkeep.balance, FUNDING)
    _assert("times_executed", upkeep.times_executed, 0)
    _assert("call_data", raw[84:], _selector(CALL_SIGNATURE))
    assert upkeep.next_execution_round >= registered_round, "Upkeep is due immediately"
    _assert(
        "escrow credited",
        _balance(algorand, keeper_client.app_address) - escrow_before,
        FUNDING + _box_mbr(_selector(CALL_SIGNATURE)),
    )

    _assert_solvent(algorand, keeper_client, app_id)

    # ------------------------------------------------------------------
    logger.info("── 3. Executing before the due round is rejected ──")
    _expect_failure(
        "execute before due",
        "Not due",
        lambda: stranger_client.send.execute(
            args=ExecuteArgs(upkeep_id=upkeep_id),
            params=algokit_utils.CommonAppCallParams(
                extra_fee=algokit_utils.AlgoAmount(
                    micro_algo=keeper_bot.EXTRA_FEE_MICROALGO
                )
            ),
        ),
    )

    # ------------------------------------------------------------------
    logger.info(f"── 4. Wait for the due round {upkeep.next_execution_round} ──")
    net.wait_for_round(algorand, upkeep.next_execution_round, poker=deployer)

    logger.info("── 5. A stranger executes it and is paid from the escrow ──")
    beats_before = pulse_client.state.global_state.beats
    stranger_before = _balance(algorand, stranger.address)
    escrow_before = _balance(algorand, keeper_client.app_address)
    response = stranger_client.send.execute(
        args=ExecuteArgs(upkeep_id=upkeep_id),
        params=algokit_utils.CommonAppCallParams(
            extra_fee=algokit_utils.AlgoAmount(micro_algo=keeper_bot.EXTRA_FEE_MICROALGO)
        ),
    )
    executed_round = response.confirmation.get("confirmed-round")
    upkeep_after, _ = _read_upkeep(algorand, app_id, upkeep_id)

    # The inner app call really ran: Pulse's own state moved.
    _assert("Pulse.beats", pulse_client.state.global_state.beats, beats_before + 1)
    _assert("Pulse.last_beat_round", pulse_client.state.global_state.last_beat_round, executed_round)
    # The keeper was paid, net of the fees it laid out for the group.
    _assert(
        "stranger paid (net)",
        _balance(algorand, stranger.address) - stranger_before,
        FEE - KEEPER_TXN_COST,
    )
    _assert("escrow debited", escrow_before - _balance(algorand, keeper_client.app_address), FEE)
    _assert("balance", upkeep_after.balance, FUNDING - FEE)
    _assert("times_executed", upkeep_after.times_executed, 1)
    _assert(
        "next_execution_round",
        upkeep_after.next_execution_round,
        upkeep.next_execution_round + INTERVAL_ROUNDS,
    )
    _assert("returned next due round", response.abi_return, upkeep_after.next_execution_round)

    # ------------------------------------------------------------------
    logger.info("── 6. The keeper bot's decoder agrees with the chain ──")
    scanned = {u.upkeep_id: u for u in keeper_bot.scan_upkeeps(algorand.client.algod, app_id)}
    _assert("bot decoded upkeep", scanned[upkeep_id], upkeep_after)

    # ------------------------------------------------------------------
    logger.info("── 7. Anyone can top up an upkeep ──")
    top_up_amount = FEE * 2
    response = stranger_client.send.top_up(
        args=TopUpArgs(
            upkeep_id=upkeep_id,
            funding_payment=algorand.create_transaction.payment(
                algokit_utils.PaymentParams(
                    sender=stranger.address,
                    receiver=keeper_client.app_address,
                    amount=algokit_utils.AlgoAmount(micro_algo=top_up_amount),
                )
            ),
        )
    )
    _assert("balance after top-up", response.abi_return, FUNDING - FEE + top_up_amount)
    _assert_solvent(algorand, keeper_client, app_id)

    # ------------------------------------------------------------------
    logger.info("── 8. The keeper bot executes the next due run ──")
    net.wait_for_round(algorand, upkeep_after.next_execution_round, poker=deployer)
    beats_before = pulse_client.state.global_state.beats
    keeper_bot.main(["--once", "--network", network, "--app-id", str(app_id)])
    upkeep_after_bot, _ = _read_upkeep(algorand, app_id, upkeep_id)
    # The bot services every due upkeep on the app, not just this one — the
    # keeper app may be shared with upkeeps from other work — so this asserts
    # that ours ran, not that nothing else did.
    _assert("times_executed after bot run", upkeep_after_bot.times_executed, 2)
    assert pulse_client.state.global_state.beats > beats_before, (
        "the bot run should have moved Pulse at least once"
    )
    logger.info(
        f"  ✔ Pulse.beats after bot run = {pulse_client.state.global_state.beats} "
        f"(was {beats_before})"
    )

    # ------------------------------------------------------------------
    logger.info("── 9. Only the creator can cancel; the escrow comes back ──")
    _expect_failure(
        "cancel by a stranger",
        "Only the creator can cancel",
        lambda: stranger_client.send.cancel(args=CancelArgs(upkeep_id=upkeep_id)),
    )
    expected_refund = upkeep_after_bot.balance + _box_mbr(_selector(CALL_SIGNATURE))
    deployer_before = _balance(algorand, deployer.address)
    response = keeper_client.send.cancel(
        args=CancelArgs(upkeep_id=upkeep_id),
        params=algokit_utils.CommonAppCallParams(
            extra_fee=algokit_utils.AlgoAmount(micro_algo=1_000)  # the refund payment
        ),
    )
    _assert("refund returned by cancel", response.abi_return, expected_refund)
    _assert(
        "creator refunded escrow + box MBR (net of fees)",
        _balance(algorand, deployer.address) - deployer_before,
        expected_refund - 2_000,
    )
    _assert("upkeep box still on chain", _box_exists(algorand, app_id, upkeep_id), False)

    # ------------------------------------------------------------------
    logger.info("── 10. An exhausted upkeep is rejected, not executed ──")
    poor_id = _register(algorand, keeper_client, deployer, pulse_client.app_id, FEE)
    poor, _ = _read_upkeep(algorand, app_id, poor_id)
    net.wait_for_round(algorand, poor.next_execution_round, poker=deployer)
    stranger_client.send.execute(
        args=ExecuteArgs(upkeep_id=poor_id),
        params=algokit_utils.CommonAppCallParams(
            extra_fee=algokit_utils.AlgoAmount(micro_algo=keeper_bot.EXTRA_FEE_MICROALGO)
        ),
    )
    drained, _ = _read_upkeep(algorand, app_id, poor_id)
    _assert("balance drained", drained.balance, 0)
    net.wait_for_round(algorand, drained.next_execution_round, poker=deployer)
    _expect_failure(
        "execute with an empty escrow",
        "Insufficient funding",
        lambda: stranger_client.send.execute(
            args=ExecuteArgs(upkeep_id=poor_id),
            params=algokit_utils.CommonAppCallParams(
                extra_fee=algokit_utils.AlgoAmount(
                    micro_algo=keeper_bot.EXTRA_FEE_MICROALGO
                )
            ),
        ),
    )
    keeper_client.send.cancel(
        args=CancelArgs(upkeep_id=poor_id),
        params=algokit_utils.CommonAppCallParams(
            extra_fee=algokit_utils.AlgoAmount(micro_algo=1_000)
        ),
    )
    _assert("upkeep box still on chain", _box_exists(algorand, app_id, poor_id), False)

    # ------------------------------------------------------------------
    logger.info("── 11. A fresh app, funded with only its base MBR, stays solvent ──")
    # Regression: register() once undercharged box MBR by 800 µALGO, which no
    # mock can catch. A subsidised app account hides it; a brand new one, with
    # exactly the 0.1 ALGO the deploy config sends, does not.
    factory = algorand.client.get_typed_app_factory(
        KeeperFactory, default_sender=deployer.address
    )
    fresh, _ = factory.send.create.bare()
    algorand.send.payment(
        algokit_utils.PaymentParams(
            sender=deployer.address,
            receiver=fresh.app_address,
            amount=algokit_utils.AlgoAmount(micro_algo=100_000),
        )
    )
    lean_id = _register(algorand, fresh, deployer, pulse_client.app_id, FEE)
    _assert_solvent(algorand, fresh, fresh.app_id)
    lean, _ = _read_upkeep(algorand, fresh.app_id, lean_id)
    net.wait_for_round(algorand, lean.next_execution_round, poker=deployer)
    beats_before = pulse_client.state.global_state.beats
    stranger_before = _balance(algorand, stranger.address)
    fresh_stranger = KeeperClient(
        algorand=algorand,
        app_id=fresh.app_id,
        default_sender=stranger.address,
        default_signer=stranger.signer,
    )
    fresh_stranger.send.execute(
        args=ExecuteArgs(upkeep_id=lean_id),
        params=algokit_utils.CommonAppCallParams(
            extra_fee=algokit_utils.AlgoAmount(micro_algo=keeper_bot.EXTRA_FEE_MICROALGO)
        ),
    )
    _assert("Pulse.beats", pulse_client.state.global_state.beats, beats_before + 1)
    _assert(
        "stranger paid from a lean app",
        _balance(algorand, stranger.address) - stranger_before,
        FEE - KEEPER_TXN_COST,
    )
    fresh_refund = fresh.send.cancel(
        args=CancelArgs(upkeep_id=lean_id),
        params=algokit_utils.CommonAppCallParams(
            extra_fee=algokit_utils.AlgoAmount(micro_algo=1_000)
        ),
    ).abi_return
    _assert("fresh app back to its base MBR", _balance(algorand, fresh.app_address), 100_000)
    logger.info(f"  ✔ cancel returned {fresh_refund} µALGO (escrow 0 + box MBR)")

    # ------------------------------------------------------------------
    logger.info("── 12. Cadences from seconds to days ──")
    # An upkeep is a promise about the future, so the schedule has to be right
    # at every horizon a user would pick — not just the 10-round minimum the
    # other stages use.
    for label, interval in CADENCES:
        registered = _register_with_interval(
            algorand, keeper_client, deployer, pulse_client.app_id, FEE * 3, interval
        )
        upkeep, _ = _read_upkeep(algorand, app_id, registered)
        confirmed = algorand.client.algod.status()["last-round"]
        _assert(
            f"every {label} ({interval} rounds ≈ {_human(interval)}): next run",
            upkeep.next_execution_round - upkeep.interval_rounds <= confirmed,
            True,
        )
        _assert(f"every {label}: interval", upkeep.interval_rounds, interval)
        _assert(f"every {label}: runs funded", upkeep.balance // upkeep.fee_per_execution, 3)
        # Nothing this far out may be executable, and the bot must agree.
        current = algorand.client.algod.status()["last-round"]
        _assert(f"every {label}: due yet", current >= upkeep.next_execution_round, False)
        due_to_bot = [
            u
            for u in keeper_bot.scan_upkeeps(algorand.client.algod, app_id)
            if u.upkeep_id == registered and current >= u.next_execution_round
        ]
        _assert(f"every {label}: bot sees it as due", due_to_bot, [])
        _expect_failure(
            f"execute a {label} upkeep early",
            "Not due",
            lambda upkeep_id=registered: stranger_client.send.execute(
                args=ExecuteArgs(upkeep_id=upkeep_id),
                params=algokit_utils.CommonAppCallParams(
                    extra_fee=algokit_utils.AlgoAmount(
                        micro_algo=keeper_bot.EXTRA_FEE_MICROALGO
                    )
                ),
            ),
        )
        keeper_client.send.cancel(
            args=CancelArgs(upkeep_id=registered),
            params=algokit_utils.CommonAppCallParams(
                extra_fee=algokit_utils.AlgoAmount(micro_algo=1_000)
            ),
        )
    _assert_solvent(algorand, keeper_client, app_id)

    # ------------------------------------------------------------------
    logger.info("── 13. A long-missed upkeep catches up one interval at a time ──")
    # The contract schedules from the *scheduled* round, not the round it was
    # executed in. An upkeep left unattended for hours therefore stays due
    # until it has caught up, rather than silently skipping its history.
    missed_id = _register(algorand, keeper_client, deployer, pulse_client.app_id, FEE * 3)
    missed, _ = _read_upkeep(algorand, app_id, missed_id)
    scheduled = missed.next_execution_round
    # Sleep through three whole intervals.
    net.wait_for_round(algorand, scheduled + 3 * INTERVAL_ROUNDS, poker=deployer)
    response = stranger_client.send.execute(
        args=ExecuteArgs(upkeep_id=missed_id),
        params=algokit_utils.CommonAppCallParams(
            extra_fee=algokit_utils.AlgoAmount(micro_algo=keeper_bot.EXTRA_FEE_MICROALGO)
        ),
    )
    _assert("next due after a missed window", response.abi_return, scheduled + INTERVAL_ROUNDS)
    after, _ = _read_upkeep(algorand, app_id, missed_id)
    _assert(
        "still due, still catching up",
        algorand.client.algod.status()["last-round"] >= after.next_execution_round,
        True,
    )
    keeper_client.send.cancel(
        args=CancelArgs(upkeep_id=missed_id),
        params=algokit_utils.CommonAppCallParams(
            extra_fee=algokit_utils.AlgoAmount(micro_algo=1_000)
        ),
    )

    # ------------------------------------------------------------------
    logger.info("── 14. What a losing keeper actually pays ──")
    # Algorand rejects a failing transaction at validation: it never reaches a
    # block, so its sender pays nothing. That is the opposite of EVM chains,
    # where a revert still burns gas — and it decides how aggressive a keeper
    # bot's backoff needs to be, so it is measured here rather than assumed.
    race_id = _register(algorand, keeper_client, deployer, pulse_client.app_id, FEE * 2)
    race, _ = _read_upkeep(algorand, app_id, race_id)
    net.wait_for_round(algorand, race.next_execution_round, poker=deployer)

    # Two keepers, one due upkeep. The stranger wins; the deployer loses.
    loser_before = _balance(algorand, deployer.address)
    stranger_client.send.execute(
        args=ExecuteArgs(upkeep_id=race_id),
        params=algokit_utils.CommonAppCallParams(
            extra_fee=algokit_utils.AlgoAmount(micro_algo=keeper_bot.EXTRA_FEE_MICROALGO)
        ),
    )
    lost_txid = None
    try:
        with _quiet():
            lost_txid = _raw_execute(
                algorand, app_id, deployer, race_id, pulse_client.app_id
            )
    except Exception as exc:
        rejection = str(exc)
    else:
        rejection = ""
    assert rejection != "", "the losing keeper's execute should have been rejected"
    _assert_rejected_by_algod(rejection)
    _assert("losing keeper charged", loser_before - _balance(algorand, deployer.address), 0)
    _assert("losing transaction reached a block", lost_txid, None)
    logger.info(f"  ✔ algod rejected it outright: {_first_line(rejection)}")

    # The other way to lose: the registered call itself fails. A bogus selector
    # makes Pulse's router reject the inner call, which fails the whole group.
    doomed_id = _register_with_interval(
        algorand,
        keeper_client,
        deployer,
        pulse_client.app_id,
        FEE * 2,
        INTERVAL_ROUNDS,
        call_data=b"\xde\xad\xbe\xef",
    )
    doomed, _ = _read_upkeep(algorand, app_id, doomed_id)
    net.wait_for_round(algorand, doomed.next_execution_round, poker=deployer)
    keeper_before = _balance(algorand, stranger.address)
    try:
        with _quiet():
            _raw_execute(algorand, app_id, stranger, doomed_id, pulse_client.app_id)
    except Exception as exc:
        rejection = str(exc)
    else:
        rejection = ""
    assert rejection != "", "an upkeep whose target rejects should not execute"
    _assert_rejected_by_algod(rejection)
    _assert(
        "keeper charged for a rejecting target",
        keeper_before - _balance(algorand, stranger.address),
        0,
    )
    still_doomed, _ = _read_upkeep(algorand, app_id, doomed_id)
    _assert("failed execution changed state", still_doomed.times_executed, 0)
    _assert("failed execution took escrow", still_doomed.balance, doomed.balance)
    logger.info("  ✔ a failed execution is free: no fee, no state change")

    for cleanup_id in (race_id, doomed_id):
        keeper_client.send.cancel(
            args=CancelArgs(upkeep_id=cleanup_id),
            params=algokit_utils.CommonAppCallParams(
                extra_fee=algokit_utils.AlgoAmount(micro_algo=1_000)
            ),
        )

    # ------------------------------------------------------------------
    # Return the stranger's remaining balance; on TestNet that is real ALGO.
    algorand.send.payment(
        algokit_utils.PaymentParams(
            sender=stranger.address,
            receiver=deployer.address,
            amount=algokit_utils.AlgoAmount(micro_algo=0),
            close_remainder_to=deployer.address,
        )
    )

    info = algorand.client.algod.account_info(keeper_client.app_address)
    logger.info("")
    logger.info(f"Keeper e2e passed on {network} ✔")
    logger.info(f"  Keeper app {app_id}, Pulse app {pulse_client.app_id}")
    logger.info(f"  Pulse.beats = {pulse_client.state.global_state.beats}")
    logger.info(
        f"  App account: {info['amount']} µALGO, min-balance "
        f"{info['min-balance']} µALGO"
    )


if __name__ == "__main__":
    main()
