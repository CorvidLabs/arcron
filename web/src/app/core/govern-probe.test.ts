/**
 * A probe that cannot tell a refusal from a fault is worse than no probe.
 *
 * The point of the exercise is to learn whether wallets will sign a transaction
 * whose sender is a multisig address. That question is only answered by a
 * wallet saying no *for that reason*. A closed popup, a wrong network or a
 * dropped connection says nothing about multisig support, and recording one as
 * the other would put a false conclusion into a report that people then act on.
 *
 * So these tests are about the classification, and about `usable` staying
 * narrow enough to be worth trusting.
 */

import { describe, expect, test } from 'bun:test';

import { classify, describeResult, summariseProbe, type ProbeResult } from './govern-probe';

function result(over: Partial<ProbeResult> = {}): ProbeResult {
  return {
    wallet: 'Pera',
    address: 'WGSHC4TYKYBS6EX5V5E377BQDLKWIIPBCFOLZQZIXCKHFIEKRPBFOMW25A',
    outcome: 'signed',
    detail: null,
    signature: new Uint8Array(64),
    ...over,
  };
}

describe('telling a refusal from a fault', () => {
  test.each([
    'User rejected the request',
    'Request declined by user',
    'Signing denied',
    'Transaction cancelled',
    'Unknown account for this wallet',
    'Cannot sign for that address',
    'Multisig is unsupported',
  ])('%s reads as a refusal', (message) => {
    expect(classify(new Error(message)).outcome).toBe('refused');
  });

  test.each([
    'Network request failed',
    'WebSocket connection closed',
    'Timed out waiting for the wallet',
    'Wrong network: expected testnet',
  ])('%s reads as an error, not a refusal', (message) => {
    // These are all about the connection or the environment. Counting them as
    // refusals would report "wallets do not support multisig" on the strength
    // of a flaky socket.
    expect(classify(new Error(message)).outcome).toBe('errored');
  });

  test('something that is not an Error still classifies', () => {
    expect(classify('user rejected').outcome).toBe('refused');
    expect(classify(42).outcome).toBe('errored');
  });

  test('the wallet’s own words are kept', () => {
    // A report that paraphrases loses the detail somebody needs to file an
    // issue against the wallet.
    expect(classify(new Error('User rejected the request')).detail).toBe(
      'User rejected the request',
    );
  });
});

describe('what counts as usable', () => {
  test('only a real 64 byte signature does', () => {
    const found = summariseProbe([result()]);
    expect(found.usable).toEqual(['Pera']);
  });

  test('a claimed success with a short signature does not', () => {
    // If a wallet returns something signature-shaped but wrong, telling a
    // holder to rely on it would be worse than reporting nothing.
    const found = summariseProbe([result({ signature: new Uint8Array(32) })]);
    expect(found.usable).toEqual([]);
  });

  test('a claimed success with no signature does not', () => {
    expect(summariseProbe([result({ signature: null })]).usable).toEqual([]);
  });

  test('an untried wallet is neither usable nor refusing', () => {
    const found = summariseProbe([result({ outcome: 'not-tried', signature: null })]);
    expect(found.usable).toEqual([]);
    expect(found.refused).toEqual([]);
    expect(found.unclear).toEqual([]);
  });
});

describe('the verdict a person reads', () => {
  test('names the wallets that worked, and what the others should do', () => {
    const { verdict } = summariseProbe([
      result({ wallet: 'Lute' }),
      result({ wallet: 'Pera', outcome: 'refused', detail: 'no', signature: null }),
    ]);
    expect(verdict).toContain('Lute');
    expect(verdict).toContain('command line');
  });

  test('a clean sweep of refusals is stated as a finding', () => {
    const { verdict } = summariseProbe([
      result({ wallet: 'Pera', outcome: 'refused', detail: 'no', signature: null }),
      result({ wallet: 'Defly', outcome: 'refused', detail: 'no', signature: null }),
    ]);
    expect(verdict).toContain('not available');
  });

  test('all errors and no refusals is explicitly inconclusive', () => {
    // The failure mode worth guarding: two broken connections must not be
    // reported as evidence that wallets refuse.
    const { verdict } = summariseProbe([
      result({ wallet: 'Pera', outcome: 'errored', detail: 'socket', signature: null }),
      result({ wallet: 'Defly', outcome: 'errored', detail: 'socket', signature: null }),
    ]);
    expect(verdict).toContain('Nothing conclusive');
    expect(verdict).not.toContain('not available');
  });
});

describe('the line a person pastes into an issue', () => {
  test('a success says how many bytes came back', () => {
    expect(describeResult(result())).toContain('64 byte signature');
  });

  test('an error is labelled as not a refusal', () => {
    const line = describeResult(result({ outcome: 'errored', detail: 'socket', signature: null }));
    expect(line).toContain('not the same as refusing');
  });
});
