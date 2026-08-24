"""Spike: does Archon work for a post-quantum account, and what does it cost?

Algorand 5 adds post-quantum accounts: a Falcon-1024 public key is hashed into
a 32-byte address that is deliberately *not* an ed25519 curve point, so no
ed25519 key can ever spend it. The contract only ever compares addresses, so
it ought to be indifferent — but "ought" is not measured, and two things could
bite:

* **Size.** A Falcon-1024 signature and public key are far larger than an
  ed25519 signature. Algorand's minimum fee is `max(min_fee, size × fee_per_byte)`,
  so if a chain ever runs a non-zero per-byte fee, a post-quantum keeper pays
  more per execution than `MIN_UPKEEP_FEE` was set to cover.
* **Acceptance.** Whether this node's consensus version admits the transaction
  type at all.

Nothing here signs for real: the SDK takes a signing callback and no Falcon
implementation ships with it, so the signature bytes are the right size and
the wrong value. That is enough to measure encoding and to see how algod
rejects it, which is the question — a chain that does not know the type
rejects it differently from one that simply fails verification.

Run:  poetry run python -m scripts.spike_quantum [--network localnet]
"""

import argparse
import logging

from algosdk import constants, encoding, transaction

from scripts import network as net
from smart_contracts.keeper.contract import MIN_UPKEEP_FEE

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Falcon-1024 parameter sizes. The SDK carries the scheme identifier but not
# the key sizes, because it never generates keys itself.
FALCON_1024_PUBLIC_KEY_BYTES = 1_793
FALCON_1024_SIGNATURE_BYTES = 1_280
# What a keeper spends per execution today: the outer fee plus the pooled
# extra covering `execute`'s two inner transactions.
KEEPER_COST_MICROALGO = 3_000


def _pq_address(seed: bytes) -> tuple[str, int, bytes]:
    """A post-quantum address from a stand-in Falcon-1024 public key."""
    public_key = (seed * (FALCON_1024_PUBLIC_KEY_BYTES // len(seed) + 1))[
        :FALCON_1024_PUBLIC_KEY_BYTES
    ]
    address, salt = encoding.address_from_pq_key(
        constants.falcon_1024_scheme, public_key
    )
    return address, salt, public_key


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    net.add_network_argument(parser)
    args = parser.parse_args(argv)

    algorand = net.connect(args.network)
    algod = algorand.client.algod
    deployer = algorand.account.from_environment("DEPLOYER")

    # ---------------------------------------------------------------- A
    logger.info("")
    logger.info("Part A — a post-quantum address is an ordinary address")
    address, salt, public_key = _pq_address(b"archon-falcon-1024-stand-in-key")
    logger.info(f"  address           {address}")
    logger.info(f"  canonical salt    {salt}")
    logger.info(f"  public key        {len(public_key)} bytes")
    decoded = encoding.decode_address(address)
    logger.info(f"  decodes to        {len(decoded)} bytes")
    logger.info(
        f"  is an ed25519 point  {encoding.is_ed25519_point(decoded)}"
        "  (false by construction: no ed25519 key can spend it)"
    )
    assert len(decoded) == 32, "a post-quantum address must still be 32 bytes"
    assert not encoding.is_ed25519_point(decoded)

    # ---------------------------------------------------------------- B
    logger.info("")
    logger.info("Part B — what a post-quantum keeper's execution weighs")
    params = algod.suggested_params()
    params.flat_fee = True
    params.fee = 4_000
    call = transaction.ApplicationNoOpTxn(
        sender=address,
        sp=params,
        index=1,
        app_args=[b"\x00\x01\x02\x03", (0).to_bytes(8, "big")],
        boxes=[(0, b"u" + (0).to_bytes(8, "big"))],
        foreign_apps=[2],
    )
    ed25519 = transaction.SignedTransaction(call, "A" * 88)
    pq_sig = transaction.PQSig(
        constants.falcon_1024_scheme,
        salt,
        public_key,
        b"\x00" * FALCON_1024_SIGNATURE_BYTES,
    )
    pq = transaction.PQSignedTransaction(call, pq_sig)

    ed_bytes = len(encoding.msgpack_encode(ed25519).encode())
    pq_bytes = len(encoding.msgpack_encode(pq).encode())
    logger.info(f"  ed25519-signed execute   {ed_bytes:>6} bytes")
    logger.info(f"  Falcon-1024-signed       {pq_bytes:>6} bytes  ({pq_bytes / ed_bytes:.1f}×)")

    # Algorand charges max(min_fee, size × fee_per_byte). Read the per-byte
    # rate this chain actually runs rather than assuming it is still zero.
    per_byte = getattr(params, "fee", 0) if not params.flat_fee else 0
    live = algod.suggested_params()
    per_byte = live.fee if not live.flat_fee else 0
    logger.info(f"  this chain's fee per byte  {per_byte} µALGO")
    for label, size in (("ed25519", ed_bytes), ("Falcon-1024", pq_bytes)):
        floor = max(live.min_fee, size * per_byte)
        cost = floor + 2_000  # the pooled extra for two inner transactions
        verdict = "covered" if cost <= MIN_UPKEEP_FEE else "NOT COVERED"
        logger.info(
            f"  {label:<12} minimum fee {floor:>6} µALGO, execution costs "
            f"{cost:>6} against a {MIN_UPKEEP_FEE} µALGO floor — {verdict}"
        )

    # ---------------------------------------------------------------- C
    logger.info("")
    logger.info("Part C — does this node admit the transaction type at all?")
    try:
        algod.send_transactions([pq])
        logger.info("  accepted (unexpected: the signature is a stand-in)")
    except Exception as exc:
        text = str(exc).replace("\n", " ")
        index = text.lower().find("pq signature validation failed")
        logger.info(f"  rejected — {text[index:index + 130] if index >= 0 else text[-130:]}")
        # The distinction that matters: a node that does not know the type
        # fails to decode it. This one decoded the transaction, derived the
        # address from the scheme, public key and salt, and ran a real Falcon
        # verification that failed only because the signature is a stand-in.
        assert "falcon" in text.lower(), (
            "this node did not run a Falcon verification, so it does not "
            "support post-quantum accounts"
        )
        logger.info("  The node ran a real Falcon verification: the type is supported.")

    logger.info("")
    logger.info(
        "Archon stores `creator` as a 32-byte address and pays `Txn.sender`, so "
        "nothing in the contract distinguishes the two account kinds. The one "
        "thing to watch is the size: MIN_UPKEEP_FEE is permanent, and it covers "
        "a post-quantum keeper only while Algorand's per-byte fee is zero."
    )


if __name__ == "__main__":
    main()
