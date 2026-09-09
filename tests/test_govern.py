"""The governance commands pay the network minimum, whatever the node advises.

`govern update` and `govern freeze` sign in process when a single key is the
creator, and `govern create` writes a file for a multisig. Until 2026-09-08 all
three built their transaction from `algod.suggested_params()` as the node
returned it, and `MAX_SIGNABLE_FEE` was applied only when a holder signed a
file (issue #250 F14). These tests drive each path against a node whose fee
advice is wrong and check that the fee it pays is the minimum, or that it pays
nothing at all.
"""

from __future__ import annotations

import base64
import pathlib
from types import SimpleNamespace

import pytest
from algosdk import account, transaction

from scripts import govern
from scripts.verify_build import _digest, _programs, _spec

APP_ID = 4242


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode()


class FakeAlgod:
    """A node serving one app, with the fee advice a test chooses.

    `fee` is per byte and `min_fee` is flat, which is the shape a real node
    answers with: a fake that said `flat_fee=True, fee=1000` would make an
    unbounded update cost the minimum too, and the test could not tell.
    """

    def __init__(self, *, approval: bytes, clear: bytes, frozen: int = 0, fee: int = 0, min_fee: int = 1000):
        self.approval, self.clear, self.frozen = approval, clear, frozen
        self.fee, self.min_fee = fee, min_fee
        self.sent: list = []

    def suggested_params(self):
        return transaction.SuggestedParams(fee=self.fee, first=1, last=1000, gh="", gen="testnet-v1.0",
                                           flat_fee=False, min_fee=self.min_fee)

    def application_info(self, app_id: int) -> dict:
        return {"params": {
            "creator": "unused",
            "approval-program": _b64(self.approval),
            "clear-state-program": _b64(self.clear),
            "global-state": [{"key": _b64(b"frozen"), "value": {"type": 2, "uint": self.frozen}}],
        }}

    def send_transaction(self, signed) -> str:
        self.sent.append(signed)
        # The update lands: the node now serves what was sent.
        self.approval = signed.transaction.approval_program
        self.clear = signed.transaction.clear_program
        return "TXID"


def _algorand(algod, address: str, private_key: str, calls: list | None = None):
    calls = [] if calls is None else calls

    def app_client(**kwargs):
        def call(params):
            calls.append(params)
            algod.frozen = 1
            return None

        return SimpleNamespace(send=SimpleNamespace(call=call))

    return SimpleNamespace(
        client=SimpleNamespace(algod=algod, get_app_client_by_id=app_client),
        account=SimpleNamespace(from_environment=lambda name: SimpleNamespace(address=address, private_key=private_key)),
        calls=calls,
    )


@pytest.fixture
def single_key(monkeypatch):
    monkeypatch.setattr(govern.ms, "configured", lambda: False)
    monkeypatch.setattr(govern.transaction, "wait_for_confirmation", lambda client, txid, rounds: {})
    return monkeypatch


def _tree() -> tuple[bytes, bytes]:
    return _programs(_spec("keeper"))


# --- update, single key --------------------------------------------------------


def test_update_pays_the_minimum_flat_whatever_the_node_advises(single_key) -> None:
    """1,000 per byte on an update of about 2,400 bytes (`estimate_size()`) is about 2.4 ALGO. It pays 1,000."""
    private_key, address = account.generate_account()
    approval, clear = _tree()
    algod = FakeAlgod(approval=b"\x0a\x81\x01", clear=clear, fee=1000, min_fee=1000)
    assert govern.update(_algorand(algod, address, private_key), APP_ID, no_rebuild=True) == 0
    assert len(algod.sent) == 1
    sent = algod.sent[0].transaction
    assert sent.fee == 1000 and sent.sender == address
    assert len(sent.approval_program) > 1000, "so the fee is not size times the advice by coincidence"
    assert _digest(sent.approval_program, sent.clear_program) == _digest(approval, clear)


def test_update_is_refused_before_signing_when_the_node_advises_too_much(single_key, caplog) -> None:
    private_key, address = account.generate_account()
    _, clear = _tree()
    algod = FakeAlgod(approval=b"\x0a\x81\x01", clear=clear, fee=50_000)
    with pytest.raises(govern.FeeRefused, match="not authorization"):
        govern.update(_algorand(algod, address, private_key), APP_ID, no_rebuild=True)
    assert algod.sent == []


def test_a_frozen_app_is_refused_before_the_node_is_asked_about_fees(single_key) -> None:
    private_key, address = account.generate_account()
    _, clear = _tree()
    algod = FakeAlgod(approval=b"\x0a\x81\x01", clear=clear, frozen=1, fee=50_000)
    assert govern.update(_algorand(algod, address, private_key), APP_ID, no_rebuild=True) == 1
    assert algod.sent == []


# --- freeze, single key --------------------------------------------------------


def test_freeze_tells_the_app_client_the_fee_rather_than_letting_it_compute_one(single_key) -> None:
    private_key, address = account.generate_account()
    approval, clear = _tree()
    algod = FakeAlgod(approval=approval, clear=clear, fee=1000)
    single_key.setattr(govern, "_spec_json", lambda: None)
    algorand = _algorand(algod, address, private_key)
    assert govern.freeze(algorand, APP_ID, assume_yes=True) == 0
    assert len(algorand.calls) == 1
    assert algorand.calls[0].method == "freeze"
    assert algorand.calls[0].static_fee.micro_algo == 1000


def test_freeze_is_refused_before_the_operator_is_asked_to_confirm(single_key) -> None:
    private_key, address = account.generate_account()
    approval, clear = _tree()
    algod = FakeAlgod(approval=approval, clear=clear, fee=50_000)
    single_key.setattr("builtins.input", lambda prompt: pytest.fail("asked to confirm a freeze that was refused"))
    algorand = _algorand(algod, address, private_key)
    with pytest.raises(govern.FeeRefused):
        govern.freeze(algorand, APP_ID, assume_yes=False)
    assert algorand.calls == []


# --- the multisig exports ------------------------------------------------------


def test_the_unsigned_freeze_and_update_carry_the_minimum_fee(monkeypatch, tmp_path) -> None:
    """A holder's `sign` would refuse an inflated fee anyway; the file should not carry one."""
    _, holder = account.generate_account()
    monkeypatch.setattr(govern.ms, "configured", lambda: True)
    monkeypatch.setattr(govern.ms, "address", lambda: holder)
    monkeypatch.setattr(govern.ms, "describe", lambda: "a fake 1 of 1")
    written: list = []
    monkeypatch.setattr(govern.ms, "export_unsigned", lambda txn, path: written.append(txn))
    approval, clear = _tree()

    algod = FakeAlgod(approval=approval, clear=clear, fee=1000)
    assert govern.freeze(SimpleNamespace(client=SimpleNamespace(algod=algod)), APP_ID, assume_yes=True,
                         out=tmp_path / "freeze.json") == 0
    algod = FakeAlgod(approval=b"\x0a\x81\x01", clear=clear, fee=1000)
    assert govern.update(SimpleNamespace(client=SimpleNamespace(algod=algod)), APP_ID, no_rebuild=True,
                         out=tmp_path / "update.json") == 0
    assert [txn.fee for txn in written] == [1000, 1000]
    assert all(txn.sender == holder for txn in written)


# --- main prints the node's refusal as a refusal --------------------------------


def test_main_reports_a_fee_refusal_without_a_traceback(single_key, caplog) -> None:
    private_key, address = account.generate_account()
    _, clear = _tree()
    algod = FakeAlgod(approval=b"\x0a\x81\x01", clear=clear, fee=50_000)
    single_key.setattr(govern.net, "connect", lambda network: _algorand(algod, address, private_key))
    with caplog.at_level("ERROR"):
        assert govern.main(["update", "--network", "testnet", "--app-id", str(APP_ID), "--no-rebuild"]) == 1
    assert "Refusing:" in caplog.text and str(govern.MAX_SIGNABLE_FEE) in caplog.text
    assert algod.sent == []


def test_the_ceiling_is_the_one_the_multisig_sign_path_already_used() -> None:
    """One constant, two paths: the file a holder signs and the shell that signs in process."""
    assert govern.MAX_SIGNABLE_FEE == 10_000
    assert issubclass(govern.FeeRefused, RuntimeError), "deploy.create's caller catches RuntimeError"


# --- the inventory in bounded_params' docstring cannot drift silently ----------

#: The signers the docstring says are bounded, and the LocalNet instruments it
#: says are unbounded by design. A new script that signs has to be added to
#: one of these lists and to the docstring, or this fails.
BOUNDED = {"deploy", "govern", "multisig", "seed_registry", "keeper_topup", "keeper_sweep", "reclaim", "keeper_bot"}
INSTRUMENTS = {
    "keeper/deploy_config", "pulse/deploy_config", "resource_probe/deploy_config", "sim_probe/deploy_config",
    "keeper_e2e", "govern_e2e", "multisig_e2e", "clawback_e2e", "subscription_demo",
    "keeper_soak", "keeper_race", "scenario", "attacks", "reference_boundary",
    "spike_asa_fee", "spike_hostile_target", "spike_js_execute_resources", "spike_multiarg",
    "spike_quantum", "spike_reentrancy", "spike_resources", "spike_simulate_test_button",
}


def _signers() -> set[str]:
    """Every script under scripts/ that takes a signing account from the environment.

    `account.from_environment(` rather than `from_environment("DEPLOYER")`, so
    `keeper_topup`'s `--account` and the bot's `KEEPER` are counted too;
    `AlgorandClient.from_environment()` in network.py is a client, not a key,
    and does not match.
    """
    root = pathlib.Path(govern.__file__).resolve().parent
    # Three ways a script here turns a secret into a signature: an account
    # from the environment, a mnemonic decoded on the spot (the multisig
    # holders' `sign`), or `from_mnemonic`. A fourth would need adding here.
    marks = ("account.from_environment(", "mnemonic.to_private_key(", "from_mnemonic(")
    found = {path.stem for path in root.glob("*.py") if any(m in path.read_text() for m in marks)}
    # `keeper_daemon` decodes the mnemonic only to print the address it
    # derives, and signs nothing; the one match here that is not a signer.
    found.discard("keeper_daemon")
    # algokit's deploy path lives beside each contract, signs as DEPLOYER too,
    # and refuses MainNet by genesis id itself; named so a new one is noticed.
    contracts = root.parent / "smart_contracts"
    found |= {
        f"{path.parent.name}/deploy_config"
        for path in contracts.glob("*/deploy_config.py")
        if any(m in path.read_text() for m in marks)
    }
    return found


def test_every_signing_script_is_in_the_inventory() -> None:
    signers = _signers()
    assert signers, "the grep found nothing, so it is looking in the wrong place"
    unlisted = signers - BOUNDED - INSTRUMENTS
    assert not unlisted, f"scripts that sign and are in neither list: {sorted(unlisted)}"
    stale = (BOUNDED | INSTRUMENTS) - signers - {"keeper_sweep"}  # sweep is handed its account by the bot
    assert not stale, f"listed but no longer signing: {sorted(stale)}"


def test_the_docstring_names_every_script_in_both_lists() -> None:
    doc = govern.bounded_params.__doc__
    for name in BOUNDED | INSTRUMENTS:
        assert name.split("/")[-1] in doc, f"{name} signs and the inventory does not name it"
    assert "ARCRON_ALLOW_MAINNET" in doc and ".env.mainnet" in doc and "load_network" in doc
