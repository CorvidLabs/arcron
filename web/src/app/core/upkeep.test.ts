/**
 * The TypeScript decoder must agree with the Python one, byte for byte.
 *
 * The vector is the same recorded box used by tests/test_keeper_bot.py —
 * upkeep 0 on LocalNet app 11172 after its first execution, registered with
 * SKIP_AHEAD and a 12,000 µALGO cap so that all three fields added by #7 and
 * #14 hold non-zero values. If the struct changes, both tests change together.
 */

import { describe, expect, test } from 'bun:test';
import {
  BOX_MBR_FIXED,
  SKIP_AHEAD,
  boxMbr,
  decodeUpkeep,
  effectiveFee,
  escalates,
  executionsRemaining,
  isExecutable,
  roundsUntilDue,
  toHex,
  upkeepBoxName,
  upkeepIdFromBoxName,
} from './upkeep';

const LIVE_BOX_HEX =
  '5defa167e82d6882b1a57beb7d3bb8583440a2e2e19a27358c94744a4fa7e3cf' +
  '0000000000000413' + // target_app = 1043
  '006a' + //             tail offset = 106
  '000000000000000a' + // interval_rounds = 10
  '0000000000001eda' + // next_execution_round = 7898
  '0000000000000fa0' + // fee_per_execution = 4000
  '0000000000005aa0' + // balance = 23200
  '0000000000000001' + // times_executed = 1
  '0000000000000001' + // policy = SKIP_AHEAD
  '0000000000002ee0' + // fee_cap = 12000
  '0000000000001ed1' + // last_serviced_round = 7889
  '00044d4d5f0b'; //      tail: uint16 length 4 + tick()uint64 selector

function fromHex(hex: string): Uint8Array {
  return Uint8Array.from(hex.match(/.{2}/g)!.map((byte) => parseInt(byte, 16)));
}

describe('decodeUpkeep', () => {
  const upkeep = decodeUpkeep(0n, fromHex(LIVE_BOX_HEX));

  test('reads every field of a real box', () => {
    expect(upkeep.id).toBe(0n);
    expect(upkeep.targetApp).toBe(1043n);
    expect(upkeep.intervalRounds).toBe(10n);
    expect(upkeep.nextExecutionRound).toBe(7898n);
    expect(upkeep.feePerExecution).toBe(4000n);
    expect(upkeep.balance).toBe(23200n);
    expect(upkeep.timesExecuted).toBe(1n);
    expect(upkeep.policy).toBe(SKIP_AHEAD);
    expect(upkeep.feeCap).toBe(12000n);
    expect(upkeep.lastServicedRound).toBe(7889n);
  });

  test('reads the fields the Python decoder skips', () => {
    expect(upkeep.creator).toBe('LXX2CZ7IFVUIFMNFPPVX2O5YLA2EBIXC4GNCONMMSR2EUT5H4PHZ53VNOQ');
    expect(toHex(upkeep.callData)).toBe('4d4d5f0b');
  });

  test('rejects a truncated box rather than decoding garbage', () => {
    expect(() => decodeUpkeep(0n, fromHex(LIVE_BOX_HEX).subarray(0, 40))).toThrow();
  });
});

describe('effectiveFee', () => {
  const upkeep = decodeUpkeep(0n, fromHex(LIVE_BOX_HEX));
  const serviced = upkeep.lastServicedRound;

  test('rises linearly from the base to the cap over one missed interval', () => {
    expect(effectiveFee(upkeep, serviced + 10n)).toBe(4000n);
    expect(effectiveFee(upkeep, serviced + 15n)).toBe(8000n);
    expect(effectiveFee(upkeep, serviced + 20n)).toBe(12000n);
  });

  test('holds at the cap however late it gets', () => {
    expect(effectiveFee(upkeep, serviced + 10_000n)).toBe(12000n);
  });

  test('is measured from the last service, so a drained backlog pays base', () => {
    expect(effectiveFee(upkeep, serviced)).toBe(4000n);
  });

  test('never moves when no cap is set', () => {
    const flat = { ...upkeep, feeCap: 0n };
    expect(escalates(flat)).toBe(false);
    expect(effectiveFee(flat, serviced + 10_000n)).toBe(4000n);
  });

  test('prices the runway at the worst case the creator can be charged', () => {
    // 23,200 µALGO of escrow buys one run at the 12,000 cap, not five at base.
    expect(executionsRemaining(upkeep)).toBe(1n);
  });
});

describe('derived state', () => {
  const upkeep = decodeUpkeep(0n, fromHex(LIVE_BOX_HEX));

  test('counts remaining executions at the price it can be charged', () => {
    expect(executionsRemaining(upkeep)).toBe(1n); // 23200 / the 12000 cap
    expect(executionsRemaining({ ...upkeep, feeCap: 0n })).toBe(5n); // 23200 / 4000
  });

  test('is executable once the due round passes', () => {
    expect(isExecutable(upkeep, 7897n)).toBe(false);
    expect(isExecutable(upkeep, 7898n)).toBe(true);
    expect(roundsUntilDue(upkeep, 7888n)).toBe(10n);
    expect(roundsUntilDue(upkeep, 7908n)).toBe(-10n);
  });

  test('is not executable when the escrow cannot cover a fee', () => {
    expect(isExecutable({ ...upkeep, balance: 3_999n }, 7898n)).toBe(false);
  });

  test('an escrow that cannot reach the ceiling pays base and stays executable', () => {
    // 5,000 µALGO clears the base fee but not the escalated one, so the
    // contract charges base rather than freezing the upkeep for good.
    const thin = { ...upkeep, balance: 5_000n };
    expect(isExecutable(thin, 7898n)).toBe(true);
    expect(isExecutable(thin, 7889n + 20n)).toBe(true);
    expect(effectiveFee(thin, 7889n + 20n)).toBe(4_000n);
    // Below the base fee there is nothing anyone can be paid.
    expect(isExecutable({ ...upkeep, balance: 3_999n }, 7898n)).toBe(false);
  });

  test('a replay of a backlog never escalates', () => {
    const replay = { ...upkeep, nextExecutionRound: 7_800n, lastServicedRound: 7_889n };
    expect(effectiveFee(replay, 9_000n)).toBe(4_000n);
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
    expect(BOX_MBR_FIXED).toBe(2_500 + 400 * 117);
    expect(boxMbr(4)).toBe(50_900); // a 4-byte selector, as charged on-chain
    // ...and what the recorded box above actually costs.
    expect(2_500 + 400 * (9 + fromHex(LIVE_BOX_HEX).length)).toBe(boxMbr(4));
  });
});
