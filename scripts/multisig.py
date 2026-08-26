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

try:  # pragma: no cover - exercised by the tests below, absent only on odd builds
    from nacl.bindings import crypto_core_ed25519_is_valid_point as _on_curve
except ImportError:  # pragma: no cover
    _on_curve = None

logger = logging.getLogger(__name__)

THRESHOLD_VAR = "ARCRON_MULTISIG_THRESHOLD"
ADDRESSES_VAR = "ARCRON_MULTISIG_ADDRESSES"
# Version 1 is the only multisig version Algorand defines.
MULTISIG_VERSION = 1


def _can_sign(address: str) -> bool:
    """Whether this address could ever produce an ed25519 signature.

    A multisig subsignature is a 32-byte ed25519 public key and a 64-byte
    ed25519 signature over the transaction. Producing one needs the private
    key for that public key, which only exists when the address is a point on
    the curve. A post-quantum Falcon account's address is a hash instead, so
    it is a perfectly good Algorand account that can never be a multisig
    member.

    Checking membership matters more than it sounds: about half of all 32-byte
    values happen to be valid points, so a weaker check passes some Falcon
    addresses and rejects others, which is worse than not checking at all.
    """
    if _on_curve is None:
        return True
    return bool(_on_curve(encoding.decode_address(address)))


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
        if not _can_sign(candidate):
            raise RuntimeError(
                f"{candidate} cannot take part in a multisig: its address is not "
                "a point on the ed25519 curve, so no ed25519 private key "
                "corresponds to it and it can never produce a subsignature. A "
                "post-quantum (Falcon) account looks exactly like this. Nothing "
                "would have complained: the address derives normally, and the "
                "result would read as a threshold of N while behaving as a "
                "threshold of N out of one fewer signer."
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
                "note": "Arcron multisig transaction. Read it with `govern show --file <this>` and sign with `govern sign --file <this>`.",
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
        approval = getattr(txn, "approval_program", b"") or b""
        clear = getattr(txn, "clear_program", b"") or b""
        # A create is a NoOp against app id 0 that carries programs, so it
        # looked like an ordinary call here and printed nothing that mattered.
        # The fields a create fixes forever were only ever shown by `govern
        # create`, which the coordinator runs. The holders who authorise the
        # one transaction that cannot be undone read this.
        is_create = int(getattr(txn, "index", 0) or 0) == 0 and bool(approval)
        pages = int(getattr(txn, "extra_pages", 0) or 0)
        gs = getattr(txn, "global_schema", None)
        ls = getattr(txn, "local_schema", None)
        if is_create:
            lines.append("CREATES A NEW APPLICATION. Every field below is permanent:")
            lines.append("  the creator is this sender and can never be changed")
            lines.append(f"  extra pages   {pages}")
            lines.append(
                f"  global state  {getattr(gs, 'num_uints', 0)} uints, "
                f"{getattr(gs, 'num_byte_slices', 0)} byte slices"
            )
            lines.append(
                f"  local state   {getattr(ls, 'num_uints', 0)} uints, "
                f"{getattr(ls, 'num_byte_slices', 0)} byte slices"
            )
            lines.append("  The creator and the local schema cannot be changed afterwards.")
        elif pages or gs is not None:
            # Printed for updates too, because they can carry these. An earlier
            # version showed them only for creates, on the stated reasoning
            # that an update cannot change them. `ApplicationUpdateTxn` takes
            # both, and on a protocol that honours them a non-zero value
            # replaces the app's outright while an omitted one goes to zero.
            # So a hostile file with honest bytecode, whose combined digest
            # matches, could resize the app under holders who had been told
            # that was impossible.
            lines.append("!! THIS UPDATE ALSO RESIZES THE APPLICATION:")
            lines.append(f"     extra pages   {pages}")
            lines.append(
                f"     global state  {getattr(gs, 'num_uints', 0)} uints, "
                f"{getattr(gs, 'num_byte_slices', 0)} byte slices"
            )
            lines.append("     An honest `govern update` sets neither. Do not sign this.")
        if is_create or label == "UpdateApplication":
            verb = "CARRIES PROGRAMS of" if is_create else "REPLACES THE PROGRAMS with"
            lines.append(f"{verb} {len(approval)} + {len(clear)} bytes")
            # The combined digest is what `verify_build` records, and it is the
            # only one that pins both programs. An approval-only hash lets an
            # honest approval be shipped alongside a hostile clear program,
            # which matches on inspection and cannot be replaced after freeze.
            lines.append(f"  combined sha256 {combined_digest(approval, clear)}")
            lines.append(f"  approval sha256 {hashlib.sha256(approval).hexdigest()}")
            lines.append(f"  clear    sha256 {hashlib.sha256(clear).hexdigest()}")
            lines.append(
                "  Compare the combined digest against "
                "`poetry run python -m scripts.verify_build` on the commit you expect. "
                "Do not compare against `fledge run verify`, which does not rebuild."
            )
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
    if getattr(txn, "close_assets_to", None):
        lines.append(
            f"!! CLOSES an asset holding to {txn.close_assets_to}. Do not sign unless you meant this."
        )
    return lines


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


def combined_digest(approval: bytes, clear: bytes) -> str:
    """The digest `scripts/verify_build.py` records, byte for byte.

    Kept identical on purpose: a holder comparing a hash before signing and a
    reviewer checking a deployment afterwards have to be looking at the same
    number, or the comparison proves nothing.
    """
    return hashlib.sha256(approval + b"\x00" + clear).hexdigest()


def blob_address(path: pathlib.Path) -> str:
    """The account this file actually spends from, read from the blob.

    The JSON fields beside a signing file are decoration. Only the multisig
    embedded in the signed blob decides which account a signature binds, and
    for a create transaction the sender *is* whatever that blob says, with no
    later check to catch it.
    """
    return _load(path).multisig.address()


def blob_signers(path: pathlib.Path) -> list[str]:
    """The member addresses in the blob, in the order that forms the address."""
    return [encoding.encode_address(sub.public_key) for sub in _load(path).multisig.subsigs]


def carried_programs(path: pathlib.Path) -> tuple[bytes, bytes] | None:
    """The programs a create or update transaction carries, if it carries any."""
    txn = _load(path).transaction
    approval = getattr(txn, "approval_program", b"") or b""
    clear = getattr(txn, "clear_program", b"") or b""
    return (approval, clear) if approval else None


def refusals(
    path: pathlib.Path,
    *,
    app_id: int,
    genesis_ids: tuple[str, ...],
    expected_address: str | None,
    expected_digest: str | None,
    max_fee: int,
    allow_account_txn: bool = False,
    allow_rekey: bool = False,
) -> list[str]:
    """Every reason this file must not be signed. Empty means it may be.

    Written as refusals rather than a boolean so a holder is told all of what
    is wrong at once. The previous guard was `if in_file and in_file != app_id`,
    and `app_id()` is 0 for anything that is not an app call, so the whole
    check short-circuited away for exactly the transactions that can steal the
    account outright: a rekey, a close, and a create.
    """
    signed = _load(path)
    txn = signed.transaction
    reasons: list[str] = []

    in_file = int(getattr(txn, "index", 0) or 0)
    is_app_call = txn.type == "appl"

    if not is_app_call and not allow_account_txn:
        reasons.append(
            f"This is a {txn.type} transaction, not an application call. Governance never "
            "produces one. Pass --account-txn only if you know why this exists."
        )
    if is_app_call and in_file != app_id:
        reasons.append(
            f"This file acts on app {in_file}, not the {app_id} you named. "
            "Use --app-id 0 only when creating an app."
        )
    if txn.genesis_id not in genesis_ids:
        reasons.append(
            f"This file is for network {txn.genesis_id}, and you are signing as "
            f"{'/'.join(genesis_ids)}. Signing is the irreversible half; a wrong network "
            "here means signing away an account on a chain you did not mean to touch."
        )
    if expected_address is not None and blob_address(path) != expected_address:
        reasons.append(
            f"This file spends from {blob_address(path)}, not the configured "
            f"{expected_address}. The blob decides the account, not the JSON beside it."
        )
    if getattr(txn, "rekey_to", None) and not allow_rekey:
        reasons.append(
            f"This REKEYS the sender to {txn.rekey_to}, handing the account to that key "
            "permanently. Pass --i-mean-to-rekey if that is genuinely what you want."
        )
    if getattr(txn, "close_remainder_to", None) and not allow_rekey:
        reasons.append(
            f"This CLOSES the sender to {txn.close_remainder_to}, emptying the account. "
            "Pass --i-mean-to-rekey if that is genuinely what you want."
        )
    if getattr(txn, "close_assets_to", None) and not allow_rekey:
        reasons.append(
            f"This CLOSES an asset holding to {txn.close_assets_to}, sending the whole "
            "balance there and opting the sender out. Pass --i-mean-to-rekey if that is "
            "genuinely what you want."
        )
    # A signing machine with no multisig configured cannot compare the blob
    # against anything, and saying nothing looks identical to having checked.
    if expected_address is None:
        reasons.append(
            "No multisig is configured on this machine, so nothing checked which account "
            "this file spends from. Set ARCRON_MULTISIG_ADDRESSES and "
            "ARCRON_MULTISIG_THRESHOLD to the group you believe you are part of, then "
            "read the members this file actually names."
        )
    if is_app_call and in_file != 0:
        # An honest `govern update` omits both, which keeps the app's current
        # sizes. A file that sets either is asking to resize the app, and a
        # matching program digest says nothing about that.
        if int(getattr(txn, "extra_pages", 0) or 0):
            reasons.append(
                "This update also sets extra program pages, which resizes the application "
                "and changes what its creator has locked up. An honest update sets none. "
                "Pass --i-mean-to-resize only if you know why this is here."
            )
        if getattr(txn, "global_schema", None) is not None:
            reasons.append(
                "This update also sets a global state schema, which replaces the app's "
                "outright. Shrinking it bricks the app and growing it costs the creator "
                "minimum balance permanently. An honest update sets none."
            )
    if expected_digest is not None:
        carried = carried_programs(path)
        if carried is not None and combined_digest(*carried) != expected_digest:
            reasons.append(
                f"The programs in this file are not the ones this working tree compiles to.\n"
                f"      file: {combined_digest(*carried)}\n"
                f"      tree: {expected_digest}\n"
                "      Printing the digest asks somebody to compare it. This is the "
                "comparison. Check out the tag this was built from, or find out why the "
                "file disagrees with it."
            )
    if int(txn.fee) > max_fee:
        reasons.append(
            f"The fee is {txn.fee} microAlgos, above the {max_fee} ceiling. A fee is spent "
            "whether or not the transaction does anything, so an inflated one drains the "
            "account it is signed from. Pass --allow-high-fee if this is deliberate."
        )
    return reasons
