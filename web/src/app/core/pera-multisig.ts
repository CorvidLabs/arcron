/**
 * Asking Pera to sign for a multisig, going around use-wallet to do it.
 *
 * `@txnlab/use-wallet`'s Pera adapter filters before the wallet is involved:
 *
 *     const canSignTxn = this.addresses.includes(txn.sender.toString());
 *     if (isIndexMatch && canSignTxn) txnsToSign.push({ txn });
 *     else txnsToSign.push({ txn, signers: [] });
 *
 * A multisig sender is never an address a member holds, so every governance
 * transaction lands in that `else` and is tagged `signers: []`. Pera's own
 * documentation for that field says "Wallet skips to sign this txn if signers
 * is empty array. If undefined, wallet tries to sign it." So the wallet is
 * *told* not to sign, returns nothing, and a probe going through use-wallet
 * records a refusal that never happened.
 *
 * Pera's SDK supports this directly. `SignerTransaction` carries an optional
 * `msig: PeraWalletMultisigMetadata`, described as "multisig metadata used to
 * sign the transaction", with version, threshold and an ordered address list.
 * That is ARC-1's shape, implemented. use-wallet exports the same type and
 * implements none of it.
 *
 * So this talks to Pera itself: same wallet, same session, without the filter.
 */

import algosdk from 'algosdk';

/** What Pera answered, kept as a record rather than collapsed to a boolean. */
export interface PeraMultisigAttempt {
  readonly outcome: 'signed' | 'refused' | 'errored';
  readonly detail: string | null;
  readonly signature: Uint8Array | null;
}

/**
 * Ask Pera to sign `txn` as `member`, declaring the multisig it belongs to.
 *
 * `signers` is deliberately left undefined. Setting it to `[member]` would be
 * reasonable-looking and is the trap: the field's documented meaning is that an
 * empty array skips the transaction, and use-wallet's bug is passing `[]`.
 * Leaving it out is what tells Pera to try.
 */
export async function signMultisigWithPera(
  txn: algosdk.Transaction,
  member: string,
  multisig: { version: number; threshold: number; addrs: string[] },
): Promise<PeraMultisigAttempt> {
  let client: { reconnectSession(): Promise<string[]>; signTransaction(
    groups: Array<Array<{ txn: algosdk.Transaction; msig?: unknown; signers?: string[]; message?: string }>>,
    signerAddress?: string,
  ): Promise<Uint8Array[]> };

  try {
    const { PeraWalletConnect } = await import('@perawallet/connect');
    client = new PeraWalletConnect({ chainId: 416002 }) as unknown as typeof client;
    // Picks up the session use-wallet already established, so this does not
    // ask the holder to connect a second time.
    await client.reconnectSession();
  } catch (cause) {
    return {
      outcome: 'errored',
      detail: `Could not reach Pera: ${cause instanceof Error ? cause.message : String(cause)}`,
      signature: null,
    };
  }

  try {
    const signed = await client.signTransaction(
      [
        [
          {
            txn,
            msig: multisig,
            message: 'Arcron governance signing probe. Nothing is submitted.',
          },
        ],
      ],
      member,
    );
    const first = signed?.[0];
    if (!first) {
      return {
        outcome: 'refused',
        detail: 'Pera returned nothing, having been asked directly with msig metadata.',
        signature: null,
      };
    }
    return { outcome: 'signed', detail: null, signature: extractAnySignature(first) };
  } catch (cause) {
    const message = cause instanceof Error ? cause.message : String(cause);
    const refused = /reject|declin|cancel|denied/i.test(message);
    return { outcome: refused ? 'refused' : 'errored', detail: message, signature: null };
  }
}

/**
 * The signature out of whatever Pera returned.
 *
 * Told it is a multisig, Pera may return a `SignedTxn` carrying a `msig` with
 * one subsig filled rather than a bare `sig`. Both are handled, because which
 * one comes back is itself part of what this probe is measuring.
 */
export function extractAnySignature(signedTxn: Uint8Array): Uint8Array | null {
  const decoded = algosdk.decodeObj(signedTxn) as Record<string, unknown>;
  const bare = decoded['sig'] as Uint8Array | undefined;
  if (bare) return bare;

  const msig = decoded['msig'] as { subsig?: Array<{ s?: Uint8Array }> } | undefined;
  const filled = msig?.subsig?.find((entry) => entry.s);
  return filled?.s ?? null;
}
