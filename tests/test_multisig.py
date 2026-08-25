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
