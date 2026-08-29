/**
 * Putting one member's signature into a multisig envelope.
 *
 * ## Why this exists rather than using the wallet's own multisig support
 *
 * ARC-1 defines a `msig` field on a signing request, and `@txnlab/use-wallet`
 * exports `MultisigMetadata` describing it. **No adapter implements it.** The
 * string `msig` appears zero times in the library's built code and zero times
 * in the Pera, Defly, Lute and Kibisis adapters. The type is the interface
 * definition carried forward; nothing constructs or forwards the field.
 *
 * So the wallet is never told this is a multisig. It is asked to sign a
 * transaction, it returns a signed transaction, and this module takes the
 * signature out and puts it where the multisig envelope needs it. Algorand
 * multisig is exactly that: N ed25519 signatures over the same transaction
 * bytes, collected in order.
 *
 * ## The part that is not known yet
 *
 * The transaction's sender is the multisig address, not the member's address.
 * Wallets generally check that they are being asked to sign for an account they
 * hold, and a member does not hold the multisig address. Whether any given
 * wallet signs anyway is a question about that wallet, not about this code, and
 * it is answered by trying it rather than by reading a type definition.
 *
 * Everything here is pure and tested against a real key, so when that question
 * is answered the assembly is already known to be correct.
 */

import algosdk from 'algosdk';

import { GovernFileError, decodeMsigBlob, type GovernEnvelope } from './govern-file';

/** Re-encode a multisig transaction back into the envelope's double base64. */
export function encodeMsigBlob(bytes: Uint8Array): string {
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  // Double, matching `scripts/multisig.py::export_unsigned`: msgpack_encode
  // already returns base64, and the envelope base64s that string again.
  return btoa(btoa(binary));
}

/**
 * The signature a wallet produced, taken out of the signed transaction it returned.
 *
 * A wallet hands back a whole `SignedTxn`, of which only the 64-byte `sig` is
 * wanted. Everything else it wrapped around the transaction is discarded,
 * because the envelope already holds the transaction and this must not be a
 * route by which a wallet substitutes a different one.
 */
export function extractSignature(signedTxn: Uint8Array): Uint8Array {
  const decoded = algosdk.decodeObj(signedTxn) as Record<string, unknown>;
  const sig = decoded['sig'] as Uint8Array | undefined;
  if (!sig) {
    throw new GovernFileError(
      'The wallet returned a transaction with no signature on it. If it returned a ' +
        'multisig instead, this account may already have signed.',
    );
  }
  if (sig.length !== 64) {
    throw new GovernFileError(`Expected a 64 byte signature, got ${sig.length}.`);
  }
  return sig;
}

/**
 * The transaction inside the envelope, encoded as a wallet expects to receive it.
 *
 * Taken from the blob rather than rebuilt, so that what gets signed is exactly
 * what will be submitted. Rebuilding it from decoded fields would introduce a
 * gap where the two could differ, and that gap is the whole attack.
 */
export function unsignedTxnBytes(envelope: GovernEnvelope): Uint8Array {
  const decoded = algosdk.decodeObj(decodeMsigBlob(envelope.msig)) as Record<string, unknown>;
  const txn = decoded['txn'];
  if (txn === undefined) {
    throw new GovernFileError('That envelope carries no transaction.');
  }
  return algosdk.encodeObj(txn as Record<string, unknown>);
}

/**
 * Put `signature` into the subsig belonging to `address`, and return the new blob.
 *
 * Refuses rather than guesses in three cases, each of which means the file and
 * the signer disagree about something that matters:
 *
 * - the address is not a member, so there is no slot for it
 * - that member has already signed, so this would silently overwrite
 * - the signature is not 64 bytes
 */
export function insertSignature(
  envelope: GovernEnvelope,
  address: string,
  signature: Uint8Array,
): string {
  const decoded = algosdk.decodeObj(decodeMsigBlob(envelope.msig)) as Record<string, unknown>;
  const msig = decoded['msig'] as
    | { subsig: Array<{ pk?: Uint8Array; s?: Uint8Array }> }
    | undefined;
  if (!msig?.subsig) {
    throw new GovernFileError('That envelope carries no multisig.');
  }

  const index = msig.subsig.findIndex(
    (entry) => entry.pk && algosdk.encodeAddress(entry.pk) === address,
  );
  if (index === -1) {
    throw new GovernFileError(
      `${address} is not one of this multisig's members, so there is no slot for its ` +
        'signature. Connect one of the signer accounts.',
    );
  }
  if (msig.subsig[index].s) {
    throw new GovernFileError(
      `${address} has already signed this transaction. Pass the file to a holder who ` +
        'has not.',
    );
  }
  if (signature.length !== 64) {
    throw new GovernFileError(`Expected a 64 byte signature, got ${signature.length}.`);
  }

  msig.subsig[index].s = signature;
  return encodeMsigBlob(algosdk.encodeObj(decoded));
}

/** The envelope with one more signature, ready to hand to the next holder. */
export function withSignature(
  envelope: GovernEnvelope,
  address: string,
  signature: Uint8Array,
): GovernEnvelope {
  return { ...envelope, msig: insertSignature(envelope, address, signature) };
}
