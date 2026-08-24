/**
 * The TypeScript decoder must agree with the Python one, byte for byte.
 *
 * The vector is the same live TestNet box used by tests/test_keeper_bot.py —
 * upkeep 4 on app 769772891 after its first execution. If the contract's
 * Upkeep struct changes, both tests must change together.
 */

import { describe, expect, test } from 'bun:test';
import {
  BOX_MBR_FIXED,
  boxMbr,
  decodeUpkeep,
  executionsRemaining,
  isExecutable,
  roundsUntilDue,
  toHex,
  upkeepBoxName,
  upkeepIdFromBoxName,
} from './upkeep';

const LIVE_BOX_HEX =
  '2759a71fb768d8d0053eab8aea563a42a2f11a07e6df5175fb1da10d2ebaaa6b' +
  '000000002de1cd6a' + // target_app = 769772906
  '0052' + //             tail offset = 82
  '000000000000000a' + // interval_rounds = 10
  '0000000003f864f3' + // next_execution_round = 66610419
  '0000000000000fa0' + // fee_per_execution = 4000
  '0000000000003e80' + // balance = 16000
  '0000000000000001' + // times_executed = 1
  '00044d4d5f0b'; //      tail: uint16 length 4 + tick()uint64 selector

function fromHex(hex: string): Uint8Array {
  return Uint8Array.from(hex.match(/.{2}/g)!.map((byte) => parseInt(byte, 16)));
}

describe('decodeUpkeep', () => {
  const upkeep = decodeUpkeep(4n, fromHex(LIVE_BOX_HEX));

  test('reads every field of a real box', () => {
    expect(upkeep.id).toBe(4n);
    expect(upkeep.targetApp).toBe(769772906n);
    expect(upkeep.intervalRounds).toBe(10n);
    expect(upkeep.nextExecutionRound).toBe(66610419n);
    expect(upkeep.feePerExecution).toBe(4000n);
    expect(upkeep.balance).toBe(16000n);
    expect(upkeep.timesExecuted).toBe(1n);
  });

  test('reads the fields the Python decoder skips', () => {
    expect(upkeep.creator).toBe('E5M2OH5XNDMNABJ6VOFOUVR2IKRPCGQH43PVC5P3DWQQ2LV2VJV2FJZQ3E');
    expect(toHex(upkeep.callData)).toBe('4d4d5f0b');
  });

  test('rejects a truncated box rather than decoding garbage', () => {
    expect(() => decodeUpkeep(4n, fromHex(LIVE_BOX_HEX).subarray(0, 40))).toThrow();
  });
});

describe('derived state', () => {
  const upkeep = decodeUpkeep(4n, fromHex(LIVE_BOX_HEX));

  test('counts remaining executions', () => {
    expect(executionsRemaining(upkeep)).toBe(4n); // 16000 / 4000
  });

  test('is executable once the due round passes', () => {
    expect(isExecutable(upkeep, 66610418n)).toBe(false);
    expect(isExecutable(upkeep, 66610419n)).toBe(true);
    expect(roundsUntilDue(upkeep, 66610409n)).toBe(10n);
    expect(roundsUntilDue(upkeep, 66610429n)).toBe(-10n);
  });

  test('is not executable when the escrow cannot cover a fee', () => {
    expect(isExecutable({ ...upkeep, balance: 3_999n }, 66610419n)).toBe(false);
  });
});

describe('box names', () => {
  test('round-trip', () => {
    expect(toHex(upkeepBoxName(4))).toBe('750000000000000004');
    expect(upkeepIdFromBoxName(upkeepBoxName(4n))).toBe(4n);
  });

  test('ignores boxes that are not upkeeps', () => {
    expect(upkeepIdFromBoxName(new Uint8Array([0x78, 1, 2, 3, 4, 5, 6, 7, 8]))).toBeNull();
  });
});

describe('box MBR', () => {
  test('matches the contract formula', () => {
    expect(BOX_MBR_FIXED).toBe(2_500 + 400 * 93);
    expect(boxMbr(4)).toBe(41_300); // a 4-byte selector, as charged on-chain
  });
});
