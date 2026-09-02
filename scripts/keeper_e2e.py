"""End-to-end test for the Arcron keeper network against a real node.

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
from algosdk import abi, encoding

from scripts import keeper_bot, network as net
from scripts.keeper_backoff import Backoff
from smart_contracts.artifacts.keeper.keeper_client import (
    CancelArgs,
    OptInAssetArgs,
    TopUpAssetArgs,
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
# Measured over 1,000,000 rounds on 2026-08-28; the nominal figure is 2.8.
ROUND_SECONDS = 2.752
# Cadences a real user would pick, from a heartbeat to a daily settlement.
CADENCES = (
    ("30 seconds", 10),
    ("5 minutes", 107),
    ("1 hour", 1_286),
    ("1 day", 30_857),
)


def _selector(signature: str) -> bytes:
    return hashlib.new("sha512_256", signature.encode()).digest()[:4]


def _encode_args(call_args: list[bytes]) -> bytes:
    """The ARC-4 `byte[][]` an upkeep stores."""
    return abi.ABIType.from_string("byte[][]").encode([list(a) for a in call_args])


def _box_mbr(call_args: list[bytes] | bytes) -> int:
    """What one upkeep box costs, per the contract's own constant."""
    if isinstance(call_args, (bytes, bytearray)):
        call_args = [bytes(call_args)]
    return BOX_MBR_FIXED + 400 * len(_encode_args(call_args))


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
    fee: int = FEE,
    policy: int = keeper_bot.CATCH_UP,
    fee_cap: int = 0,
    call_args: list[bytes] | None = None,
    fee_asset: int = 0,
    asset_fee: int = 0,
) -> int:
    """Register an upkeep at an arbitrary cadence; returns the new upkeep id."""
    if call_args is None:
        call_args = [_selector(CALL_SIGNATURE) if call_data is None else call_data]
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
            mbr_payment=payment(_box_mbr(call_args)),
            funding_payment=payment(funding),
            target_app=target_app,
            call_args=call_args,
            interval_rounds=interval,
            fee_per_execution=fee,
            policy=policy,
            fee_cap=fee_cap,
            fee_asset=fee_asset,
            asset_fee=asset_fee,
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


# Populated by `_raw_execute`, so an assertion can read what an execution did
# rather than what an account balance says afterwards.
_LAST_CONFIRMATION: dict = {}


def _raw_execute(
    algorand, app_id: int, account, upkeep_id: int, target_app: int, assets=()
) -> str:
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
        foreign_assets=list(assets),
    )
    signed = account.signer.sign_transactions([txn], [0])
    # send_transactions encodes the signed objects; send_raw_transaction wants
    # bytes and would fail client-side, which would look like a rejection.
    txid = algorand.client.algod.send_transactions(signed)
    # Wait for it, and keep the confirmation. Reading an account balance
    # straight after sending reads state the node has not applied yet — which
    # is invisible on LocalNet, where dev mode commits a block per
    # transaction, and a flake on a public endpoint.
    _LAST_CONFIRMATION.clear()
    _LAST_CONFIRMATION.update(
        transaction.wait_for_confirmation(algorand.client.algod, txid, 6)
    )
    return txid


def _paid_to_caller() -> int:
    """What the last raw execution paid its caller, from its own confirmation.

    Deliberately not an account balance: this reads what the contract did,
    not what a node currently believes an account holds.
    """
    for inner in _LAST_CONFIRMATION.get("inner-txns", []):
        txn = inner.get("txn", {}).get("txn", {})
        if txn.get("type") == "pay":
            return int(txn.get("amt", 0))
    return 0


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
    # Bracket the registration rather than reading the round after it. algod's
    # last-round can already have moved on by the time the call returns, and
    # asserting equality against it made this stage fail one run in several.
    before_round = algorand.client.algod.status()["last-round"]
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
    _assert("policy", upkeep.policy, keeper_bot.CATCH_UP)
    _assert("fee_cap", upkeep.fee_cap, 0)
    _assert(
        "last_serviced_round is the round it registered in",
        before_round <= upkeep.last_serviced_round <= registered_round,
        True,
    )
    # The tail begins where the head ends; read the offset rather than
    # restating it, so a struct change shows up as a decode failure and not as
    # a test quietly checking the wrong bytes.
    tail = int.from_bytes(raw[40:42], "big")
    _assert("head size", tail, 130)
    _assert("call_args", raw[tail:], _encode_args([_selector(CALL_SIGNATURE)]))
    _assert("fee_asset", upkeep.fee_asset, 0)
    _assert("asset_balance", upkeep.asset_balance, 0)
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

    # The same two losses, but reached through the bot rather than raw, because
    # what the keeper *does* about a loss is decided by which of them it thinks
    # it just had. Losing a race must never back an upkeep off; a target that
    # rejects must. The two arrive as the same kind of exception, so the code
    # that separates them is worth pinning against a real AVM.
    logger.info("── 14b. A losing keeper can tell a race from a broken target ──")
    collision_id = _register(algorand, keeper_client, deployer, pulse_client.app_id, FEE * 4)
    collision, _ = _read_upkeep(algorand, app_id, collision_id)
    net.wait_for_round(algorand, collision.next_execution_round, poker=deployer)

    # Both keepers reach at the same round, so both simulate against a state
    # where the upkeep is still due. That is the ordinary shape of a race and
    # the one a controlled experiment misses: the loser is not refused before
    # it broadcasts, it is refused by the pool after it does.
    winner_params = keeper_bot._resolve_execute_references(
        stranger_client, collision, keeper_bot.EXTRA_FEE_MICROALGO
    )
    loser_params = keeper_bot._resolve_execute_references(
        keeper_client, collision, keeper_bot.EXTRA_FEE_MICROALGO
    )
    no_populate = algokit_utils.SendParams(populate_app_call_resources=False)
    stranger_client.send.execute(
        args=ExecuteArgs(upkeep_id=collision_id),
        params=winner_params,
        send_params=no_populate,
    )
    loser_before = _balance(algorand, deployer.address)
    try:
        with _quiet():
            keeper_client.send.execute(
                args=ExecuteArgs(upkeep_id=collision_id),
                params=loser_params,
                send_params=no_populate,
            )
    except Exception as exc:
        lost = exc
    else:
        lost = None
    assert lost is not None, "both keepers cannot win the same execution"
    _assert_rejected_by_algod(keeper_bot.failure_text(lost))
    _assert("losing keeper charged, mid-flight", loser_before - _balance(algorand, deployer.address), 0)

    moved, after_race = keeper_bot.registry_moved_on(
        algorand.client.algod, app_id, collision
    )
    _assert("the registry says it moved on", moved, True)
    assert after_race is not None
    _assert(
        "and names the round it moved in",
        after_race.last_serviced_round >= collision.next_execution_round,
        True,
    )
    _assert(
        "the winner is recoverable from the block",
        keeper_bot.find_winner(
            algorand.client.algod, app_id, collision_id, after_race.last_serviced_round
        ),
        stranger.address,
    )
    race_backoff = Backoff(None)
    _assert(
        "a lost race backs nothing off",
        race_backoff.record_failure(
            collision_id, keeper_bot.failure_text(lost), 0, INTERVAL_ROUNDS, advanced=moved
        ),
        None,
    )

    # And the contrast, from the same code path: the doomed upkeep's target is
    # what failed, so the registry has not moved and the bot backs it off.
    try:
        with _quiet():
            keeper_client.send.execute(
                args=ExecuteArgs(upkeep_id=doomed_id),
                params=keeper_bot._resolve_execute_references(
                    keeper_client, still_doomed, keeper_bot.EXTRA_FEE_MICROALGO
                ),
                send_params=no_populate,
            )
    except Exception as exc:
        broke = exc
    else:
        broke = None
    assert broke is not None, "a target that rejects cannot be executed"
    broken_moved, _ = keeper_bot.registry_moved_on(
        algorand.client.algod, app_id, still_doomed
    )
    _assert("a broken target moves nothing", broken_moved, False)
    entry = race_backoff.record_failure(
        doomed_id, keeper_bot.failure_text(broke), 100, INTERVAL_ROUNDS, advanced=broken_moved
    )
    assert entry is not None, "a broken target must be backed off"
    # A round, not an interval. `docs/reviews/2026-09-01-opus-5-audit-verification.md`
    # §3: a target that refuses conditionally — an oracle on a stale price, a
    # rebalancer on an epoch — is indistinguishable from a broken one at the
    # moment it refuses, and this used to cost the upkeep `1 x interval`
    # rounds, capped at an hour. The whole argument for the short schedule is
    # in `scripts/keeper_backoff.py`, and this is where it meets a real node:
    # the classification and the site below are read out of what algod
    # actually wrote, not out of a string a test made up.
    _assert("and is retried a round later, not an hour", entry.next_attempt_round, 101)
    _assert("algod attributed it to the target", entry.target_refusal, True)
    _assert(
        "and named the instruction that refused",
        entry.site.startswith(f"app={still_doomed.target_app} pc="),
        True,
    )
    logger.info(
        f"  ✔ the loser paid 0 µALGO and kept trying; the broken target waits "
        f"1 round and is recorded at {entry.site}"
    )

    for cleanup_id in (race_id, collision_id, doomed_id):
        keeper_client.send.cancel(
            args=CancelArgs(upkeep_id=cleanup_id),
            params=algokit_utils.CommonAppCallParams(
                extra_fee=algokit_utils.AlgoAmount(micro_algo=1_000)
            ),
        )

    # ------------------------------------------------------------------
    logger.info("── 15. SKIP_AHEAD drops the backlog and keeps the phase ──")
    # Stage 13 showed the default: replay every missed interval. For work
    # where only the latest run matters — a draw, a staleness check, a switch
    # that fires once — that is pure waste, so the creator can say so at
    # registration. The upkeep must land on a slot strictly in the future
    # while staying on its original phase, so a daily upkeep keeps its time of
    # day instead of drifting to whenever a keeper happened to arrive.
    skip_id = _register_with_interval(
        algorand,
        keeper_client,
        deployer,
        pulse_client.app_id,
        FEE * 4,
        INTERVAL_ROUNDS,
        policy=keeper_bot.SKIP_AHEAD,
    )
    skip, _ = _read_upkeep(algorand, app_id, skip_id)
    scheduled = skip.next_execution_round
    net.wait_for_round(algorand, scheduled + 3 * INTERVAL_ROUNDS, poker=deployer)
    at_execution = algorand.client.algod.status()["last-round"]
    stranger_client.send.execute(
        args=ExecuteArgs(upkeep_id=skip_id),
        params=algokit_utils.CommonAppCallParams(
            extra_fee=algokit_utils.AlgoAmount(micro_algo=keeper_bot.EXTRA_FEE_MICROALGO)
        ),
    )
    skipped, _ = _read_upkeep(algorand, app_id, skip_id)
    _assert("one execution, not four", skipped.times_executed, 1)
    _assert("one fee taken, not four", skip.balance - skipped.balance, FEE)
    _assert("no longer due", skipped.next_execution_round > at_execution, True)
    _assert(
        "landed on the schedule's own phase",
        (skipped.next_execution_round - scheduled) % INTERVAL_ROUNDS,
        0,
    )
    _assert(
        "landed on the *first* future slot",
        skipped.next_execution_round - at_execution <= INTERVAL_ROUNDS,
        True,
    )
    _assert(
        "recorded when it actually ran, not when it was scheduled",
        skipped.last_serviced_round >= at_execution,
        True,
    )
    logger.info(
        f"  ✔ missed 3 intervals, ran once, next due {skipped.next_execution_round} "
        f"(scheduled phase {scheduled} + {(skipped.next_execution_round - scheduled) // INTERVAL_ROUNDS} intervals)"
    )

    # ------------------------------------------------------------------
    logger.info("── 16. A neglected upkeep pays more, once ──")
    # Escalation exists to clear a market: an upkeep nobody wants becomes
    # worth doing. Once a keeper has arrived the market has cleared, so the
    # backlog it then drains pays base — otherwise catch-up and escalation
    # multiply, and a long-neglected upkeep burns its escrow at the ceiling
    # for work nobody asked for.
    cap = FEE * 3
    esc_id = _register_with_interval(
        algorand,
        keeper_client,
        deployer,
        pulse_client.app_id,
        FEE * 8,
        INTERVAL_ROUNDS,
        fee_cap=cap,
    )
    before, _ = _read_upkeep(algorand, app_id, esc_id)

    def _execute_and_price(upkeep_id: int, previous) -> tuple[int, object]:
        stranger_client.send.execute(
            args=ExecuteArgs(upkeep_id=upkeep_id),
            params=algokit_utils.CommonAppCallParams(
                extra_fee=algokit_utils.AlgoAmount(
                    micro_algo=keeper_bot.EXTRA_FEE_MICROALGO
                )
            ),
        )
        after, _ = _read_upkeep(algorand, app_id, upkeep_id)
        paid = previous.balance - after.balance
        # The box now records the round it actually ran in, so the bot's twin
        # of the escalation arithmetic can be checked against the contract's —
        # on a real chain, at whatever round the transaction happened to land.
        _assert(
            "the bot's fee arithmetic agrees with the contract",
            paid,
            keeper_bot.effective_fee(previous, after.last_serviced_round),
        )
        return paid, after

    # Executed as soon as it comes due, which is never exactly on the due
    # round. LocalNet advances a round per transaction, so this lands one or
    # two rounds late. TestNet advances every ~2.8 seconds whether or not
    # anyone is sending, so the same code lands three or four rounds late and
    # the fee has climbed correspondingly further up the curve.
    #
    # An earlier version asserted the fee was within a fixed quarter of the
    # way to the ceiling. That is a LocalNet assumption: it passed there
    # always, and on TestNet it passed or failed depending on how many rounds
    # happened to elapse while the transaction was in flight. It failed at
    # 6,400 against a 6,000 bound, having landed three rounds late, with the
    # contract behaving exactly as specified.
    #
    # So bound the claim by what actually happened rather than by a constant.
    # The escalation is linear in rounds late over one interval, so a fee that
    # is on the curve for its own lateness is the real invariant, and the
    # assertion above has already checked it against the contract's own
    # arithmetic.
    net.wait_for_round(algorand, before.next_execution_round, poker=deployer)
    on_time_fee, after_on_time = _execute_and_price(esc_id, before)
    _assert("an on-time execution does not pay the ceiling", on_time_fee < cap, True)

    rounds_late = after_on_time.last_serviced_round - before.next_execution_round
    _assert(
        f"and is only {rounds_late} round(s) late, so barely up the curve",
        rounds_late < INTERVAL_ROUNDS,
        True,
    )
    _assert(
        "the fee is where the curve puts it for that lateness",
        on_time_fee,
        FEE + (cap - FEE) * rounds_late // INTERVAL_ROUNDS,
    )

    # Neglected for two intervals past the last service: the curve is flat at
    # the ceiling from one whole missed interval onwards.
    net.wait_for_round(
        algorand,
        after_on_time.last_serviced_round + 2 * INTERVAL_ROUNDS + 2,
        poker=deployer,
    )
    late_fee, after_late = _execute_and_price(esc_id, after_on_time)
    _assert("a neglected execution pays the ceiling", late_fee, cap)

    # The same keeper immediately drains the backlog. It was serviced moments
    # ago, so it is not late, so it pays base — this is the whole reason
    # escalation is measured from the last service rather than the schedule.
    drain_fee, after_drain = _execute_and_price(esc_id, after_late)
    _assert("the backlog behind it pays base", drain_fee, FEE)
    _assert(
        "escrow spent is one ceiling, not three",
        before.balance - after_drain.balance,
        on_time_fee + cap + FEE,
    )
    logger.info(
        f"  ✔ {on_time_fee} µALGO on time, {cap} µALGO neglected, {FEE} µALGO for "
        f"the replay behind it"
    )

    # ------------------------------------------------------------------
    logger.info("── 17. The bot reaches for the escalated work first ──")
    # #14's point: a creator paying the minimum buys latency rather than
    # unreliability. That only holds if keepers actually re-rank, so the bot
    # takes due work by effective fee rather than by registry order.
    cheap_id = _register_with_interval(
        algorand, keeper_client, deployer, pulse_client.app_id, FEE * 8,
        INTERVAL_ROUNDS, fee=FEE, fee_cap=FEE * 3,
    )
    rich_id = _register_with_interval(
        algorand, keeper_client, deployer, pulse_client.app_id, FEE * 8,
        INTERVAL_ROUNDS, fee=FEE * 2,
    )
    cheap, _ = _read_upkeep(algorand, app_id, cheap_id)
    net.wait_for_round(
        algorand, cheap.last_serviced_round + 2 * INTERVAL_ROUNDS + 2, poker=deployer
    )
    at_round = algorand.client.algod.status()["last-round"]
    registry = keeper_bot.scan_upkeeps(algorand.client.algod, app_id)
    # The bot's own selection, not a copy of it: this stage exists to catch a
    # regression to registry order, and a reimplementation here would pass
    # whatever `keeper_bot` did.
    queue = keeper_bot.select_due(registry, at_round)
    _assert(
        "the neglected minimum-fee upkeep outranks the richer one",
        queue[0].upkeep_id,
        cheap_id,
    )
    _assert(
        "because it is now worth more",
        keeper_bot.effective_fee(queue[0], at_round)
        > keeper_bot.effective_fee(
            next(u for u in registry if u.upkeep_id == rich_id), at_round
        ),
        True,
    )
    logger.info(
        f"  ✔ upkeep {cheap_id} at {keeper_bot.effective_fee(queue[0], at_round)} µALGO "
        f"ahead of upkeep {rich_id} at {FEE * 2} µALGO"
    )

    for cleanup_id in (skip_id, esc_id, cheap_id, rich_id):
        keeper_client.send.cancel(
            args=CancelArgs(upkeep_id=cleanup_id),
            params=algokit_utils.CommonAppCallParams(
                extra_fee=algokit_utils.AlgoAmount(micro_algo=1_000)
            ),
        )

    # ------------------------------------------------------------------
    logger.info("── 18. A target method with arguments of its own ──")
    # Before #8 an upkeep carried one app arg, so only zero-argument hooks
    # were reachable: an ARC-4 method needs its selector *and* each argument
    # in an app arg of its own. This registers a real three-arg call and
    # checks the target's state moved by the argument's value, not by one.
    note = "arcron"
    step = 7
    multi_args = [
        _selector("tick_with(uint64,string)uint64"),
        step.to_bytes(8, "big"),
        abi.ABIType.from_string("string").encode(note),
    ]
    beats_before = int(pulse_client.state.global_state.beats)
    multi_id = _register_with_interval(
        algorand, keeper_client, deployer, pulse_client.app_id, FEE * 3,
        INTERVAL_ROUNDS, call_args=multi_args,
    )
    multi, raw = _read_upkeep(algorand, app_id, multi_id)
    _assert("stored three app args", raw[130:], _encode_args(multi_args))
    net.wait_for_round(algorand, multi.next_execution_round, poker=deployer)
    stranger_client.send.execute(
        args=ExecuteArgs(upkeep_id=multi_id),
        params=algokit_utils.CommonAppCallParams(
            extra_fee=algokit_utils.AlgoAmount(micro_algo=keeper_bot.EXTRA_FEE_MICROALGO)
        ),
    )
    _assert(
        "the target advanced by the argument, not by one",
        int(pulse_client.state.global_state.beats) - beats_before,
        step,
    )
    _assert(
        "and received the second argument too",
        pulse_client.state.global_state.last_note,
        note,
    )
    keeper_client.send.cancel(
        args=CancelArgs(upkeep_id=multi_id),
        params=algokit_utils.CommonAppCallParams(
            extra_fee=algokit_utils.AlgoAmount(micro_algo=1_000)
        ),
    )

    # ------------------------------------------------------------------
    logger.info("── 19. An ASA bonus on top of the ALGO fee ──")
    # The ALGO fee is never replaced, so a keeper that does not hold — or does
    # not want — the asset is still paid for its work. That is what keeps the
    # profitability floor checkable on-chain without anyone pricing the asset.
    asset_id = algorand.send.asset_create(
        algokit_utils.AssetCreateParams(sender=deployer.address, total=10_000_000)
    ).asset_id
    bonus = 250_000
    bonus_id = _register_with_interval(
        algorand, keeper_client, deployer, pulse_client.app_id, FEE * 4,
        INTERVAL_ROUNDS, fee_asset=asset_id, asset_fee=bonus,
    )
    box_name = b"u" + bonus_id.to_bytes(8, "big")
    first_valid = algorand.client.algod.status()["last-round"]
    keeper_client.send.opt_in_asset(
        args=OptInAssetArgs(
            mbr_payment=algorand.create_transaction.payment(
                algokit_utils.PaymentParams(
                    sender=deployer.address,
                    receiver=keeper_client.app_address,
                    amount=algokit_utils.AlgoAmount(micro_algo=100_000),
                    first_valid_round=first_valid,
                    last_valid_round=first_valid + 1_000,
                )
            ),
            upkeep_id=bonus_id,
            asset=asset_id,
        ),
        params=algokit_utils.CommonAppCallParams(
            extra_fee=algokit_utils.AlgoAmount(micro_algo=1_000),
            asset_references=[asset_id],
            box_references=[box_name],
        ),
    )
    keeper_client.send.top_up_asset(
        args=TopUpAssetArgs(
            upkeep_id=bonus_id,
            asset_funding=algorand.create_transaction.asset_transfer(
                algokit_utils.AssetTransferParams(
                    sender=deployer.address,
                    receiver=keeper_client.app_address,
                    asset_id=asset_id,
                    amount=bonus * 4,
                )
            ),
        ),
        params=algokit_utils.CommonAppCallParams(box_references=[box_name]),
    )

    def _asset_of(address: str) -> int | None:
        info = algorand.client.algod.account_info(address)
        for holding in info.get("assets", []):
            if holding["asset-id"] == asset_id:
                return holding["amount"]
        return None

    # The stranger has never seen this asset, so it cannot receive the bonus —
    # and must still be paid its ALGO fee rather than having the call fail.
    _assert("the stranger cannot hold the asset", _asset_of(stranger.address), None)
    bonus_upkeep, _ = _read_upkeep(algorand, app_id, bonus_id)
    net.wait_for_round(algorand, bonus_upkeep.next_execution_round, poker=deployer)
    _raw_execute(algorand, app_id, stranger, bonus_id, pulse_client.app_id, assets=[asset_id])
    after_stranger, _ = _read_upkeep(algorand, app_id, bonus_id)
    _assert("an un-opted-in keeper still earns the ALGO fee", _paid_to_caller(), FEE)
    _assert("and the bonus stays in escrow", after_stranger.asset_balance, bonus * 4)

    # Now a keeper that has opted in.
    algorand.send.asset_opt_in(
        algokit_utils.AssetOptInParams(sender=deployer.address, asset_id=asset_id)
    )
    held_before = _asset_of(deployer.address) or 0
    net.wait_for_round(algorand, after_stranger.next_execution_round, poker=deployer)
    keeper_client.send.execute(
        args=ExecuteArgs(upkeep_id=bonus_id),
        params=algokit_utils.CommonAppCallParams(
            extra_fee=algokit_utils.AlgoAmount(micro_algo=3_000),
            asset_references=[asset_id],
        ),
    )
    after_holder, _ = _read_upkeep(algorand, app_id, bonus_id)
    _assert(
        "an opted-in keeper is paid the bonus",
        (_asset_of(deployer.address) or 0) - held_before,
        bonus,
    )
    _assert("and the escrow falls by exactly that", after_holder.asset_balance, bonus * 3)

    refunded = keeper_client.send.cancel(
        args=CancelArgs(upkeep_id=bonus_id),
        params=algokit_utils.CommonAppCallParams(
            extra_fee=algokit_utils.AlgoAmount(micro_algo=2_000),
            asset_references=[asset_id],
        ),
    )
    _assert(
        "cancel returns the unspent bonus too",
        (_asset_of(deployer.address) or 0) - held_before,
        bonus + bonus * 3,
    )
    logger.info(f"  ✔ ALGO refund {refunded.abi_return} µALGO, plus {bonus * 3} base units")

    # ------------------------------------------------------------------
    logger.info("── 20. One escrow can never pay for another (#12) ──")
    # The app account is shared by every upkeep; only the boxes are separate.
    # So the accounting invariant worth stating is that the app's *spendable*
    # balance always covers the sum of every escrow — and that no upkeep can
    # draw on another's. Checked after each mutation rather than once at the
    # end, because a transient violation is still a bug.
    varied: list[int] = []
    # Deliberately varied box sizes and fees: the MBR is charged per byte, so
    # a registry of identical upkeeps would not exercise the formula.
    for args, fee, funding in (
        ([_selector(CALL_SIGNATURE)], FEE, FEE * 2),
        ([_selector("tick_with(uint64,string)uint64"), (1).to_bytes(8, "big"),
          abi.ABIType.from_string("string").encode("x" * 40)], FEE * 2, FEE * 6),
        # Funded for exactly one run, so it drains while its neighbours do not.
        ([_selector(CALL_SIGNATURE)], FEE, FEE),
    ):
        varied.append(
            _register_with_interval(
                algorand, keeper_client, deployer, pulse_client.app_id, funding,
                INTERVAL_ROUNDS, call_args=args, fee=fee,
            )
        )
        _assert_solvent(algorand, keeper_client, app_id)

    # The poorest upkeep, executed until its own escrow is empty, must never
    # reach into the richest one's.
    poor, rich = varied[2], varied[1]
    rich_before, _ = _read_upkeep(algorand, app_id, rich)
    runs = 0
    while True:
        state, _ = _read_upkeep(algorand, app_id, poor)
        if state.balance < state.fee_per_execution:
            break
        net.wait_for_round(algorand, state.next_execution_round, poker=deployer)
        keeper_client.send.execute(
            args=ExecuteArgs(upkeep_id=poor),
            params=algokit_utils.CommonAppCallParams(
                extra_fee=algokit_utils.AlgoAmount(micro_algo=keeper_bot.EXTRA_FEE_MICROALGO)
            ),
        )
        runs += 1
        _assert_solvent(algorand, keeper_client, app_id)

    drained, _ = _read_upkeep(algorand, app_id, poor)
    rich_after, _ = _read_upkeep(algorand, app_id, rich)
    _assert("the poor upkeep spent exactly its own escrow", drained.balance, 0)
    _assert("and it ran only what it funded", runs, 1)
    _assert("its neighbour is untouched", rich_after.balance, rich_before.balance)
    # Wait until it is genuinely due, so the rejection is about the money and
    # not about the schedule.
    net.wait_for_round(algorand, drained.next_execution_round, poker=deployer)
    _expect_failure(
        "a drained upkeep cannot borrow from the app account",
        "Insufficient funding",
        lambda: keeper_client.send.execute(
            args=ExecuteArgs(upkeep_id=poor),
            params=algokit_utils.CommonAppCallParams(
                extra_fee=algokit_utils.AlgoAmount(micro_algo=keeper_bot.EXTRA_FEE_MICROALGO)
            ),
        ),
    )

    # Cancelling returns exactly what that upkeep put in, and leaves the rest
    # of the registry solvent.
    for cleanup_id in varied:
        keeper_client.send.cancel(
            args=CancelArgs(upkeep_id=cleanup_id),
            params=algokit_utils.CommonAppCallParams(
                extra_fee=algokit_utils.AlgoAmount(micro_algo=1_000)
            ),
        )
        _assert_solvent(algorand, keeper_client, app_id)

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
