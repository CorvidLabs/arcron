"""The create ceremony checks what it displays, on every network.

`scripts/deploy.py` is the command that makes the MainNet app, and a create
fixes the creator, the extra pages and both schemas forever. Before this file
existed the script had guards for two of the permanent mistakes (wrong creator,
wrong genesis) and tests for neither; the rest it printed after the fact or
not at all. Every refusal below is pinned at the decision, and the read-back
is pinned against a chain that lies.
"""

from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace

import pytest
from algosdk import account, transaction

from scripts import deploy, network as net

CORVID = net.MAINNET_CREATOR
# A real TestNet throwaway shape: valid checksum, not the MainNet creator.
STRANGER = "E5M2OH5XNDMNABJ6VOFOUVR2IKRPCGQH43PVC5P3DWQQ2LV2VJV2FJZQ3E"

CLEAN = deploy.TreeState(commit="a" * 40, tag="mainnet-1", dirty=False)
UNTAGGED = deploy.TreeState(commit="a" * 40, tag=None, dirty=False)
DIRTY = deploy.TreeState(commit="a" * 40, tag="mainnet-1", dirty=True)


def plan(network: str = net.MAINNET, creator: str = CORVID, tree: deploy.TreeState = CLEAN):
    genesis = {net.MAINNET: "mainnet-v1.0", net.TESTNET: "testnet-v1.0"}.get(network, "dockernet-v1")
    return deploy.plan_for(network, genesis, creator, tree)


def refuse(p, **overrides) -> list[str]:
    options = dict(existing_keepers=[], allow_dirty=False, allow_another=False, mnemonic_on_disk=False)
    options.update(overrides)
    return deploy.refusals(p, **options)


# --- the shape, derived rather than typed ------------------------------------


def test_the_shape_is_what_the_docs_promise() -> None:
    """docs/deploying.md says two pages, two global uints, no local state.

    Derived from the committed ARC-56 spec here, the same derivation `govern
    create` uses, so a build that grew a global or crossed a page boundary
    fails this before it can fail a create.
    """
    p = plan()
    assert p.shape == {
        "extra pages": 1,
        "global uints": 2,
        "global byte slices": 0,
        "local uints": 0,
        "local byte slices": 0,
    }
    assert len(p.approval) > deploy.PROGRAM_PAGE, "one page would mean zero extra pages"


def test_build_create_carries_exactly_the_described_shape() -> None:
    p = plan()
    params = transaction.SuggestedParams(fee=1000, first=1, last=1000, gh="", gen="mainnet-v1.0", flat_fee=True)
    txn = deploy.build_create(p, params)
    assert txn.sender == CORVID
    assert txn.extra_pages == 1
    assert (txn.global_schema.num_uints, txn.global_schema.num_byte_slices) == (2, 0)
    # algosdk drops an all-zero schema to None on the wire; both spell (0, 0).
    local = txn.local_schema
    assert local is None or (local.num_uints, local.num_byte_slices) == (0, 0)
    assert txn.approval_program == p.approval and txn.clear_program == p.clear
    assert txn.on_complete == transaction.OnComplete.NoOpOC


# --- refusals, each at the decision ------------------------------------------


def test_a_clean_tagged_mainnet_create_from_the_creator_is_not_refused() -> None:
    assert refuse(plan()) == []


def test_a_dirty_tree_is_refused_everywhere() -> None:
    reasons = refuse(plan(net.TESTNET, STRANGER, DIRTY))
    assert any("uncommitted" in r for r in reasons), reasons


def test_allow_dirty_is_honoured_off_mainnet_only() -> None:
    assert refuse(plan(net.TESTNET, STRANGER, DIRTY), allow_dirty=True) == []
    reasons = refuse(plan(net.MAINNET, CORVID, DIRTY), allow_dirty=True)
    assert any("never created from a dirty tree" in r for r in reasons), reasons


def test_mainnet_is_created_from_a_tag() -> None:
    reasons = refuse(plan(tree=UNTAGGED))
    assert any("tag" in r for r in reasons), reasons
    # TestNet may rehearse from any commit.
    assert refuse(plan(net.TESTNET, STRANGER, UNTAGGED)) == []


def test_mainnet_refuses_any_creator_but_corvid_algo() -> None:
    reasons = refuse(plan(creator=STRANGER))
    assert any(CORVID in r and STRANGER in r for r in reasons), reasons


def test_mainnet_refuses_a_mnemonic_written_to_disk() -> None:
    reasons = refuse(plan(), mnemonic_on_disk=True)
    assert any("DEPLOYER_MNEMONIC is written" in r for r in reasons), reasons
    assert refuse(plan(net.TESTNET, STRANGER), mnemonic_on_disk=True) == []


def test_a_second_keeper_needs_saying_so_and_mainnet_never_gets_one() -> None:
    reasons = refuse(plan(net.TESTNET, STRANGER), existing_keepers=[769891898])
    assert any("--another" in r for r in reasons), reasons
    assert refuse(plan(net.TESTNET, STRANGER), existing_keepers=[769891898], allow_another=True) == []
    reasons = refuse(plan(), existing_keepers=[1], allow_another=True)
    assert any("one MainNet keeper" in r for r in reasons), reasons


def test_every_reason_is_reported_at_once() -> None:
    """An operator fixes one thing and reruns; four refusals one at a time is four reruns."""
    reasons = refuse(plan(creator=STRANGER, tree=deploy.TreeState("b" * 40, None, True)),
                     existing_keepers=[7], mnemonic_on_disk=True)
    assert len(reasons) >= 5, reasons


# --- what is displayed ---------------------------------------------------------


def test_describe_names_every_permanent_field() -> None:
    text = "\n".join(deploy.describe(plan()))
    for needle in (CORVID, "mainnet-v1.0", "extra pages   1", "global state  2 uints, 0 byte slices",
                   "local state   0 uints, 0 byte slices", "sha256", "a" * 40, "mainnet-1",
                   "0.1 ALGO of minimum balance per program page"):
        assert needle in text, needle


def test_describe_flags_a_dirty_tree_and_a_missing_tag() -> None:
    text = "\n".join(deploy.describe(plan(net.TESTNET, STRANGER, deploy.TreeState("c" * 40, None, True))))
    assert "(DIRTY)" in text and "(untagged)" in text


# --- the confirmation ----------------------------------------------------------


def test_confirmation_must_be_the_creator_address() -> None:
    assert deploy.confirm(plan(), False, ask=lambda _: CORVID)
    assert deploy.confirm(plan(), False, ask=lambda _: f"  {CORVID}\n")
    assert not deploy.confirm(plan(), False, ask=lambda _: "yes")
    assert not deploy.confirm(plan(), False, ask=lambda _: STRANGER)


def test_yes_is_ignored_on_mainnet_and_honoured_elsewhere() -> None:
    asked = []

    def ask(prompt: str) -> str:
        asked.append(prompt)
        return "nope"

    assert not deploy.confirm(plan(), True, ask=ask)
    assert asked, "MainNet must ask even when --yes is passed"
    assert deploy.confirm(plan(net.TESTNET, STRANGER), True, ask=ask)


# --- the mnemonic on disk ------------------------------------------------------


def test_mnemonic_written_to_reads_the_shape_of_the_line_only(tmp_path: Path) -> None:
    env = tmp_path / ".env.mainnet"
    assert not deploy.mnemonic_written_to(env), "absent file is not a mnemonic"
    env.write_text("ALGOD_SERVER=https://x\n# DEPLOYER_MNEMONIC=\"word1 ...\"\nDEPLOYER_MNEMONIC=\n")
    assert not deploy.mnemonic_written_to(env), "a comment and an empty value are not a mnemonic"
    env.write_text("ALGOD_SERVER=https://x\nDEPLOYER_MNEMONIC=\"abandon abandon ...\"\n")
    assert deploy.mnemonic_written_to(env)
    env.write_text("export DEPLOYER_MNEMONIC=abandon\n")
    assert deploy.mnemonic_written_to(env)


# --- finding a creator's existing keepers -------------------------------------


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode()


def _created(app_id: int, *, keys: tuple[bytes, ...] = (), approval: bytes = b"\x0a", clear: bytes = b"\x0a") -> dict:
    return {
        "id": app_id,
        "params": {
            "approval-program": _b64(approval),
            "clear-state-program": _b64(clear),
            "global-state": [{"key": _b64(k), "value": {"type": 2, "uint": 0}} for k in keys],
        },
    }


class FakeAlgod:
    def __init__(self, *, gen: str = "testnet-v1.0", created: list[dict] | None = None, app: dict | None = None):
        self.gen = gen
        self.created = created or []
        self.app = app
        self.sent: list = []

    def suggested_params(self):
        return transaction.SuggestedParams(fee=1000, first=1, last=1000, gh="", gen=self.gen, flat_fee=True)

    def account_info(self, address: str) -> dict:
        return {"amount": 10_000_000, "min-balance": 100_000, "created-apps": self.created}

    def application_info(self, app_id: int) -> dict:
        assert self.app is not None
        return self.app

    def simulate_transactions(self, request) -> dict:
        return {"txn-groups": [{}]}

    def send_transaction(self, signed) -> str:
        self.sent.append(signed)
        return "TXID"


def test_find_keepers_recognises_a_keeper_by_its_state_or_its_bytecode() -> None:
    p = plan(net.TESTNET, STRANGER)
    algod = FakeAlgod(created=[
        _created(3, keys=(deploy.KEEPER_MARKER, b"frozen")),
        _created(1, approval=p.approval, clear=p.clear),
        _created(2, keys=(b"beats",)),
    ])
    assert deploy.find_keepers(algod, STRANGER, p.digest) == [1, 3]


def test_an_account_with_no_apps_has_no_keepers() -> None:
    assert deploy.find_keepers(FakeAlgod(), STRANGER, None) == []


# --- reading it back -----------------------------------------------------------


def _live(p, *, creator: str | None = None, pages: int = 1, g=(2, 0), l=(0, 0),
          approval: bytes | None = None, frozen: int | None = 0) -> dict:
    state = [] if frozen is None else [{"key": _b64(b"frozen"), "value": {"type": 2, "uint": frozen}}]
    return {"params": {
        "creator": creator or p.creator,
        "extra-program-pages": pages,
        "global-state-schema": {"num-uint": g[0], "num-byte-slice": g[1]},
        "local-state-schema": {"num-uint": l[0], "num-byte-slice": l[1]},
        "approval-program": _b64(approval if approval is not None else p.approval),
        "clear-state-program": _b64(p.clear),
        "global-state": state,
    }}


def test_postflight_is_silent_when_the_chain_matches() -> None:
    p = plan()
    assert deploy.postflight(FakeAlgod(app=_live(p)), 1, p) == []


def test_postflight_names_every_mismatch() -> None:
    """A chain that disagrees on all five fields produces five lines, not one."""
    p = plan()
    lying = _live(p, creator=STRANGER, pages=2, g=(3, 1), l=(1, 0), approval=b"\x0a\x81\x01", frozen=1)
    mismatches = deploy.postflight(FakeAlgod(app=lying), 1, p)
    text = "\n".join(mismatches)
    for needle in ("creator is", "extra program pages is 2", "global schema is (3, 1)",
                   "local schema is (1, 0)", "not the ones this tree builds", "frozen is 1"):
        assert needle in text, (needle, mismatches)
    assert len(mismatches) == 6


def test_postflight_treats_a_missing_frozen_key_as_a_mismatch() -> None:
    p = plan()
    mismatches = deploy.postflight(FakeAlgod(app=_live(p, frozen=None)), 1, p)
    assert mismatches == ["frozen is -1, expected 0 on a fresh deployment"]


# --- main, end to end against a fake node --------------------------------------


def _algorand(algod: FakeAlgod, deployer_address: str, private_key: str):
    payments: list = []
    return SimpleNamespace(
        client=SimpleNamespace(algod=algod),
        account=SimpleNamespace(from_environment=lambda name: SimpleNamespace(address=deployer_address, private_key=private_key)),
        send=SimpleNamespace(payment=lambda params: payments.append(params)),
        payments=payments,
    )


@pytest.fixture
def quiet(monkeypatch):
    monkeypatch.setattr(deploy.ms, "configured", lambda: False)
    monkeypatch.setattr(deploy, "mnemonic_written_to", lambda path: False)
    return monkeypatch


def test_main_refuses_the_wrong_creator_on_mainnet_before_anything_is_sent(quiet) -> None:
    private_key, address = account.generate_account()
    algod = FakeAlgod(gen="mainnet-v1.0")
    quiet.setattr(deploy.net, "connect", lambda network: _algorand(algod, address, private_key))
    quiet.setattr(deploy, "tree_state", lambda: CLEAN)
    quiet.setattr("builtins.input", lambda prompt: pytest.fail("asked for confirmation after refusing"))

    assert deploy.main(["--network", "mainnet", "--no-rebuild"]) == 1
    assert algod.sent == []


def test_main_refuses_an_untagged_mainnet_tree_even_from_the_creator(quiet) -> None:
    algod = FakeAlgod(gen="mainnet-v1.0")
    quiet.setattr(deploy.net, "connect", lambda network: _algorand(algod, CORVID, "unused"))
    quiet.setattr(deploy, "tree_state", lambda: UNTAGGED)
    assert deploy.main(["--network", "mainnet", "--no-rebuild"]) == 1
    assert algod.sent == []


def test_main_does_not_create_when_the_confirmation_is_wrong(quiet) -> None:
    private_key, address = account.generate_account()
    algod = FakeAlgod()
    quiet.setattr(deploy.net, "connect", lambda network: _algorand(algod, address, private_key))
    quiet.setattr(deploy, "tree_state", lambda: CLEAN)
    quiet.setattr("builtins.input", lambda prompt: "yes")
    assert deploy.main(["--network", "testnet", "--no-rebuild"]) == 1
    assert algod.sent == []


def test_main_creates_funds_and_reads_back_when_everything_checks_out(quiet) -> None:
    """The happy path, with the chain answering exactly what was promised."""
    private_key, address = account.generate_account()
    algod = FakeAlgod()
    algorand = _algorand(algod, address, private_key)
    quiet.setattr(deploy.net, "connect", lambda network: algorand)
    quiet.setattr(deploy, "tree_state", lambda: CLEAN)

    def confirmed(client, txid, rounds):
        # The read-back needs the app to exist by the time it is asked about.
        algod.app = _live(plan(net.TESTNET, address))
        return {"application-index": 4242}

    quiet.setattr(deploy.transaction, "wait_for_confirmation", confirmed)
    # The app account starts empty, so the floor has to be funded.
    algod.account_info = lambda addr: {"amount": 0 if addr != address else 10_000_000,
                                       "min-balance": 100_000, "created-apps": []}

    assert deploy.main(["--network", "testnet", "--no-rebuild", "--yes"]) == 0
    assert len(algod.sent) == 1, "exactly one create was sent"
    sent = algod.sent[0].transaction
    assert sent.extra_pages == 1 and sent.sender == address
    assert len(algorand.payments) == 1 and algorand.payments[0].amount.micro_algo == deploy.BASE_MBR


def test_main_shouts_when_the_chain_disagrees_after_the_create(quiet) -> None:
    private_key, address = account.generate_account()
    algod = FakeAlgod()
    algorand = _algorand(algod, address, private_key)
    quiet.setattr(deploy.net, "connect", lambda network: algorand)
    quiet.setattr(deploy, "tree_state", lambda: CLEAN)

    def confirmed(client, txid, rounds):
        algod.app = _live(plan(net.TESTNET, address), pages=2)
        return {"application-index": 4242}

    quiet.setattr(deploy.transaction, "wait_for_confirmation", confirmed)
    assert deploy.main(["--network", "testnet", "--no-rebuild", "--yes"]) == 1


def test_main_refuses_a_second_keeper_on_testnet_without_another(quiet) -> None:
    private_key, address = account.generate_account()
    p = plan(net.TESTNET, address)
    algod = FakeAlgod(created=[_created(9, keys=(deploy.KEEPER_MARKER,))])
    quiet.setattr(deploy.net, "connect", lambda network: _algorand(algod, address, private_key))
    quiet.setattr(deploy, "tree_state", lambda: CLEAN)
    assert deploy.main(["--network", "testnet", "--no-rebuild", "--yes"]) == 1
    assert algod.sent == []
    assert p.creator == address


def test_a_simulate_failure_stops_the_create(quiet) -> None:
    private_key, address = account.generate_account()
    algod = FakeAlgod()
    algod.simulate_transactions = lambda request: {"txn-groups": [{"failure-message": "logic eval error: schema"}]}
    quiet.setattr(deploy.net, "connect", lambda network: _algorand(algod, address, private_key))
    quiet.setattr(deploy, "tree_state", lambda: CLEAN)
    assert deploy.main(["--network", "testnet", "--no-rebuild", "--yes"]) == 1
    assert algod.sent == []


# --- what the reviewers found ----------------------------------------------


def test_postflight_compares_the_clear_program_too() -> None:
    """A hostile clear program beside an honest approval must not read back clean."""
    p = plan()
    lying = _live(p)
    lying["params"]["clear-state-program"] = _b64(b"\x0a\x81\x00")
    mismatches = deploy.postflight(FakeAlgod(app=lying), 1, p)
    assert mismatches == ["the deployed programs are not the ones this tree builds"]


def test_main_reads_the_mnemonic_rule_from_the_networks_env_file(monkeypatch, tmp_path: Path) -> None:
    """The wiring, not the helper: main must look at REPO/.env.<network>."""
    (tmp_path / ".env.mainnet").write_text("ALGOD_SERVER=https://x\nDEPLOYER_MNEMONIC=\"abandon abandon\"\n")
    monkeypatch.setattr(deploy, "REPO", tmp_path)
    monkeypatch.setattr(deploy.ms, "configured", lambda: False)
    algod = FakeAlgod(gen="mainnet-v1.0")
    monkeypatch.setattr(deploy.net, "connect", lambda network: _algorand(algod, CORVID, "unused"))
    monkeypatch.setattr(deploy, "tree_state", lambda: CLEAN)
    assert deploy.main(["--network", "mainnet", "--no-rebuild"]) == 1
    assert algod.sent == []


def test_the_network_funnel_refuses_a_mainnet_env_file_carrying_the_key(monkeypatch, tmp_path: Path) -> None:
    """Every MainNet script passes through load_network, so the rule lives there.

    deploy.py used to be the only place that checked, and the ceremony's own
    sequence (create, then register the first upkeep as DEPLOYER) invited the
    shortcut of writing the key into .env.mainnet for the scripts that did not.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARCRON_ALLOW_MAINNET", "1")
    monkeypatch.delenv("DEPLOYER_MNEMONIC", raising=False)
    (tmp_path / ".env.mainnet").write_text("ALGOD_SERVER=https://x\nDEPLOYER_MNEMONIC=abandon abandon\n")
    with pytest.raises(RuntimeError, match="DEPLOYER_MNEMONIC"):
        net.load_network(net.MAINNET)
    assert "DEPLOYER_MNEMONIC" not in __import__("os").environ, "refused before dotenv loaded it"
    # The same file without the line is fine.
    (tmp_path / ".env.mainnet").write_text("ALGOD_SERVER=https://x\nKEEPER_APP_ID=\n")
    assert net.load_network(net.MAINNET) == net.MAINNET


def test_a_bom_does_not_hide_the_mnemonic_line(tmp_path: Path) -> None:
    env = tmp_path / ".env.mainnet"
    env.write_bytes("﻿DEPLOYER_MNEMONIC=abandon\n".encode("utf-8"))
    assert deploy.mnemonic_written_to(env)


def test_the_algokit_deploy_path_refuses_mainnet_for_any_deployer() -> None:
    with pytest.raises(RuntimeError, match="deploy-mainnet"):
        net.refuse_algokit_create_on_mainnet("mainnet-v1.0", "keeper")
    net.refuse_algokit_create_on_mainnet("testnet-v1.0", "keeper")
    net.refuse_algokit_create_on_mainnet("dockernet-v1", "pulse")


def test_pulse_is_planned_from_its_own_spec_and_has_no_freeze_flag() -> None:
    p = plan(net.TESTNET, STRANGER)
    pulse = deploy.plan_for(net.TESTNET, "testnet-v1.0", STRANGER, CLEAN, contract="pulse")
    assert pulse.shape == {"extra pages": 0, "global uints": 2, "global byte slices": 1,
                           "local uints": 0, "local byte slices": 0}
    assert pulse.marker == b"beats" and not pulse.has_freeze and p.has_freeze
    # No frozen key on chain is exactly right for Pulse.
    live = _live(pulse, pages=0, g=(2, 1), frozen=None)
    assert deploy.postflight(FakeAlgod(app=live), 1, pulse) == []


class _TwoAppAlgod(FakeAlgod):
    """Serves application_info per id, so a keeper and a Pulse can be read back."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.apps: dict[int, dict] = {}
        self.next_id = 4242

    def application_info(self, app_id: int) -> dict:
        return self.apps[app_id]


def test_main_creates_pulse_directly_and_reads_it_back(quiet) -> None:
    private_key, address = account.generate_account()
    algod = _TwoAppAlgod()
    algorand = _algorand(algod, address, private_key)
    quiet.setattr(deploy.net, "connect", lambda network: algorand)
    quiet.setattr(deploy, "tree_state", lambda: CLEAN)

    def confirmed(client, txid, rounds):
        app_id = algod.next_id
        algod.next_id += 1
        which = algod.sent[-1].transaction
        contract = "keeper" if which.extra_pages else "pulse"
        p = deploy.plan_for(net.TESTNET, "testnet-v1.0", address, CLEAN, contract=contract)
        algod.apps[app_id] = _live(p, pages=p.extra_pages,
                                   g=(p.shape["global uints"], p.shape["global byte slices"]),
                                   frozen=0 if p.has_freeze else None)
        return {"application-index": app_id}

    quiet.setattr(deploy.transaction, "wait_for_confirmation", confirmed)
    assert deploy.main(["--network", "testnet", "--no-rebuild", "--yes", "--with-pulse"]) == 0
    assert len(algod.sent) == 2, "one keeper create, one pulse create, no indexer"
    assert [t.transaction.extra_pages for t in algod.sent] == [1, 0]


def test_an_existing_pulse_is_refused_before_the_keeper_is_created(quiet) -> None:
    private_key, address = account.generate_account()
    algod = FakeAlgod(created=[_created(77, keys=(b"beats",))])
    quiet.setattr(deploy.net, "connect", lambda network: _algorand(algod, address, private_key))
    quiet.setattr(deploy, "tree_state", lambda: CLEAN)
    assert deploy.main(["--network", "testnet", "--no-rebuild", "--yes", "--with-pulse"]) == 1
    assert algod.sent == [], "the keeper must not be created if the whole request cannot be"


def test_a_create_that_does_not_confirm_is_reported_with_its_txid(quiet, caplog) -> None:
    """The app may exist. A traceback would imply it does not."""
    private_key, address = account.generate_account()
    algod = FakeAlgod()
    quiet.setattr(deploy.net, "connect", lambda network: _algorand(algod, address, private_key))
    quiet.setattr(deploy, "tree_state", lambda: CLEAN)

    def timeout(client, txid, rounds):
        raise Exception("Wait for transaction id TXID timed out")

    quiet.setattr(deploy.transaction, "wait_for_confirmation", timeout)
    with caplog.at_level("ERROR"):
        assert deploy.main(["--network", "testnet", "--no-rebuild", "--yes"]) == 1
    assert "SENT as TXID" in caplog.text and "may well have landed" in caplog.text
    assert len(algod.sent) == 1
