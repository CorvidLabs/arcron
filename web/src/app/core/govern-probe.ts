/**
 * Whether a wallet will sign a transaction whose sender is a multisig.
 *
 * This is the one question the rest of the governance work cannot answer from
 * a desk. Algorand multisig is N ed25519 signatures over the same transaction
 * bytes, and `govern-sign.ts` assembles them correctly, proven against real
 * keypairs. What is unknown is whether any wallet will produce one of those
 * signatures, because the transaction's sender is the multisig address and a
 * member does not hold that address. Wallets generally check exactly that.
 *
 * ARC-1 has a `msig` field for this, and `@txnlab/use-wallet` exports
 * `MultisigMetadata` describing it, and **no adapter implements it**: zero
 * occurrences in the library's built code, zero in the Pera, Defly, Lute and
 * Kibisis adapters. Reading the type and concluding it was supported is how
 * this was got wrong once already, so the answer here comes from asking a real
 * wallet on TestNet and writing down what it said.
 *
 * The result is deliberately a record rather than a boolean. "Pera refused"
 * and "Pera was never tried" must not look the same in a report, and a wallet
 * that errors for an unrelated reason must not be recorded as refusing.
 */

export type ProbeOutcome = 'signed' | 'refused' | 'errored' | 'not-tried';

export interface ProbeResult {
  readonly wallet: string;
  readonly address: string;
  readonly outcome: ProbeOutcome;
  /** What the wallet said, when it said anything. */
  readonly detail: string | null;
  /** Present only on success: the 64 byte signature it produced. */
  readonly signature: Uint8Array | null;
}

/**
 * Classify what came back from a signing attempt.
 *
 * A refusal and a fault are different findings. A refusal is an answer about
 * the wallet's policy on multisig senders, which is what this exists to learn.
 * A fault is a bad connection, a wrong network, or a closed popup, and
 * recording one as the other would put a false conclusion in the report.
 */
export function classify(error: unknown): { outcome: ProbeOutcome; detail: string } {
  const message = error instanceof Error ? error.message : String(error);
  const lowered = message.toLowerCase();

  // Phrasing varies by wallet, so this matches on the idea rather than on any
  // one wallet's exact words, and falls back to "errored" when unsure. Being
  // unsure is a fine answer; guessing is not.
  const refusalWords = [
    'reject',
    'declin',
    'denied',
    'cancel',
    'not authorized',
    'unauthorized',
    'unknown account',
    'account not found',
    'invalid signer',
    'cannot sign',
    'unsupported',
  ];
  if (refusalWords.some((word) => lowered.includes(word))) {
    return { outcome: 'refused', detail: message };
  }
  return { outcome: 'errored', detail: message };
}

/** A one-line summary a person can paste into an issue. */
export function describeResult(result: ProbeResult): string {
  switch (result.outcome) {
    case 'signed':
      return `${result.wallet}: signed. Produced a ${result.signature?.length ?? 0} byte signature.`;
    case 'refused':
      return `${result.wallet}: refused to sign for a multisig sender. ${result.detail ?? ''}`.trim();
    case 'errored':
      return `${result.wallet}: errored, which is not the same as refusing. ${result.detail ?? ''}`.trim();
    case 'not-tried':
      return `${result.wallet}: not tried.`;
  }
}

/**
 * What the whole probe found, in the form the report needs.
 *
 * `usable` is deliberately narrow: a wallet counts only if it actually returned
 * a 64 byte signature. Anything else, including an error nobody understood, is
 * not a wallet a holder should be told to rely on.
 */
export function summariseProbe(results: readonly ProbeResult[]): {
  usable: string[];
  refused: string[];
  unclear: string[];
  verdict: string;
} {
  const usable = results
    .filter((r) => r.outcome === 'signed' && r.signature?.length === 64)
    .map((r) => r.wallet);
  const refused = results.filter((r) => r.outcome === 'refused').map((r) => r.wallet);
  const unclear = results.filter((r) => r.outcome === 'errored').map((r) => r.wallet);

  let verdict: string;
  if (usable.length > 0) {
    verdict =
      `Wallet signing works for governance with ${usable.join(', ')}. ` +
      'Holders on other wallets keep using the command line.';
  } else if (refused.length > 0) {
    verdict =
      'No wallet tried would sign for a multisig sender. Wallet-based governance ' +
      'signing is not available through use-wallet today, and the command line ' +
      'stays the only route.';
  } else {
    verdict =
      'Nothing conclusive. Every attempt errored for reasons that were not a ' +
      'refusal, so this says nothing about multisig support either way.';
  }
  return { usable, refused, unclear, verdict };
}
