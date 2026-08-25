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
import hashlib
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


def describe_transaction(path: pathlib.Path) -> list[str]:
    """What the transaction in this file would actually do, in plain terms.

    A holder is asked to sign base64 msgpack, which nobody can read. Signing
    what you cannot read is the whole failure mode of a multisig: it turns
    several people into one person who happened to click several times. This
    is what `govern show` prints, and it is worth reading before signing.
    """
    signed = _load(path)
    txn = signed.transaction
    lines = [
        f"type          {txn.type}",
        f"sender        {txn.sender}",
        f"network       {txn.genesis_id}",
        f"valid rounds  {txn.first_valid_round} to {txn.last_valid_round}",
        f"fee           {txn.fee} microAlgos",
    ]
    on_complete = getattr(txn, "on_complete", None)
    if on_complete is not None:
        names = {0: "NoOp", 1: "OptIn", 2: "CloseOut", 3: "ClearState",
                 4: "UpdateApplication", 5: "DeleteApplication"}
        label = names.get(int(on_complete), str(on_complete))
        lines.append(f"app id        {getattr(txn, 'index', 0)}")
        lines.append(f"on complete   {label}")
        if label == "UpdateApplication":
            approval = getattr(txn, "approval_program", b"") or b""
            clear = getattr(txn, "clear_program", b"") or b""
            lines.append(f"REPLACES THE PROGRAMS with {len(approval)} + {len(clear)} bytes")
            lines.append(f"  approval sha256 {hashlib.sha256(approval).hexdigest()}")
            lines.append("  Compare that against `fledge run verify` on the commit you expect.")
        args = getattr(txn, "app_args", None) or []
        if args:
            lines.append("app args      " + ", ".join(a.hex() for a in args))
    if txn.type == "pay":
        lines.append(f"receiver      {getattr(txn, 'receiver', '')}")
        lines.append(f"amount        {getattr(txn, 'amt', 0)} microAlgos")
    if getattr(txn, "rekey_to", None):
        lines.append(f"!! REKEYS the sender to {txn.rekey_to}. Do not sign unless you meant this.")
    if getattr(txn, "close_remainder_to", None):
        lines.append(f"!! CLOSES the sender to {txn.close_remainder_to}. Do not sign unless you meant this.")
    return lines


def collected(path: pathlib.Path) -> int:
    """How many of the required signatures the file carries."""
    signed = _load(path)
    return sum(1 for s in signed.multisig.subsigs if s.signature)


def submit(algod, path: pathlib.Path) -> str:
    """Send a fully signed transaction. Returns the transaction id."""
    signed = _load(path)
    have = collected(path)
    payload = json.loads(path.read_text())
    if have < payload["threshold"]:
        raise RuntimeError(
            f"Only {have} of {payload['threshold']} signatures. Not submitting."
        )
    txid = algod.send_transaction(signed)
    transaction.wait_for_confirmation(algod, txid, 6)
    return txid
