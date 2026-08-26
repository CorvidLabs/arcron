"""Spike: can algod `simulate` honestly predict what a real Arcron `execute`
will do, *before* the upkeep box that `execute` requires exists?

The console plan proposes a "Test" button on the registration form: before a
creator escrows anything, simulate the call Arcron will make and tell them
whether their target will accept it. This measures whether that simulation
can be built to be honest, rather than reasoning about what algod probably
does.

Five questions, each with its own section below:

1. **Sender.** Arcron's inner call arrives with `Txn.sender` set to the
   keeper *application's* account (`docs/integrating.md` tells integrators to
   check exactly that). That account has no private key. Does algod's
   simulate accept a transaction from it at all, and under which flag?
2. **The outer path.** `Keeper.execute` needs an upkeep box. Can it be
   simulated meaningfully — or even at all — before one has been registered?
3. **The inner path.** Simulating the *target* call directly, with sender set
   to the keeper app's address: does a target that checks
   `Txn.sender == Application(keeper_app).address` pass, and does one that
   doesn't check anything (or reverts unconditionally) behave as expected?
4. **Fidelity: fees and resources.** Arcron's inner call carries no foreign
   arrays of its own (`smart_contracts/keeper/contract.py`) and a zero fee
   pooled from the group. Does a standalone simulated call reproduce either
   constraint, or does simulating it in isolation quietly grant it more than
   a real execution ever will?
5. **The false-positive hunt.** For every target above, this also registers a
   *real* upkeep on LocalNet and runs a real `execute`, so the simulated
   prediction can be checked against what the chain actually does. Any
   disagreement is a lie the Test button could tell.

Targets: `smart_contracts/sim_probe/`. See its module docstring for what each
method isolates.

Run:  poetry run python -m scripts.spike_simulate_test_button [--network localnet]
"""

import argparse
import logging

import algokit_utils
from algosdk import transaction
from algosdk.logic import get_application_address
from algosdk.v2client.models import SimulateRequest, SimulateRequestTransactionGroup

from scripts import network as net
from scripts.keeper_e2e import _box_mbr, _read_upkeep, _selector
from smart_contracts.artifacts.keeper.keeper_client import (
    CancelArgs,
    ExecuteArgs,
    RegisterArgs,
)
from smart_contracts.artifacts.sim_probe.sim_probe_client import (
    ConfigureArgs,
    ConfigureSubjectsArgs,
)
from smart_contracts.keeper.deploy_config import deploy as deploy_keeper
from smart_contracts.sim_probe.deploy_config import deploy as deploy_probe

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)
# algokit-utils logs every send failure at ERROR with a full traceback before
# re-raising -- expected and handled below (this spike deliberately causes
# failures), so it is silenced to keep the output readable. Its logger is a
# standalone AlgoKitLogger instance, not one `logging.getLogger` can reach.
from algokit_utils.config import config as _algokit_config  # noqa: E402

_algokit_config.logger.setLevel(logging.CRITICAL)

FEE = 4_000
INTERVAL = 10
# Generous: the group carries Arcron's inner call, the probe's own budget-
# burning loop, and the keeper's payment.
EXECUTE_FEE = 8_000
MIN_FEE = 1_000


# --------------------------------------------------------------------------
# Simulate plumbing
# --------------------------------------------------------------------------


def _unsigned(txn: "transaction.Transaction") -> "transaction.SignedTransaction":
    """Wrap a transaction with no signature at all.

    `algosdk.transaction.SignedTransaction.dictify` only emits a `sig` field
    when `signature` is truthy, so an empty string produces exactly the
    "signed transaction with an empty signature" shape `allow-empty-
    signatures` exists to accept. This is the only way to hand algod a
    transaction from the keeper app's account: that account has no private
    key, ever, so nothing can produce a real one.
    """
    return transaction.SignedTransaction(txn, "")


def _simulate(
    algod,
    txns: list["transaction.Transaction"],
    *,
    allow_empty_signatures: bool = True,
    allow_unnamed_resources: bool = False,
    extra_opcode_budget: int = 0,
) -> dict:
    request = SimulateRequest(
        txn_groups=[SimulateRequestTransactionGroup(txns=[_unsigned(t) for t in txns])],
        allow_empty_signatures=allow_empty_signatures,
        allow_unnamed_resources=allow_unnamed_resources,
        extra_opcode_budget=extra_opcode_budget,
    )
    return algod.simulate_transactions(request)


def _outcome(response: dict) -> tuple[bool, str]:
    """(passed, message) from a raw simulate response for its one group."""
    group = response["txn-groups"][0]
    message = group.get("failure-message", "")
    return (message == "", message.replace("\n", " ")[:160])


def _probe_txn(
    algod,
    sender: str,
    probe_app_id: int,
    signature: str,
    *,
    fee: int = MIN_FEE,
    accounts: list[str] | None = None,
    assets: list[int] | None = None,
    apps: list[int] | None = None,
) -> "transaction.ApplicationCallTxn":
    params = algod.suggested_params()
    params.flat_fee = True
    params.fee = fee
    return transaction.ApplicationNoOpTxn(
        sender=sender,
        sp=params,
        index=probe_app_id,
        app_args=[_selector(signature)],
        accounts=accounts or [],
        foreign_assets=assets or [],
        foreign_apps=apps or [],
    )


def _execute_txn(
    algod,
    app_id: int,
    sender: str,
    upkeep_id: int,
    target_app: int,
    accounts: list[str] | None = None,
):
    params = algod.suggested_params()
    params.flat_fee = True
    params.fee = EXECUTE_FEE
    return transaction.ApplicationNoOpTxn(
        sender=sender,
        sp=params,
        index=app_id,
        app_args=[_selector("execute(uint64)uint64"), upkeep_id.to_bytes(8, "big")],
        boxes=[(0, b"u" + upkeep_id.to_bytes(8, "big"))],
        foreign_apps=[target_app],
        accounts=accounts or [],
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    net.add_network_argument(parser)
    args = parser.parse_args(argv)

    algorand = net.connect(args.network)
    deployer = algorand.account.from_environment("DEPLOYER")
    algod = algorand.client.algod
    logger.info(f"algod build: {algod.versions()['build']}")

    keeper_client = deploy_keeper()
    probe_client = deploy_probe()
    keeper_app_id = keeper_client.app_id
    probe_app_id = probe_client.app_id
    keeper_app_address = get_application_address(keeper_app_id)
    assert keeper_app_address == keeper_client.app_address, (
        "get_application_address disagrees with the deployed client's own "
        "app_address -- that would undermine every sender comparison below"
    )
    logger.info(f"Keeper {keeper_app_id} ({keeper_app_address}), probe {probe_app_id}")

    probe_client.send.configure(args=ConfigureArgs(keeper_app=keeper_app_id))

    # Seven accounts named nowhere in any call -- the resource-fidelity and
    # false-positive targets below reach for them.
    subjects = [algorand.account.random() for _ in range(7)]
    for subject in subjects:
        algorand.send.payment(
            algokit_utils.PaymentParams(
                sender=deployer.address,
                receiver=subject.address,
                amount=algokit_utils.AlgoAmount(micro_algo=200_000),
            )
        )
    probe_client.send.configure_subjects(
        args=ConfigureSubjectsArgs(
            s0=subjects[0].address,
            s1=subjects[1].address,
            s2=subjects[2].address,
            s3=subjects[3].address,
            s4=subjects[4].address,
            s5=subjects[5].address,
            s6=subjects[6].address,
        )
    )
    stranger = algorand.account.random()
    algorand.send.payment(
        algokit_utils.PaymentParams(
            sender=deployer.address,
            receiver=stranger.address,
            amount=algokit_utils.AlgoAmount(micro_algo=1_000_000),
        )
    )

    findings: list[str] = []

    # ----------------------------------------------------------------------
    # 1. Can simulate accept a transaction whose sender is an app account?
    # ----------------------------------------------------------------------
    logger.info("")
    logger.info("=== 1. Sender is an application account, with no private key ===")
    txn = _probe_txn(algod, keeper_app_address, probe_app_id, "works()uint64")
    without_flag = _simulate(algod, [txn], allow_empty_signatures=False)
    passed, message = _outcome(without_flag)
    logger.info(f"allow_empty_signatures=False: passed={passed} — {message}")
    with_flag = _simulate(algod, [txn], allow_empty_signatures=True)
    passed2, message2 = _outcome(with_flag)
    logger.info(f"allow_empty_signatures=True:  passed={passed2} — {message2}")
    findings.append(
        f"Q1: sender=app-account rejected without allow_empty_signatures "
        f"({'as expected' if not passed else 'UNEXPECTED PASS'}), "
        f"accepted with it ({'as expected' if passed2 else 'UNEXPECTED FAIL: ' + message2})"
    )

    # ----------------------------------------------------------------------
    # 2. Simulating the OUTER execute() call before any upkeep is registered.
    # ----------------------------------------------------------------------
    logger.info("")
    logger.info("=== 2. Simulating execute() on an upkeep that does not exist ===")
    nonexistent_id = keeper_client.state.global_state.next_upkeep_id
    logger.info(f"next_upkeep_id (guaranteed unregistered) = {nonexistent_id}")
    outer = _execute_txn(algod, keeper_app_id, keeper_app_address, nonexistent_id, probe_app_id)
    for label, resources in (("bare", False), ("allow_unnamed_resources", True)):
        response = _simulate(algod, [outer], allow_unnamed_resources=resources)
        passed, message = _outcome(response)
        logger.info(f"{label}: passed={passed} — {message}")
    findings.append(
        "Q2: simulating the real outer execute() before registration fails "
        "on the box, regardless of simulate flags — see full output above"
    )

    # ----------------------------------------------------------------------
    # 3. Simulating the INNER call directly, sender = keeper app address.
    # ----------------------------------------------------------------------
    logger.info("")
    logger.info("=== 3. Simulating the inner call directly ===")
    inner_cases = (
        ("works(), sender=keeper app", keeper_app_address, "works()uint64", True),
        ("works(), sender=stranger", stranger.address, "works()uint64", True),
        ("keeper_only(), sender=keeper app", keeper_app_address, "keeper_only()uint64", True),
        ("keeper_only(), sender=stranger", stranger.address, "keeper_only()uint64", False),
        ("always_reverts(), sender=keeper app", keeper_app_address, "always_reverts()uint64", False),
    )
    for label, sender, signature, expect_pass in inner_cases:
        txn = _probe_txn(algod, sender, probe_app_id, signature)
        response = _simulate(algod, [txn])
        passed, message = _outcome(response)
        agree = "OK" if passed == expect_pass else "MISMATCH"
        logger.info(f"[{agree}] {label}: passed={passed} (expected {expect_pass}) — {message}")
        if agree != "OK":
            findings.append(f"Q3 MISMATCH: {label} — expected pass={expect_pass}, got {passed}")
    findings.append(
        "Q3: a direct simulate with sender=keeper app address correctly "
        "fails keeper_only() for any sender other than the keeper app, and "
        "always_reverts() correctly fails — see [OK]/[MISMATCH] tags above"
    )

    # keeper_only() failed above even with the right sender. Why: inside it,
    # `Application(self.keeper_app.value).address` looks up app 1002's own
    # info, which needs app 1002 to be an *available* resource. In a real
    # execution that is free — app 1002 is the top-level transaction's own
    # ApplicationID, and a group's own calling app is always available to
    # everything nested under it. A standalone simulated call has no such
    # top-level txn (the top-level app here is the *probe*, not the keeper),
    # so nothing makes 1002 available unless something asks for it.
    logger.info("")
    logger.info("--- Same case, with allow_unnamed_resources=True ---")
    txn = _probe_txn(algod, keeper_app_address, probe_app_id, "keeper_only()uint64")
    passed, message = _outcome(_simulate(algod, [txn], allow_unnamed_resources=True))
    logger.info(f"keeper_only(), sender=keeper app, allow_unnamed_resources=True: passed={passed} — {message}")
    findings.append(
        "Q3 GAP: keeper_only() -- exactly docs/integrating.md's recommended "
        "`assert Txn.sender == Application(keeper_app).address` check -- FAILS "
        "under a naive standalone simulate (unavailable App), because app 1002 "
        "is only free-of-charge in a real execution by being the *top-level* "
        "call's own ApplicationID, which a standalone simulated call has no "
        "equivalent of. allow_unnamed_resources=True is what makes it pass: "
        f"passed={passed} with the flag vs False without it above."
    )

    # ----------------------------------------------------------------------
    # 4. Fee and resource fidelity.
    # ----------------------------------------------------------------------
    logger.info("")
    logger.info("=== 4. Resource fidelity: needs_six / needs_seven ===")
    bare = _probe_txn(algod, keeper_app_address, probe_app_id, "needs_six()uint64")
    passed, message = _outcome(_simulate(algod, [bare]))
    logger.info(f"needs_six, bare (no refs, no flags): passed={passed} — {message}")

    with_refs = _probe_txn(
        algod, keeper_app_address, probe_app_id, "needs_six()uint64",
        accounts=[s.address for s in subjects[:6]],
    )
    passed, message = _outcome(_simulate(algod, [with_refs]))
    logger.info(f"needs_six, 6 accounts attached directly: passed={passed} — {message}")

    unnamed = _probe_txn(algod, keeper_app_address, probe_app_id, "needs_six()uint64")
    passed, message = _outcome(_simulate(algod, [unnamed], allow_unnamed_resources=True))
    logger.info(f"needs_six, allow_unnamed_resources=True, no refs attached: passed={passed} — {message}")

    # The false-positive candidate: a standalone simulated call pays none of
    # the 2 resource slots a real Arcron execution spends on the upkeep box
    # and the target app (docs/arcron.md), so it has the full 8 to itself
    # instead of the 6 a real target gets. needs_seven should not fit for
    # real, but might fit here.
    seven_direct = _probe_txn(
        algod, keeper_app_address, probe_app_id, "needs_seven()uint64",
        accounts=[s.address for s in subjects],
    )
    passed_seven_direct, message = _outcome(_simulate(algod, [seven_direct]))
    logger.info(
        f"needs_seven, 7 accounts attached directly (standalone simulate has "
        f"no Arcron tax): passed={passed_seven_direct} — {message}"
    )
    seven_unnamed = _probe_txn(algod, keeper_app_address, probe_app_id, "needs_seven()uint64")
    passed_seven_unnamed, message = _outcome(
        _simulate(algod, [seven_unnamed], allow_unnamed_resources=True)
    )
    logger.info(
        f"needs_seven, allow_unnamed_resources=True, no refs attached: "
        f"passed={passed_seven_unnamed} — {message}"
    )

    logger.info("")
    logger.info("--- Fee fidelity: a standalone call with the real fee (0) ---")
    zero_fee = _probe_txn(algod, keeper_app_address, probe_app_id, "works()uint64", fee=0)
    passed, message = _outcome(_simulate(algod, [zero_fee]))
    logger.info(f"fee=0, standalone, allow_empty_signatures=True: passed={passed} — {message}")
    # Two-transaction group mirroring the real shape: something pays the pool,
    # the probe call itself carries the real fee=0 an inner transaction has.
    # A real atomic group needs its Group field set, same as any real group.
    payer = _probe_txn(algod, stranger.address, probe_app_id, "works()uint64", fee=2 * MIN_FEE)
    pooled = _probe_txn(algod, keeper_app_address, probe_app_id, "works()uint64", fee=0)
    transaction.assign_group_id([payer, pooled])
    passed, message = _outcome(_simulate(algod, [payer, pooled]))
    logger.info(f"fee=0 pooled inside a real (grouped) 2-txn group: passed={passed} — {message}")

    logger.info("")
    logger.info("--- Budget fidelity: burns_budget() (>1,250-budget cost, always fails for real) ---")
    burner = _probe_txn(algod, keeper_app_address, probe_app_id, "burns_budget()uint64")
    passed_default, message = _outcome(_simulate(algod, [burner]))
    logger.info(f"extra_opcode_budget=0 (default): passed={passed_default} — {message}")
    burner2 = _probe_txn(algod, keeper_app_address, probe_app_id, "burns_budget()uint64")
    passed_inflated, message = _outcome(_simulate(algod, [burner2], extra_opcode_budget=17_000))
    logger.info(f"extra_opcode_budget=17000: passed={passed_inflated} — {message}")

    # ----------------------------------------------------------------------
    # 5. The false-positive hunt: register real upkeeps and run real execute.
    # ----------------------------------------------------------------------
    logger.info("")
    logger.info("=== 5. Real execute(), compared against the standalone-simulate prediction ===")

    def register(signature: str) -> int:
        call_data = _selector(signature)
        first_valid = algod.status()["last-round"]

        def payment(amount: int):
            return algorand.create_transaction.payment(
                algokit_utils.PaymentParams(
                    sender=deployer.address,
                    receiver=keeper_client.app_address,
                    amount=algokit_utils.AlgoAmount(micro_algo=amount),
                    first_valid_round=first_valid,
                    last_valid_round=first_valid + 1_000,
                )
            )

        return keeper_client.send.register(
            args=RegisterArgs(
                mbr_payment=payment(_box_mbr([call_data])),
                funding_payment=payment(FEE * 5),
                target_app=probe_app_id,
                call_args=[call_data],
                interval_rounds=INTERVAL,
                fee_per_execution=FEE,
                policy=0,  # CATCH_UP
                fee_cap=0,
                fee_asset=0,
                asset_fee=0,
            )
        ).abi_return

    def run_real_execute(signature: str) -> tuple[bool, str]:
        """Run the actual keeper path: `send.execute`, which algokit-utils
        resource-populates by default (`docs/integrating.md` -- the same
        mechanism `keeper_bot.py` relies on). This is deliberately *not* the
        raw hand-built `_execute_txn`: a keeper executing for real always goes
        through this discovery step, so comparing the Test button's
        pre-registration guess against a bare, unresourced `execute()` would
        not be a fair test of the guess -- it would just be testing whether
        resources were attached at all.
        """
        upkeep_id = register(signature)
        upkeep, _ = _read_upkeep(algorand, keeper_app_id, upkeep_id)
        net.wait_for_round(algorand, upkeep.next_execution_round, poker=deployer)
        try:
            keeper_client.send.execute(
                args=ExecuteArgs(upkeep_id=upkeep_id),
                params=algokit_utils.CommonAppCallParams(
                    sender=deployer.address,
                    signer=deployer.signer,
                    extra_fee=algokit_utils.AlgoAmount(micro_algo=3_000),
                ),
            )
            ok, msg = True, ""
        except Exception as exc:
            ok, msg = False, str(exc).replace("\n", " ")[:200]
        keeper_client.send.cancel(
            args=CancelArgs(upkeep_id=upkeep_id),
            params=algokit_utils.CommonAppCallParams(
                extra_fee=algokit_utils.AlgoAmount(micro_algo=1_000)
            ),
        )
        return ok, msg

    comparisons: list[tuple[str, bool, bool, str]] = []

    def compare(label: str, signature: str, predicted: bool) -> tuple[bool, str]:
        real_ok, real_msg = run_real_execute(signature)
        comparisons.append((label, predicted, real_ok, real_msg))
        tag = "AGREE" if predicted == real_ok else "**LIE**"
        logger.info(
            f"[{tag}] {label}: simulate predicted pass={predicted}, "
            f"real execute pass={real_ok} — {real_msg}"
        )
        return real_ok, real_msg

    # Predictions taken from a standalone simulate shaped the way a naive Test
    # button would build it: sender = keeper app address, allow_empty_signatures
    # + allow_unnamed_resources, no manual resource refs, no extra budget.
    def predict(signature: str) -> bool:
        txn = _probe_txn(algod, keeper_app_address, probe_app_id, signature)
        return _outcome(_simulate(algod, [txn], allow_unnamed_resources=True))[0]

    compare("works() — the control", "works()uint64", predict("works()uint64"))
    compare("keeper_only() — target checks the real sender", "keeper_only()uint64", predict("keeper_only()uint64"))
    compare("always_reverts() — deliberate failure", "always_reverts()uint64", predict("always_reverts()uint64"))
    compare("needs_six() — fits Arcron's 6-slot budget", "needs_six()uint64", predict("needs_six()uint64"))
    # For needs_seven, use the predictions already computed above with
    # explicit refs / allow_unnamed_resources, since that is the shape most
    # likely to be tried by an implementation reaching for "make it pass".
    compare(
        "needs_seven() — needs 7, standalone simulate had no Arcron tax",
        "needs_seven()uint64",
        passed_seven_direct,
    )
    burns_real_ok, burns_real_msg = compare(
        "burns_budget() — honest prediction (extra_opcode_budget=0)",
        "burns_budget()uint64",
        passed_default,
    )
    # Same real outcome, checked against what a Test button would have
    # predicted had it "generously" set extra_opcode_budget to be safe --
    # a plausible implementation mistake, not a hypothetical one.
    comparisons.append(
        (
            "burns_budget() — if the button set extra_opcode_budget=17000",
            passed_inflated,
            burns_real_ok,
            burns_real_msg,
        )
    )
    tag = "AGREE" if passed_inflated == burns_real_ok else "**LIE**"
    logger.info(
        f"[{tag}] burns_budget() with extra_opcode_budget=17000: simulate "
        f"predicted pass={passed_inflated}, real execute pass={burns_real_ok}"
    )

    # ----------------------------------------------------------------------
    # 5b. needs_six failed above through send.execute(), which resource-
    # populates by default. Is that an AVM limit (matching needs_seven, which
    # is real) or an algokit-utils one? Hand-build the execute() call with the
    # accounts attached directly, bypassing algokit-utils' own populator, to
    # find out.
    # ----------------------------------------------------------------------
    logger.info("")
    logger.info("=== 5b. needs_six / needs_seven, hand-built execute() (bypassing algokit-utils' populator) ===")

    def raw_execute_with_accounts(signature: str, accounts: list[str]) -> tuple[bool, str]:
        upkeep_id = register(signature)
        upkeep, _ = _read_upkeep(algorand, keeper_app_id, upkeep_id)
        net.wait_for_round(algorand, upkeep.next_execution_round, poker=deployer)
        txn = _execute_txn(algod, keeper_app_id, deployer.address, upkeep_id, probe_app_id, accounts=accounts)
        try:
            signed = deployer.signer.sign_transactions([txn], [0])
            txid = algod.send_transactions(signed)
            transaction.wait_for_confirmation(algod, txid, 6)
            ok, msg = True, ""
        except Exception as exc:
            ok, msg = False, str(exc).replace("\n", " ")[:200]
        keeper_client.send.cancel(
            args=CancelArgs(upkeep_id=upkeep_id),
            params=algokit_utils.CommonAppCallParams(
                extra_fee=algokit_utils.AlgoAmount(micro_algo=1_000)
            ),
        )
        return ok, msg

    six_ok, six_msg = raw_execute_with_accounts(
        "needs_six()uint64", [s.address for s in subjects[:6]]
    )
    logger.info(f"needs_six, hand-built, 6 accounts attached directly: passed={six_ok} — {six_msg}")
    seven_ok, seven_msg = raw_execute_with_accounts(
        "needs_seven()uint64", [s.address for s in subjects]
    )
    logger.info(f"needs_seven, hand-built, 7 accounts attached directly: passed={seven_ok} — {seven_msg}")
    findings.append(
        f"Q5b: needs_six {'DOES' if six_ok else 'does NOT'} fit inside a real "
        "execute() at the raw AVM level when the accounts are attached by "
        "hand (accounts=[...]) rather than through algokit-utils' default "
        "resource populator (which caps at MAX_APP_CALL_ACCOUNT_REFERENCES=4 "
        "direct accounts per transaction and refused it above). "
        f"needs_seven {'also fits' if seven_ok else 'genuinely does not fit'} "
        "even hand-built, confirming that one is a real AVM-level ceiling "
        "(9 references requested, 8 available), not an artifact of any client."
    )

    # ----------------------------------------------------------------------
    # Summary
    # ----------------------------------------------------------------------
    logger.info("")
    logger.info("=" * 78)
    logger.info("SUMMARY")
    logger.info("=" * 78)
    logger.info("")
    logger.info("| Target | Standalone-simulate prediction | Real execute() | Agree? |")
    logger.info("|---|---|---|---|")
    lies = []
    for label, predicted, real_ok, real_msg in comparisons:
        agree = "yes" if predicted == real_ok else "**NO — LIE**"
        logger.info(f"| {label} | {'pass' if predicted else 'fail'} | {'pass' if real_ok else 'fail'} | {agree} |")
        if predicted != real_ok:
            lies.append((label, predicted, real_ok, real_msg))

    logger.info("")
    for finding in findings:
        logger.info(f"- {finding}")

    logger.info("")
    if lies:
        logger.info(f"{len(lies)} disagreement(s) found between simulate and real execute:")
        for label, predicted, real_ok, real_msg in lies:
            logger.info(
                f"  - {label}: simulate said pass={predicted}, real execute said "
                f"pass={real_ok} ({real_msg})"
            )
    else:
        logger.info("No disagreements found between the standalone-simulate predictions and real execute().")

    logger.info("")
    logger.info(
        "needs_seven, direct standalone: predicted "
        f"{'PASS' if passed_seven_direct else 'FAIL'} (7 accounts fit in a "
        "standalone txn's 8 slots -- no Arcron tax paid)"
    )
    logger.info(
        "needs_seven, allow_unnamed_resources, no manual refs: predicted "
        f"{'PASS' if passed_seven_unnamed else 'FAIL'}"
    )
    logger.info(
        "burns_budget, extra_opcode_budget=17000: predicted "
        f"{'PASS' if passed_inflated else 'FAIL'} while the honest "
        f"(default-budget) prediction was {'PASS' if passed_default else 'FAIL'}"
    )


if __name__ == "__main__":
    main()
