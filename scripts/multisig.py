"""Multisig control of a deployment, and offline signing for it.

The creator of a keeper app can replace its programs while the app is
unfrozen, which makes that key the most valuable secret in the project. A
single mnemonic on a single machine is the wrong place for it once anything of
value is escrowed: one compromised laptop rewrites the contract.

So the creator can be an Algorand multisig address instead. Nothing in the
contract changes, because the contract compares `Txn.sender` against
`Global.creator_address` and a multisig address is an address like any other.
What changes is that producing a signature needs several people.

Configure it with, in `.env.<network>` or the environment:

    ARCRON_MULTISIG_THRESHOLD=2
    ARCRON_MULTISIG_ADDRESSES=ADDR1,ADDR2,ADDR3

`address()` then returns the multisig address to deploy from, and the govern
commands switch to producing an unsigned transaction file rather than signing
in process. Each holder signs it wherever their key lives, and anybody can
submit the result: a signature is not a secret.
"""

import base64
import json
import logging
import os
import pathlib

from algosdk import encoding, mnemonic, transaction

logger = logging.getLogger(__name__)

THRESHOLD_VAR = "ARCRON_MULTISIG_THRESHOLD"
ADDRESSES_VAR = "ARCRON_MULTISIG_ADDRESSES"
# Version 1 is the only multisig version Algorand defines.
MULTISIG_VERSION = 1


def configured() -> bool:
    """True when this network is set up to be controlled by a multisig."""
    return bool(os.environ.get(ADDRESSES_VAR))


def signers() -> list[str]:
    raw = os.environ.get(ADDRESSES_VAR, "")
    return [a.strip() for a in raw.split(",") if a.strip()]


def threshold() -> int:
    value = os.environ.get(THRESHOLD_VAR)
    if value is None:
        raise RuntimeError(f"{ADDRESSES_VAR} is set but {THRESHOLD_VAR} is not")
    return int(value)


def multisig() -> transaction.Multisig:
    """The Multisig this network is configured for."""
    addresses = signers()
    if not addresses:
        raise RuntimeError(f"{ADDRESSES_VAR} is not set")
    need = threshold()
    if not 1 <= need <= len(addresses):
        raise RuntimeError(
            f"{THRESHOLD_VAR}={need} is impossible for {len(addresses)} signers"
        )
    if len(set(addresses)) != len(addresses):
        raise RuntimeError("The same address appears twice in " + ADDRESSES_VAR)
    for candidate in addresses:
        if not encoding.is_valid_address(candidate):
            raise RuntimeError(
                f"{candidate!r} in {ADDRESSES_VAR} is not an Algorand address. "
                "Expected 58 characters; check for a stray space or a truncated paste."
            )
    return transaction.Multisig(MULTISIG_VERSION, need, addresses)


def address() -> str:
    """The address to deploy from, and the one the contract will call creator."""
    return multisig().address()


def describe() -> str:
    return f"{threshold()} of {len(signers())} at {address()}"


def export_unsigned(txn: transaction.Transaction, path: pathlib.Path) -> pathlib.Path:
    """Write a transaction with an empty multisig envelope, ready to be signed."""
    signed = transaction.MultisigTransaction(txn, multisig())
    path.write_text(
        json.dumps(
            {
                "note": "Arcron multisig transaction. Sign with scripts.multisig sign.",
                "threshold": threshold(),
                "signers": signers(),
                "address": address(),
                "msig": base64.b64encode(encoding.msgpack_encode(signed).encode()).decode(),
            },
            indent=2,
        )
        + "\n"
    )
    return path


def _load(path: pathlib.Path) -> transaction.MultisigTransaction:
    payload = json.loads(path.read_text())
    raw = base64.b64decode(payload["msig"]).decode()
    return encoding.msgpack_decode(raw)


def sign(path: pathlib.Path, signer_mnemonic: str) -> int:
    """Add one signature in place. Returns how many signatures the file now has."""
    signed = _load(path)
    signed.sign(mnemonic.to_private_key(signer_mnemonic))
    payload = json.loads(path.read_text())
    payload["msig"] = base64.b64encode(encoding.msgpack_encode(signed).encode()).decode()
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return collected(path)


def collected(path: pathlib.Path) -> int:
    """How many of the required signatures the file carries."""
    signed = _load(path)
    return sum(1 for s in signed.multisig.subsigs if s.signature)


def blob_threshold(path: pathlib.Path) -> int:
    """The threshold inside the signed blob, which is the one that counts.

    The JSON alongside it is decoration: a signature covers the transaction
    bytes and the multisig envelope, not the filename, not the note, and not
    the `threshold` field somebody could edit.
    """
    return _load(path).multisig.threshold


def app_id(path: pathlib.Path) -> int:
    """The app this transaction acts on, or 0 if it is not an app call."""
    return int(getattr(_load(path).transaction, "index", 0) or 0)


def submit(algod, path: pathlib.Path) -> str:
    """Send a fully signed transaction. Returns the transaction id."""
    signed = _load(path)
    have = collected(path)
    need = blob_threshold(path)
    if have < need:
        raise RuntimeError(f"Only {have} of {need} signatures. Not submitting.")
    txid = algod.send_transaction(signed)
    transaction.wait_for_confirmation(algod, txid, 6)
    return txid
