/**
 * The TypeScript decoder must agree with the Python one, byte for byte.
 *
 * The vector is the same recorded box used by tests/test_keeper_bot.py —
 * upkeep 0 on LocalNet app 18775 after its first execution. Every field the
 * 1.0 batch added holds a non-zero value: SKIP_AHEAD, a 12,000 µALGO ceiling,
 * a three-argument call, and an ASA bonus that was actually paid. If the
 * struct changes, both tests change together.
 */

import { describe, expect, test } from 'bun:test';
import {
  BOX_MBR_FIXED,
  SKIP_AHEAD,
  boxMbr,
  decodeUpkeep,
  encodeCallArgs,
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
  '0000000000004959' + // target_app = 18777
  '0082' + //             tail offset = 130
  '000000000000000a' + // interval_rounds = 10
  '0000000000003698' + // next_execution_round = 13976
  '0000000000000fa0' + // fee_per_execution = 4000
  '0000000000008980' + // balance = 35200
  '0000000000000001' + // times_executed = 1
  '0000000000000001' + // policy = SKIP_AHEAD
  '0000000000002ee0' + // fee_cap = 12000
  '000000000000368f' + // last_serviced_round = 13967
  '000000000000495a' + // fee_asset = 18778
  '000000000003d090' + // asset_fee = 250000
  '00000000000b71b0' + // asset_balance = 750000
  // tail: byte[][] of absorb(uint64,string)'s selector, 7777 and "archon"
  '00030006000c00160004cb782a4800080000000000001e6100080006617263686f6e';

function fromHex(hex: string): Uint8Array {
  return Uint8Array.from(hex.match(/.{2}/g)!.map((byte) => parseInt(byte, 16)));
}

describe('decodeUpkeep', () => {
  const upkeep = decodeUpkeep(0n, fromHex(LIVE_BOX_HEX));

  test('reads every field of a real box', () => {
    expect(upkeep.id).toBe(0n);
    expect(upkeep.targetApp).toBe(18777n);
    expect(upkeep.intervalRounds).toBe(10n);
    expect(upkeep.nextExecutionRound).toBe(13976n);
    expect(upkeep.feePerExecution).toBe(4000n);
    expect(upkeep.balance).toBe(35200n);
    expect(upkeep.timesExecuted).toBe(1n);
    expect(upkeep.policy).toBe(SKIP_AHEAD);
    expect(upkeep.feeCap).toBe(12000n);
    expect(upkeep.lastServicedRound).toBe(13967n);
    expect(upkeep.feeAsset).toBe(18778n);
    expect(upkeep.assetFee).toBe(250_000n);
    expect(upkeep.assetBalance).toBe(750_000n);
  });

  test('reads the fields the Python decoder skips', () => {
    expect(upkeep.creator).toBe('LXX2CZ7IFVUIFMNFPPVX2O5YLA2EBIXC4GNCONMMSR2EUT5H4PHZ53VNOQ');
    expect(upkeep.callArgs.map(toHex)).toEqual([
      'cb782a48', // absorb(uint64,string) selector
      '0000000000001e61', // 7777
      '0006617263686f6e', // "archon"
    ]);
  });

  test('round-trips the argument list through the encoder', () => {
    expect(toHex(encodeCallArgs(upkeep.callArgs))).toBe(
      toHex(fromHex(LIVE_BOX_HEX).subarray(130)),
    );
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
    // 35,200 µALGO of escrow buys two runs at the 12,000 ceiling, not eight
    // at the fee its creator wrote down.
    expect(executionsRemaining(upkeep)).toBe(2n);
  });
});

describe('derived state', () => {
  const upkeep = decodeUpkeep(0n, fromHex(LIVE_BOX_HEX));

  test('counts remaining executions at the price it can be charged', () => {
    expect(executionsRemaining(upkeep)).toBe(2n); // 35200 / the 12000 cap
    expect(executionsRemaining({ ...upkeep, feeCap: 0n })).toBe(8n); // 35200 / 4000
  });

  test('is executable once the due round passes', () => {
    expect(isExecutable(upkeep, 13975n)).toBe(false);
    expect(isExecutable(upkeep, 13976n)).toBe(true);
    expect(roundsUntilDue(upkeep, 13966n)).toBe(10n);
    expect(roundsUntilDue(upkeep, 13986n)).toBe(-10n);
  });

  test('is not executable when the escrow cannot cover a fee', () => {
    expect(isExecutable({ ...upkeep, balance: 3_999n }, 13976n)).toBe(false);
  });

  test('an escrow that cannot reach the ceiling pays base and stays executable', () => {
    // 5,000 µALGO clears the base fee but not the escalated one, so the
    // contract charges base rather than freezing the upkeep for good.
    const thin = { ...upkeep, balance: 5_000n };
    expect(isExecutable(thin, 13976n)).toBe(true);
    expect(isExecutable(thin, 13967n + 20n)).toBe(true);
    expect(effectiveFee(thin, 13967n + 20n)).toBe(4_000n);
    // Below the base fee there is nothing anyone can be paid.
    expect(isExecutable({ ...upkeep, balance: 3_999n }, 13976n)).toBe(false);
  });

  test('a replay of a backlog never escalates', () => {
    const replay = { ...upkeep, nextExecutionRound: 13_900n, lastServicedRound: 13_967n };
    expect(effectiveFee(replay, 15_000n)).toBe(4_000n);
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
    expect(BOX_MBR_FIXED).toBe(2_500 + 400 * 139);
    // A bare 4-byte selector, as charged on-chain.
    expect(boxMbr([new Uint8Array(4)])).toBe(62_100);
    // ...and what the recorded three-argument box above actually costs.
    const recorded = fromHex(LIVE_BOX_HEX);
    expect(2_500 + 400 * (9 + recorded.length)).toBe(boxMbr(decodeUpkeep(0n, recorded).callArgs));
  });
});
