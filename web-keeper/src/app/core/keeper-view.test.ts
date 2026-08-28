/**
 * The dashboard must not tell an operator their keeper is broken when it isn't.
 *
 * This app reads the chain and nothing else, so it can only ever report what a
 * keeper *did*. A bot that is running fine on an idle registry produces
 * exactly the same silence as a bot that has been killed, and a dashboard that
 * cannot tell those apart, but claims to, is worse than one that says so.
 *
 * That is what most of these tests are about.
 */

import { describe, expect, test } from 'bun:test';
import type { BoardEntry } from '@corvidlabs/arcron/board';

import {
    SILENT_ROUNDS_STOPPED,
    SILENT_ROUNDS_WARNING,
    claimableNow,
    humanRounds,
    liveness,
    livenessReason,
    nextBest,
    unprofitable,
} from './keeper-view';

function entry(over: Partial<BoardEntry> = {}): BoardEntry {
    return {
        upkeep: { id: 1n } as BoardEntry['upkeep'],
        availability: 'due',
        overdueRounds: 0n,
        netReward: 7_000n,
        currentFee: 10_000n,
        escalated: false,
        runsRemaining: 10n,
        lastExecutionRound: null,
        ...over,
    };
}

describe('is the keeper working', () => {
    test('a recent execution is the only positive evidence there is', () => {
        expect(liveness(1_000n, 1_100n, 3)).toBe('working');
    });

    test('an address that has never executed is unknown, not stopped', () => {
        // A fresh keeper has done nothing, and calling that "stopped" would
        // greet every new operator with a fault.
        expect(liveness(null, 500_000n, 5)).toBe('unknown');
    });

    test('silence with nothing due is quiet, never stopped', () => {
        // The failure this prevents: an idle registry making a healthy keeper
        // look dead. There was nothing to win, so winning nothing proves
        // nothing.
        const silent = SILENT_ROUNDS_STOPPED * 10n;
        expect(liveness(1_000n, 1_000n + silent, 0)).toBe('quiet');
    });

    test('silence while work is due is quiet first, then stopped', () => {
        // Losing a race is normal and must not read as a fault immediately.
        expect(liveness(1_000n, 1_000n + SILENT_ROUNDS_WARNING, 2)).toBe('quiet');
        expect(liveness(1_000n, 1_000n + SILENT_ROUNDS_STOPPED, 2)).toBe('stopped');
    });

    test('the reason says which of the two silences it is', () => {
        expect(livenessReason('quiet', 0)).toContain('not a fault');
        expect(livenessReason('quiet', 3)).toContain('losing races');
        expect(livenessReason('unknown', 0)).toContain('never executed');
    });
});

describe('what the registry is worth right now', () => {
    test('only what is due counts, because only that can be taken', () => {
        const total = claimableNow([
            entry({ netReward: 7_000n }),
            entry({ availability: 'scheduled', netReward: 50_000n }),
            entry({ availability: 'dormant', netReward: 90_000n }),
        ]);
        expect(total).toBe(7_000n);
    });

    test('nothing due is zero, not an error', () => {
        expect(claimableNow([entry({ availability: 'scheduled' })])).toBe(0n);
    });
});

describe('what to execute next', () => {
    test('the richest due upkeep', () => {
        const best = nextBest([entry({ netReward: 1_000n }), entry({ netReward: 9_000n })]);
        expect(best?.netReward).toBe(9_000n);
    });

    test('ties break on how overdue it is, because a late fee keeps climbing', () => {
        const best = nextBest([
            entry({ netReward: 9_000n, overdueRounds: 5n }),
            entry({ netReward: 9_000n, overdueRounds: 500n }),
        ]);
        expect(best?.overdueRounds).toBe(500n);
    });

    test('scheduled work is never suggested, however rich', () => {
        expect(nextBest([entry({ availability: 'scheduled', netReward: 10n ** 9n })])).toBeNull();
    });
});

describe('what does not pay', () => {
    test('a reward at or below zero is work that costs money to do', () => {
        // netReward is already net of the execution cost, so zero is not
        // break-even in any useful sense: it is an hour of machine time for
        // nothing.
        const found = unprofitable([
            entry({ netReward: 1n }),
            entry({ netReward: 0n }),
            entry({ netReward: -500n }),
        ]);
        expect(found).toHaveLength(2);
    });
});

describe('rounds as time', () => {
    test('reads as a duration a person can act on', () => {
        expect(humanRounds(10n, 2.7)).toBe('27s');
        expect(humanRounds(1_000n, 2.7)).toBe('45m');
        expect(humanRounds(10_000n, 2.7)).toBe('7.5h');
        expect(humanRounds(100_000n, 2.7)).toBe('3.1d');
    });

    test('takes the block time as an argument rather than assuming one', () => {
        // The repo carries three different block times, and a dashboard that
        // hardcoded one would quietly disagree with every other page.
        expect(humanRounds(1_000n, 2.7)).not.toBe(humanRounds(1_000n, 2.8));
    });
});
