/**
 * Reading, checking and writing the file a multisig holder is asked to sign.
 *
 * `scripts/multisig.py::export_unsigned` writes a JSON envelope carrying the
 * threshold, the ordered signer set, the derived multisig address, and the
 * transaction itself. This module is the browser's half of that format, and it
 * exists so the page can tell a holder what they are about to authorize
 * *before* a wallet is involved.
 *
 * The point of this page is not convenience. `govern show` on the command line
 * forces a holder to read a decoded transaction; a friendly button invites them
 * not to, which would make a nicer tool that is worse at the only job it has.
 * So everything here is arranged so that signing is the last thing that becomes
 * possible, after the digest has been shown and matched.
 *
 * ## The encoding, which is easy to get wrong
 *
 * `msig` is **double base64**. Python's `encoding.msgpack_encode` already
 * returns a base64 string, and `export_unsigned` base64-encodes that string
 * again before writing it. Decoding once yields more base64, not msgpack. A
 * parser that stops after one decode gets a plausible-looking byte string and
 * fails somewhere further along, which is a worse failure than an obvious one.
 */

import algosdk from 'algosdk';

/** The envelope on disk, as `export_unsigned` writes it. */
export interface GovernEnvelope {
  readonly note?: string;
  readonly threshold: number;
  readonly signers: readonly string[];
  readonly address: string;
  readonly msig: string;
}

/** What a holder needs to see before deciding. */
export interface GovernSummary {
  /** The multisig this transaction spends from, derived from the blob itself. */
  readonly address: string;
  readonly threshold: number;
  readonly signers: readonly string[];
  /** Which signers have already signed, in the order the multisig fixes. */
  readonly signedBy: readonly string[];
  readonly signatureCount: number;
  /** Application this acts on, or null for a create. */
  readonly appId: bigint | null;
  readonly onComplete: string;
  /** Present only when the transaction replaces programs. */
  readonly approvalBytes: number | null;
  readonly clearBytes: number | null;
  /** sha256 over approval + 0x00 + clear, matching `scripts/verify_build`. */
  readonly combinedDigest: string | null;
}

export class GovernFileError extends Error {}

/**
 * Decode the double-base64 `msig` blob.
 *
 * Kept separate and named for what it is, because the doubling is the single
 * most confusing thing about this format.
 */
export function decodeMsigBlob(msig: string): Uint8Array {
  let once: string;
  try {
    once = atob(msig);
  } catch {
    throw new GovernFileError('The msig field is not valid base64.');
  }
  // `atob` is lenient: it happily decodes a string that is not base64 at all
  // and returns plausible-looking bytes. That turns a single-encoded blob into
  // garbage rather than an error, which is the worse of the two failures, so
  // the shape is checked before decoding rather than after.
  if (!/^[A-Za-z0-9+/]*={0,2}$/.test(once) || once.length % 4 !== 0) {
    throw new GovernFileError(
      'The msig field decoded once but the result is not base64. This format is ' +
        'base64 of a base64 msgpack string, so a single decode is not enough. ' +
        'Check the file came from `govern create`, `update` or `freeze`.',
    );
  }
  try {
    const inner = atob(once);
    const bytes = new Uint8Array(inner.length);
    for (let i = 0; i < inner.length; i += 1) bytes[i] = inner.charCodeAt(i);
    return bytes;
  } catch {
    throw new GovernFileError('The msig field could not be decoded.');
  }
}

/** Parse and structurally validate an envelope. Throws rather than guessing. */
export function parseEnvelope(text: string): GovernEnvelope {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    throw new GovernFileError('That file is not JSON.');
  }
  if (typeof raw !== 'object' || raw === null) {
    throw new GovernFileError('That file is not a transaction envelope.');
  }
  const value = raw as Record<string, unknown>;
  for (const key of ['threshold', 'signers', 'address', 'msig'] as const) {
    if (!(key in value)) {
      throw new GovernFileError(
        `That file has no "${key}". Export it with \`govern create\`, \`govern update\` ` +
          'or `govern freeze`, which write the envelope this page reads.',
      );
    }
  }
  if (typeof value['threshold'] !== 'number' || value['threshold'] < 1) {
    throw new GovernFileError('The threshold is missing or not a positive number.');
  }
  if (!Array.isArray(value['signers']) || value['signers'].length === 0) {
    throw new GovernFileError('The signer list is missing or empty.');
  }
  if (value['threshold'] > (value['signers'] as unknown[]).length) {
    throw new GovernFileError(
      'The threshold is larger than the signer set, so this could never be signed.',
    );
  }
  return value as unknown as GovernEnvelope;
}

/** sha256 over `approval || 0x00 || clear`, as `verify_build` computes it. */
export async function combinedDigest(
  approval: Uint8Array,
  clear: Uint8Array,
): Promise<string> {
  const joined = new Uint8Array(approval.length + 1 + clear.length);
  joined.set(approval, 0);
  joined[approval.length] = 0;
  joined.set(clear, approval.length + 1);
  const hash = await crypto.subtle.digest('SHA-256', joined as unknown as BufferSource);
  return Array.from(new Uint8Array(hash))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('');
}

const ON_COMPLETE: Record<number, string> = {
  0: 'NoOp',
  1: 'OptIn',
  2: 'CloseOut',
  3: 'ClearState',
  4: 'UpdateApplication',
  5: 'DeleteApplication',
};

/**
 * Everything a holder needs, read from the blob rather than from the envelope.
 *
 * The envelope's own `address`, `threshold` and `signers` are convenience
 * fields written by whoever exported it. The blob is what will actually be
 * submitted, so the summary is derived from the blob and the envelope's copies
 * are only ever compared against it, never trusted. `checkEnvelopeAgreesWithBlob`
 * is that comparison.
 */
export async function summarise(envelope: GovernEnvelope): Promise<GovernSummary> {
  const bytes = decodeMsigBlob(envelope.msig);
  const decoded = algosdk.decodeObj(bytes) as Record<string, unknown>;
  const msig = decoded['msig'] as
    | { subsig?: Array<{ pk?: Uint8Array; s?: Uint8Array }>; thr?: number; v?: number }
    | undefined;
  if (!msig?.subsig) {
    throw new GovernFileError('That blob carries no multisig envelope.');
  }

  const signers = msig.subsig.map((entry) =>
    entry.pk ? algosdk.encodeAddress(entry.pk) : '(unknown)',
  );
  const signedBy = msig.subsig
    .map((entry, index) => (entry.s ? signers[index] : null))
    .filter((address): address is string => address !== null);

  const txn = decoded['txn'] as Record<string, unknown> | undefined;
  const approval = txn?.['apap'] as Uint8Array | undefined;
  const clear = txn?.['apsu'] as Uint8Array | undefined;
  const appIdRaw = txn?.['apid'];

  return {
    // `multisigAddress` returns an Address object, not a string. Comparing it
    // against the envelope's string field silently fails otherwise.
    address: algosdk
      .multisigAddress({
        version: msig.v ?? 1,
        threshold: msig.thr ?? envelope.threshold,
        addrs: signers,
      })
      .toString(),
    threshold: msig.thr ?? envelope.threshold,
    signers,
    signedBy,
    signatureCount: signedBy.length,
    appId: appIdRaw === undefined ? null : BigInt(appIdRaw as number | bigint),
    onComplete: ON_COMPLETE[(txn?.['apan'] as number) ?? 0] ?? 'NoOp',
    approvalBytes: approval ? approval.length : null,
    clearBytes: clear ? clear.length : null,
    combinedDigest: approval && clear ? await combinedDigest(approval, clear) : null,
  };
}

/**
 * Whether the envelope's convenience fields match the blob that will be sent.
 *
 * A mismatch is the shape a tampered file takes: readable fields saying one
 * thing so a human relaxes, and a blob doing another. Nothing here should be
 * signable while these disagree.
 */
export function checkEnvelopeAgreesWithBlob(
  envelope: GovernEnvelope,
  summary: GovernSummary,
): string[] {
  const problems: string[] = [];
  if (envelope.address !== summary.address) {
    problems.push(
      `The file says it spends from ${envelope.address}, but the transaction ` +
        `inside it spends from ${summary.address}.`,
    );
  }
  if (envelope.threshold !== summary.threshold) {
    problems.push(
      `The file says the threshold is ${envelope.threshold}; the transaction says ` +
        `${summary.threshold}.`,
    );
  }
  const declared = [...envelope.signers].join(',');
  const actual = [...summary.signers].join(',');
  if (declared !== actual) {
    problems.push(
      'The signer list in the file is not the signer list in the transaction. ' +
        'Order is part of a multisig address, so this is not a cosmetic difference.',
    );
  }
  return problems;
}

/**
 * Whether this address may enter at all.
 *
 * Entry is a real check: a holder proves key possession by signing a challenge,
 * and only the multisig's own members get in. It is not, however, what makes
 * signing safe. An attacker cannot sign as us, but they never needed to; the
 * attack is us signing their transaction with our own keys, and a gate on our
 * page does not exist on a copy of it. `combinedDigest` is the control for
 * that, and it is enforced separately.
 */
export function mayEnter(address: string | null, signers: readonly string[]): boolean {
  return address !== null && signers.includes(address);
}

/** Whether this holder has already signed, so the page can say so plainly. */
export function hasSigned(address: string | null, summary: GovernSummary): boolean {
  return address !== null && summary.signedBy.includes(address);
}

/** Whether enough signatures are present to submit. */
export function isComplete(summary: GovernSummary): boolean {
  return summary.signatureCount >= summary.threshold;
}
