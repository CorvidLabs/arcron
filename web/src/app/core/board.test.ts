/**
 * What a keeper is shown, and the arithmetic behind it.
 *
 * Every figure here comes from box state alone — no indexer — including what
 * keepers have been paid, which is why the board can ship without any backend.
 */

import { describe, expect, test } from 'bun:test';
import { classify, sortEntries, summarise, toEntry } from './board';
import type { Upkeep } from './upkeep';

function upkeep(overrides: Partial<Upkeep> = {}): Upkeep {
  return {
    id: 1n,
    creator: 'E5M2OH5XNDMNABJ6VOFOUVR2IKRPCGQH43PVC5P3DWQQ2LV2VJV2FJZQ3E',
    targetApp: 1043n,
    callData: new Uint8Array([0x4d, 0x4d, 0x5f, 0x0b]),
    intervalRounds: 10n,
    nextExecutionRound: 1_000n,
    feePerExecution: 4_000n,
    balance: 12_000n,
    timesExecuted: 0n,
    policy: 0n,
    feeCap: 0n,
    lastServicedRound: 990n,
    ...overrides,
  };
}

describe('availability', () => {
  test('due once its round has passed', () => {
    expect(classify(upkeep(), 999n)).toBe('scheduled');
    expect(classify(upkeep(), 1_000n)).toBe('due');
    expect(classify(upkeep(), 5_000n)).toBe('due');
  });

  test('an upkeep that cannot pay its fee is not a keeper opportunity', () => {
    // Dormant beats due: however overdue it looks, no keeper can execute it.
    const starved = upkeep({ balance: 3_999n });
    expect(classify(starved, 99_999n)).toBe('dormant');
  });
});

describe('what a keeper is offered', () => {
  test('the reward is net of what executing costs them', () => {
    // 4,000 fee less the 1,000 outer + 2,000 pooled extra they pay themselves.
    expect(toEntry(upkeep(), 1_000n).netReward).toBe(1_000n);
    expect(toEntry(upkeep({ feePerExecution: 10_000n }), 1_000n).netReward).toBe(7_000n);
  });

  test('overdue is zero before the due round, never negative', () => {
    expect(toEntry(upkeep(), 900n).overdueRounds).toBe(0n);
    expect(toEntry(upkeep(), 1_050n).overdueRounds).toBe(50n);
  });

  test('last execution is unknown until it has run once', () => {
    expect(toEntry(upkeep(), 1_000n).lastExecutionRound).toBeNull();
    expect(toEntry(upkeep({ timesExecuted: 3n }), 1_000n).lastExecutionRound).toBe(990n);
  });

  test('runway is whole runs, not fractions', () => {
    expect(toEntry(upkeep({ balance: 11_999n }), 1_000n).runsRemaining).toBe(2n);
  });
});

describe('sorting', () => {
  const entries = [
    toEntry(upkeep({ id: 1n, feePerExecution: 4_000n, intervalRounds: 100n, balance: 40_000n }), 1_200n),
    toEntry(upkeep({ id: 2n, feePerExecution: 9_000n, intervalRounds: 10n, balance: 9_000n }), 1_200n),
    toEntry(upkeep({ id: 3n, feePerExecution: 6_000n, intervalRounds: 50n, balance: 60_000n, nextExecutionRound: 1_100n }), 1_200n),
  ];

  test('by reward, best first', () => {
    expect(sortEntries(entries, 'reward').map((e) => e.upkeep.id)).toEqual([2n, 3n, 1n]);
  });

  test('by how overdue, worst first', () => {
    expect(sortEntries(entries, 'overdue').map((e) => e.upkeep.id)).toEqual([1n, 2n, 3n]);
  });

  test('by runway, most urgent first', () => {
    // Fewest runs left is the one about to go dormant.
    expect(sortEntries(entries, 'runway').map((e) => e.upkeep.id)).toEqual([2n, 1n, 3n]);
  });

  test('by cadence, fastest first', () => {
    expect(sortEntries(entries, 'cadence').map((e) => e.upkeep.id)).toEqual([2n, 3n, 1n]);
  });

  test('ties fall back to id rather than an arbitrary order', () => {
    const tied = [
      toEntry(upkeep({ id: 7n }), 1_000n),
      toEntry(upkeep({ id: 2n }), 1_000n),
      toEntry(upkeep({ id: 5n }), 1_000n),
    ];
    expect(sortEntries(tied, 'reward').map((e) => e.upkeep.id)).toEqual([2n, 5n, 7n]);
  });

  test('does not mutate what it was given', () => {
    const original = [...entries];
    sortEntries(entries, 'reward');
    expect(entries).toEqual(original);
  });
});

describe('network stats, from box state alone', () => {
  const entries = [
    toEntry(upkeep({ id: 1n, timesExecuted: 10n, feePerExecution: 4_000n, balance: 8_000n }), 1_050n),
    toEntry(upkeep({ id: 2n, timesExecuted: 3n, feePerExecution: 9_000n, balance: 90_000n, nextExecutionRound: 1_040n }), 1_050n),
    toEntry(upkeep({ id: 3n, timesExecuted: 0n, feePerExecution: 4_000n, balance: 100n }), 1_050n),
  ];
  const stats = summarise(entries);

  test('counts what is due and what is stuck', () => {
    expect(stats.upkeeps).toBe(3);
    expect(stats.due).toBe(2);
    expect(stats.dormant).toBe(1);
  });

  test('derives what keepers have been paid without any history', () => {
    // 10 × 4,000 + 3 × 9,000 — no indexer, no transaction log.
    expect(stats.totalExecutions).toBe(13n);
    expect(stats.paidToKeepers).toBe(67_000n);
  });

  test('median lateness ignores upkeeps nobody can execute', () => {
    // Due: 50 rounds and 10 rounds late. The dormant one is not a keeper's fault.
    expect(stats.medianLateness).toBe(10n);
  });

  test('an empty board has no median rather than a misleading zero-ish average', () => {
    expect(summarise([]).medianLateness).toBe(0n);
    expect(summarise([]).paidToKeepers).toBe(0n);
  });
});

describe('escalation on the board', () => {
  // Base 4,000, ceiling 12,000, 10-round interval, last serviced at round 990.
  const escalating = upkeep({ feeCap: 12_000n, balance: 100_000n });

  test('ranks work by what it pays now, not what it was registered at', () => {
    const rich = toEntry(upkeep({ id: 2n, feePerExecution: 6_000n }), 1_000n);
    const late = toEntry(escalating, 1_010n); // a whole interval past its service
    expect(late.currentFee).toBe(12_000n);
    expect(late.escalated).toBe(true);

    const [first] = sortEntries([rich, late], 'reward');
    expect(first.upkeep.id).toBe(1n);
  });

  test('is not escalated while it is on time', () => {
    const onTime = toEntry(escalating, 1_000n);
    expect(onTime.currentFee).toBe(4_000n);
    expect(onTime.escalated).toBe(false);
  });

  test('an upkeep can go dormant at a balance that covers its base fee', () => {
    // Eight runs at the price the creator wrote down — but not one at the cap.
    const thin = upkeep({ feeCap: 12_000n, balance: 8_000n });
    expect(classify(thin, 1_000n)).toBe('due');
    expect(classify(thin, 1_010n)).toBe('dormant');
  });

  test('reads when an upkeep last ran instead of deriving it from the schedule', () => {
    // A catching-up upkeep's schedule and its service differ by the backlog;
    // deriving this from nextExecutionRound - interval is what broke #27.
    const caughtUp = upkeep({ timesExecuted: 3n, nextExecutionRound: 1_010n, lastServicedRound: 1_400n });
    expect(toEntry(caughtUp, 1_400n).lastExecutionRound).toBe(1_400n);
    expect(toEntry(upkeep(), 1_000n).lastExecutionRound).toBeNull();
  });

  test('what keepers have been paid is a floor once fees can escalate', () => {
    const stats = summarise([toEntry(upkeep({ timesExecuted: 2n, feeCap: 12_000n }), 1_000n)]);
    expect(stats.paidToKeepers).toBe(8_000n); // 2 × the base fee
  });
});
