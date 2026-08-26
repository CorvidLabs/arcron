"""Multisig configuration, and the member that can never sign.

The tooling is off-chain, so the threat model is different from the
contracts': the failures here are ones where a configuration looks correct,
derives an address, and quietly behaves differently from what was intended.
"""

import os

import pytest

from scripts import multisig as ms

# Real addresses, chosen because their properties are the point.
LEDGER = "X2OF75PUW34XMTY2QW7ZTXH2XHDREVH4ZRDDHFXJNJHXJEEPSWWB4T73AQ"
CORVID = "WGSHC4TYKYBS6EX5V5E377BQDLKWIIPBCFOLZQZIXCKHFIEKRPBFOMW25A"
HOT = "WOX2O7LDLN74QDQYDJRUHGBLAH3JBEUYAFJO6FQL4P2EXV33VYAR536BBY"
KYN = "EN3QXMNA4CRHIAOIAJUH4TBH2XDC5UF5GVSYFHLHNE7IOPLZYJMHXGG3BI"
GASPAR = "DEXWEZGRX3Q6B2S3GVO74MUN54XA3JI5GQFVGNK64JYPD4NCFRK4G5ACVY"
# A post-quantum Falcon account. A valid Algorand address whose 32 bytes are a
# hash rather than a curve point, so no ed25519 key corresponds to it.
FALCON = "B5JC6CBSTBT4IHX2RC7BC4TJYHJYOUOMRDTMVMOEIEXICAYDFYZCI2SUTY"

SIGNERS = [LEDGER, CORVID, HOT, KYN, GASPAR]


@pytest.fixture()
def configured(monkeypatch):
    def apply(threshold: int, addresses: list[str]) -> None:
        monkeypatch.setenv(ms.THRESHOLD_VAR, str(threshold))
        monkeypatch.setenv(ms.ADDRESSES_VAR, ",".join(addresses))

    return apply


def test_a_falcon_account_is_refused(configured) -> None:
    """The trap worth a test of its own.

    A post-quantum account is a perfectly good Algorand account that can never
    produce an ed25519 subsignature. Nothing complains on its own: the address
    derives normally, and the result reads as a 3 of 5 while behaving as a
    3 of 4. Whoever held two of the remaining four would silently lose the
    ability to act alone.
    """
    configured(3, [FALCON, LEDGER, CORVID, KYN, GASPAR])
    with pytest.raises(RuntimeError, match="cannot take part in a multisig"):
        ms.address()


def test_the_real_signers_are_accepted(configured) -> None:
    configured(3, SIGNERS)
    assert ms.address() == "NHQU7QBDTUC4Q5I7LV3A35GGG36QUK5EL6PM4ZVBJKZ7AS6EDOU7BCRDWA"
    assert ms.describe().startswith("3 of 5 at ")


def test_order_changes_the_address(configured) -> None:
    """Two people comparing sets in different orders would not match."""
    configured(3, SIGNERS)
    one = ms.address()
    configured(3, [SIGNERS[1], SIGNERS[0]] + SIGNERS[2:])
    assert ms.address() != one


def test_an_impossible_threshold_is_refused(configured) -> None:
    configured(6, SIGNERS)
    with pytest.raises(RuntimeError, match="impossible"):
        ms.address()
    configured(0, SIGNERS)
    with pytest.raises(RuntimeError, match="impossible"):
        ms.address()


def test_a_repeated_signer_is_refused(configured) -> None:
    """It would read as 3 of 5 while one holder counted twice."""
    configured(3, [LEDGER, LEDGER, CORVID, KYN, GASPAR])
    with pytest.raises(RuntimeError, match="twice"):
        ms.address()


def test_a_mistyped_address_says_so(configured) -> None:
    configured(2, ["NOTANADDRESS", CORVID])
    with pytest.raises(RuntimeError, match="not an Algorand address"):
        ms.address()


def test_a_threshold_without_signers_is_refused(monkeypatch) -> None:
    monkeypatch.delenv(ms.ADDRESSES_VAR, raising=False)
    monkeypatch.setenv(ms.THRESHOLD_VAR, "2")
    assert ms.configured() is False
    with pytest.raises(RuntimeError, match="is not set"):
        ms.address()


# --- what a signing file is allowed to be ------------------------------
#
# The guard these cover replaced `if in_file and in_file != app_id`. `app_id`
# is 0 for anything that is not an application call, so `and` short-circuited
# the whole check away for exactly the transactions that can take the account
# outright: a rekey, a close, and a create. Each one below is a transaction a
# compromised coordinator could hand five holders who trust `show`.

MAINNET_GENESIS = ("mainnet-v1.0",)
TESTNET_GENESIS = ("testnet-v1.0",)


def _params(genesis_id: str = "testnet-v1.0", fee: int = 1_000):
    from algosdk import transaction

    return transaction.SuggestedParams(
        fee=fee, first=1, last=1_000, gh="SGO1GKSzyE7IEPItTxCByw9x8FmnrCDexi9/cOUJOiI=",
        gen=genesis_id, flat_fee=True,
    )


def _write(tmp_path, txn):
    """Write the file exactly as `export_unsigned` does, so the test exercises
    the real format rather than one invented for the test."""
    return ms.export_unsigned(txn, tmp_path / "txn.json")


def _refusals(path, **overrides):
    kwargs = dict(
        app_id=769891898, genesis_ids=TESTNET_GENESIS,
        expected_address=ms.address(), max_fee=10_000,
    )
    kwargs.update(overrides)
    return ms.refusals(path, **kwargs)


def test_a_rekey_of_the_multisig_is_refused(configured, tmp_path) -> None:
    """The attack the old guard let straight through.

    A rekey hands the whole account to another key permanently, and it is not
    an application call, so `app_id()` returned 0 and the check short-circuited.
    After this lands, `update` and `freeze` belong to whoever was rekeyed to.
    """
    from algosdk import transaction

    configured(3, SIGNERS)
    txn = transaction.PaymentTxn(
        sender=ms.address(), sp=_params(), receiver=ms.address(), amt=0, rekey_to=HOT
    )
    reasons = _refusals(_write(tmp_path, txn))
    assert any("REKEYS" in r for r in reasons), reasons
    assert any("not an application call" in r for r in reasons), reasons


def test_a_close_of_the_multisig_is_refused(configured, tmp_path) -> None:
    from algosdk import transaction

    configured(3, SIGNERS)
    txn = transaction.PaymentTxn(
        sender=ms.address(), sp=_params(), receiver=ms.address(), amt=0, close_remainder_to=HOT
    )
    assert any("CLOSES" in r for r in _refusals(_write(tmp_path, txn)))


def test_a_file_for_another_network_is_refused(configured, tmp_path) -> None:
    """Signing is the irreversible half; submitting is mechanical after it.

    A MainNet rekey signed under `--network testnet` is a real signature on a
    real account. The old code connected to the named network and then never
    used the client for show or sign, so nothing compared them.
    """
    from algosdk import transaction

    configured(3, SIGNERS)
    txn = transaction.PaymentTxn(
        sender=ms.address(), sp=_params(genesis_id="mainnet-v1.0"), receiver=HOT, amt=0
    )
    assert any("mainnet-v1.0" in r for r in _refusals(_write(tmp_path, txn)))


def test_an_inflated_fee_is_refused(configured, tmp_path) -> None:
    """A fee is spent whether or not the transaction does anything."""
    from algosdk import transaction

    configured(3, SIGNERS)
    txn = transaction.PaymentTxn(
        sender=ms.address(), sp=_params(fee=5_000_000), receiver=HOT, amt=0
    )
    assert any("above the" in r for r in _refusals(_write(tmp_path, txn), allow_account_txn=True))


def test_a_file_spending_from_a_different_account_is_refused(configured, tmp_path) -> None:
    """The blob decides the account, not the JSON beside it.

    For a create there is no app id to check against, so the sender simply is
    whatever the blob's multisig hashes to. A 2-of-5 create that nobody
    compares against the configured address is a permanently wrong creator.
    """
    from algosdk import transaction

    configured(2, SIGNERS)
    stranger = ms.address()
    txn = transaction.PaymentTxn(sender=stranger, sp=_params(), receiver=HOT, amt=0)
    path = _write(tmp_path, txn)

    configured(3, SIGNERS)
    assert any("spends from" in r for r in _refusals(path, allow_account_txn=True))


def test_a_genuine_update_of_the_named_app_is_allowed(configured, tmp_path) -> None:
    """A guard that refuses everything is not a guard.

    This is the transaction governance actually produces, and it has to pass
    cleanly or the refusals above are just an outage.
    """
    from algosdk import transaction

    configured(3, SIGNERS)
    txn = transaction.ApplicationUpdateTxn(
        sender=ms.address(), sp=_params(), index=769891898,
        approval_program=b"\x0a\x81\x01", clear_program=b"\x0a\x81\x01",
    )
    assert _refusals(_write(tmp_path, txn)) == []


# --- the MainNet gate --------------------------------------------------
#
# ARCRON_ALLOW_MAINNET=1 was the entire gate. It stops a typo in --network and
# does nothing about which account signs, and a shell that exports it once
# turns --network mainnet back into an ordinary argument. An app's creator is
# fixed at creation, so a MainNet app made from a single key carries an admin
# key over every escrow in it forever.

def test_mainnet_refuses_without_a_multisig(monkeypatch) -> None:
    from scripts import network as net

    monkeypatch.delenv(ms.ADDRESSES_VAR, raising=False)
    monkeypatch.delenv(ms.THRESHOLD_VAR, raising=False)
    with pytest.raises(RuntimeError, match="without a configured multisig"):
        net.require_mainnet_multisig()


def test_mainnet_refuses_a_multisig_that_is_not_the_expected_one(configured) -> None:
    """Member order is part of the address, so this catches a permutation too."""
    from scripts import network as net

    configured(3, [CORVID, LEDGER, HOT, KYN, GASPAR])  # LEDGER and CORVID swapped
    with pytest.raises(RuntimeError, match="not the expected"):
        net.require_mainnet_multisig()


def test_mainnet_accepts_the_real_three_of_five(configured) -> None:
    """A gate that refuses the intended creator is an outage, not a gate."""
    from scripts import network as net

    configured(3, SIGNERS)
    assert ms.address() == net.MAINNET_CREATOR
    net.require_mainnet_multisig()


# --- the beacon ids, recorded once -------------------------------------

def test_the_foundation_beacon_ids_match_what_the_specs_record() -> None:
    """One number, four prose copies, and now one importable source.

    The beacon decides every rain draw and cannot be changed after
    `configure`, so a copy of it that drifts is a copy that would tell a
    participant a rigged draw looks fine.
    """
    import pathlib
    import re

    from scripts import network as net

    spec = pathlib.Path("specs/rain/rain.spec.md").read_text()
    testnet, mainnet = re.search(r"TestNet `(\d+)` and MainNet `(\d+)`", spec).groups()
    assert int(testnet) == net.FOUNDATION_BEACON[net.TESTNET]
    assert int(mainnet) == net.FOUNDATION_BEACON[net.MAINNET]
