/**
 * Formatting across the scales a keeper network actually spans: a fee of a
 * few thousandths of an ALGO, an upkeep that fires every 30 seconds, and one
 * that fires once a day with a month of escrow behind it.
 */

import { describe, expect, test } from 'bun:test';
import { algos, dueLabel, duration, intervalLabel, roundsAsTime, runwayLabel } from './format';

const ROUND_SECONDS = 2.8; // Algorand's nominal block time

describe('algos', () => {
  test.each([
    [0n, '0 ALGO'],
    [4_000n, '0.004 ALGO'],
    [41_300n, '0.0413 ALGO'],
    [1_000_000n, '1 ALGO'],
    [1_500_000n, '1.5 ALGO'],
    [1_234_567_000_000n, '1,234,567 ALGO'],
    [1n, '0.000001 ALGO'],
  ])('%s µALGO reads as %s', (micro, expected) => {
    expect(algos(micro)).toBe(expected);
  });

  test('signs deltas when asked', () => {
    expect(algos(4_000n, { sign: true })).toBe('+0.004 ALGO');
    expect(algos(-4_000n)).toBe('−0.004 ALGO');
  });
});

describe('duration', () => {
  test.each([
    [0, 'moments'],
    [1, '1 s'],
    [28, '28 s'],
    [59, '59 s'],
    [60, '1 min'],
    [150, '2 min 30 s'],
    [3_600, '1 h'],
    [5_400, '1 h 30 min'],
    [86_400, '1 d'],
    [108_000, '1 d 6 h'],
    [2_592_000, '30 d'],
  ])('%s seconds reads as %s', (seconds, expected) => {
    expect(duration(seconds)).toBe(expected);
  });

  test('drops the second unit once the first is large enough to carry it', () => {
    expect(duration(11 * 3_600 + 20 * 60)).toBe('11 h');
    expect(duration(40 * 86_400 + 5 * 3_600)).toBe('40 d');
  });
});

describe('rounds as time', () => {
  test.each([
    [10n, '28 s'], //          the contract's minimum interval
    [22n, '1 min'], //         about a minute
    [1_286n, '1 h'], //        hourly upkeep
    [30_857n, '1 d'], //       daily upkeep
    [216_000n, '7 d'], //      weekly upkeep
    [925_714n, '30 d'], //     monthly upkeep
  ])('%s rounds is ~%s', (count, expected) => {
    expect(roundsAsTime(count, ROUND_SECONDS)).toBe(expected);
  });

  test('says nothing when the round rate is unknown', () => {
    expect(roundsAsTime(1_286n, null)).toBeNull();
    expect(intervalLabel(1_286n, null)).toBe('1,286 rounds');
    expect(dueLabel(-42n, null)).toBe('overdue by 42 rounds');
  });
});

describe('labels', () => {
  test('interval reads at every scale', () => {
    expect(intervalLabel(10n, ROUND_SECONDS)).toBe('10 rounds · ~28 s');
    expect(intervalLabel(30_857n, ROUND_SECONDS)).toBe('30,857 rounds · ~1 d');
  });

  test('due reads before, at, and after the due round', () => {
    expect(dueLabel(1_286n, ROUND_SECONDS)).toBe('in ~1 h');
    expect(dueLabel(0n, ROUND_SECONDS)).toBe('due now');
    expect(dueLabel(-30_857n, ROUND_SECONDS)).toBe('overdue by ~1 d');
  });

  test('runway turns escrow into time', () => {
    // 30 daily runs left ≈ a month of unattended operation.
    expect(runwayLabel(30n, 30_857n, ROUND_SECONDS)).toBe('30 runs · ~30 d');
    expect(runwayLabel(1n, 10n, ROUND_SECONDS)).toBe('1 run · ~28 s');
    expect(runwayLabel(0n, 10n, ROUND_SECONDS)).toBe('empty');
  });
});
