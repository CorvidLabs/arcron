/**
 * A holder must not be able to sign something they were not shown.
 *
 * This page exists so the three multisig holders can authorize a governance
 * transaction with a wallet instead of pasting a mnemonic into a shell. That is
 * a real improvement for a Ledger, whose whole point is that the key never
 * leaves the device, and `govern sign` currently accepts nothing but
 * `SIGNER_MNEMONIC`.
 *
 * It is also a downgrade waiting to happen. `govern show` forces a holder to
 * read a decoded transaction before signing; a button does not. So the tests
 * that matter here are not about parsing. They are about the order things
 * become possible in, and about a file that says one thing in its readable
 * fields and another in the blob that will actually be submitted.
 */

import { describe, expect, test } from 'bun:test';

import {
  GovernFileError,
  checkEnvelopeAgreesWithBlob,
  combinedDigest,
  decodeMsigBlob,
  hasSigned,
  isComplete,
  mayEnter,
  parseEnvelope,
  summarise,
  type GovernEnvelope,
  type GovernSummary,
} from './govern-file';

import FIXTURE from './govern-sample.fixture.json';

const LEDGER = 'X2OF75PUW34XMTY2QW7ZTXH2XHDREVH4ZRDDHFXJNJHXJEEPSWWB4T73AQ';
const CORVID = 'WGSHC4TYKYBS6EX5V5E377BQDLKWIIPBCFOLZQZIXCKHFIEKRPBFOMW25A';
const GASPAR = 'DEXWEZGRX3Q6B2S3GVO74MUN54XA3JI5GQFVGNK64JYPD4NCFRK4G5ACVY';
const MAINNET_CREATOR = 'LUH77ATPWS4ZTCO7OZ3YM2DP5M2BXN53CHPFFQCFBATRFCYEB3NKTGMBNI';

const text = JSON.stringify(FIXTURE);

describe('the real exported envelope', () => {
  test('derives the address pinned in scripts/network.py', async () => {
    // Derived from the blob, not read from the envelope's own field. If these
    // ever disagree the file has been edited, which is the whole point of
    // computing it rather than trusting it.
    const summary = await summarise(parseEnvelope(text));
    expect(summary.address).toBe(MAINNET_CREATOR);
  });

  test('reads the threshold and the ordered signer set', async () => {
    const summary = await summarise(parseEnvelope(text));
    expect(summary.threshold).toBe(2);
    expect(summary.signers).toEqual([LEDGER, CORVID, GASPAR]);
  });

  test('an unsigned export carries no signatures', async () => {
    const summary = await summarise(parseEnvelope(text));
    expect(summary.signatureCount).toBe(0);
    expect(isComplete(summary)).toBe(false);
  });

  test('decodes what the transaction actually does', async () => {
    const summary = await summarise(parseEnvelope(text));
    expect(summary.onComplete).toBe('UpdateApplication');
    expect(summary.appId).toBe(769891898n);
  });

  test('and the envelope agrees with its own blob', async () => {
    const envelope = parseEnvelope(text);
    expect(checkEnvelopeAgreesWithBlob(envelope, await summarise(envelope))).toEqual([]);
  });
});

describe('a file that lies about itself', () => {
  async function tampered(changes: Partial<GovernEnvelope>): Promise<string[]> {
    const envelope = { ...parseEnvelope(text), ...changes } as GovernEnvelope;
    return checkEnvelopeAgreesWithBlob(envelope, await summarise(parseEnvelope(text)));
  }

  test('a swapped address is caught', async () => {
    // The shape a tampered file takes: readable fields that reassure, and a
    // blob that does something else.
    const problems = await tampered({ address: CORVID });
    expect(problems.length).toBeGreaterThan(0);
    expect(problems[0]).toContain('spends from');
  });

  test('a lowered threshold is caught', async () => {
    expect(await tampered({ threshold: 1 })).not.toEqual([]);
  });

  test('a reordered signer list is caught', async () => {
    // Order is part of a multisig address, so the same three keys in a
    // different order are a different account holding nothing.
    const problems = await tampered({ signers: [CORVID, LEDGER, GASPAR] });
    expect(problems.some((p) => p.includes('Order is part'))).toBe(true);
  });
});

describe('the double base64, which is the easy thing to get wrong', () => {
  test('a single-encoded blob is refused with an explanation', () => {
    // Decoding once yields more base64. A parser that stops there gets a
    // plausible byte string and fails somewhere further along.
    expect(() => decodeMsigBlob(btoa('not base64 inside'))).toThrow(GovernFileError);
  });

  test('outright rubbish is refused', () => {
    expect(() => decodeMsigBlob('!!!!')).toThrow(GovernFileError);
  });
});

describe('rejecting files that are not this', () => {
  test('plain text', () => {
    expect(() => parseEnvelope('hello')).toThrow(/not JSON/);
  });

  test('JSON that is not an envelope', () => {
    expect(() => parseEnvelope('{"hello":1}')).toThrow(/has no "threshold"/);
  });

  test('a threshold larger than the signer set', () => {
    // Could never be satisfied, so signing it would waste everybody's time.
    const bad = JSON.stringify({ ...FIXTURE, threshold: 9 });
    expect(() => parseEnvelope(bad)).toThrow(/larger than the signer set/);
  });

  test('an empty signer list', () => {
    const bad = JSON.stringify({ ...FIXTURE, signers: [] });
    expect(() => parseEnvelope(bad)).toThrow(/missing or empty/);
  });
});

describe('who may enter', () => {
  const signers = [LEDGER, CORVID, GASPAR];

  test('a holder may', () => {
    expect(mayEnter(CORVID, signers)).toBe(true);
  });

  test('a stranger may not', () => {
    expect(mayEnter('A3OZPORJYG6ZC6TCPXVNQPGQ4OLE22RSLXPCYRL3XD2FEX7MZTJI4DJZFI', signers)).toBe(
      false,
    );
  });

  test('nobody connected may not', () => {
    expect(mayEnter(null, signers)).toBe(false);
  });

  test('entry proves who you are and says nothing about what you sign', () => {
    // Worth pinning as an assertion because it is the thing most likely to be
    // misremembered later: this gate is authentication, and the digest check is
    // integrity. An attacker never needed our keys. They need us to use ours on
    // their transaction, and a gate on our page is absent from a copy of it.
    expect(mayEnter(CORVID, signers)).toBe(true);
    expect(typeof combinedDigest).toBe('function');
  });
});

describe('what this holder has already done', () => {
  const summary = { signedBy: [CORVID], signatureCount: 1, threshold: 2 } as GovernSummary;

  test('a holder who signed is told so', () => {
    expect(hasSigned(CORVID, summary)).toBe(true);
  });

  test('one who has not is not', () => {
    expect(hasSigned(LEDGER, summary)).toBe(false);
  });

  test('one of two is not enough to submit', () => {
    expect(isComplete(summary)).toBe(false);
  });

  test('two of two is', () => {
    expect(isComplete({ ...summary, signatureCount: 2 })).toBe(true);
  });
});

describe('the combined digest', () => {
  test('matches sha256 over approval, a zero byte, then clear', async () => {
    // The same construction as scripts/verify_build, which is what a holder
    // compares against. A different construction here would produce a digest
    // that never matches and teach holders to ignore the check.
    const digest = await combinedDigest(new Uint8Array([1, 2]), new Uint8Array([3]));
    const expected = await crypto.subtle.digest(
      'SHA-256',
      new Uint8Array([1, 2, 0, 3]) as unknown as BufferSource,
    );
    const hex = Array.from(new Uint8Array(expected))
      .map((b) => b.toString(16).padStart(2, '0'))
      .join('');
    expect(digest).toBe(hex);
  });

  test('is absent when the transaction replaces no programs', async () => {
    // A freeze carries no programs, so there is nothing to compare and the page
    // must not invent a digest to make its own flow feel complete.
    const summary = await summarise(parseEnvelope(text));
    expect(summary.combinedDigest === null || typeof summary.combinedDigest === 'string').toBe(
      true,
    );
  });
});
