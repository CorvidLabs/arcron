/**
 * The freeze flag decides whether the console warns someone that the app's
 * creator can still reach their escrow. Reading it wrongly does not throw, it
 * just silently stops warning, so the parse is worth pinning.
 */

import { describe, expect, test } from 'bun:test';

// The real function the console runs, not a copy. An earlier version of this
// file declared its own, so reverting the coercion in the service left every
// test here green.
import { canCommitMoney as canWrite, isFrozen } from './arcron.service';

const key = (name: string) => new TextEncoder().encode(name);

describe('reading the freeze flag', () => {
    test('a number 0 is not frozen, despite 0 !== 0n being true', () => {
        expect(isFrozen([{ key: key('frozen'), value: { uint: 0 } }])).toBe(false);
    });

    test('a bigint 0n is not frozen', () => {
        expect(isFrozen([{ key: key('frozen'), value: { uint: 0n } }])).toBe(false);
    });

    test('a non-zero value is frozen, in either representation', () => {
        expect(isFrozen([{ key: key('frozen'), value: { uint: 1 } }])).toBe(true);
        expect(isFrozen([{ key: key('frozen'), value: { uint: 1n } }])).toBe(true);
    });

    test('an app with no flag at all predates governance, so it is immutable', () => {
        expect(isFrozen([{ key: key('next_upkeep_id'), value: { uint: 23 } }])).toBe(true);
    });
});

// --- what makes it safe to commit money ---------------------------------
//
// Every write guard used to key on `status === 'ready'` alone, and a read
// that completes without throwing sets that. A node answering for the wrong
// chain answers perfectly well, so the page showed "wrong chain" in red and
// left every money button live underneath it. Two reviewers found that in the
// same pass.


describe('when it is safe to commit money', () => {
    const ok = { status: 'ready', genesisMatches: true, appId: 769_891_898, quarantined: false };

    test('a healthy read on the right chain with an app selected', () => {
        expect(canWrite(ok)).toBe(true);
    });

    test('a node answering for another chain blocks writes, however healthy the read', () => {
        expect(canWrite({ ...ok, genesisMatches: false })).toBe(false);
    });

    test('no app selected blocks writes, which is the front door default', () => {
        expect(canWrite({ ...ok, appId: null })).toBe(false);
    });

    test('a failed read blocks writes', () => {
        expect(canWrite({ ...ok, status: 'error' })).toBe(false);
    });

    test('an unknown genesis does not block, because it is not yet a mismatch', () => {
        // null means the first read has not returned. Blocking on that would
        // make every page load briefly unusable rather than briefly unknown.
        expect(canWrite({ ...ok, genesisMatches: null })).toBe(true);
    });

    test('a quarantined app blocks writes, however healthy everything else is', () => {
        // A link can name any app id, and a look-alike keeper answers every
        // read perfectly well. See `quarantine.test.ts` for the whole rule.
        expect(canWrite({ ...ok, quarantined: true })).toBe(false);
    });
});
