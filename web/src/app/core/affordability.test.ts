/**
 * The guard that stops the console opening a wallet for a registration the
 * connected account cannot pay for.
 *
 * Nothing in the console read the user's balance at all before this, so the
 * failure arrived from algod after the wallet had opened, rendered raw in the
 * activity panel. Two properties are worth pinning: an unread balance is not
 * an affordable one, and the figure compared against the total is what the
 * account can *spend*, not what it holds.
 *
 * Imports the real predicate. A copy of this arithmetic declared here would
 * stay green with the check deleted from the form.
 */

import { describe, expect, test } from 'bun:test';

import { affordability } from './affordability';

// The form's own defaults: 62,100 box MBR + 12,000 escrow + 3,000 in fees.
const TOTAL = 77_100n;

describe('affordability', () => {
    test('an unread balance is unknown, not enough and not short', () => {
        // The distinction the whole three-state shape exists for. Reported as
        // "short" it would name a shortfall nobody can act on; reported as
        // "enough" it would let a wallet open on no evidence at all.
        expect(affordability(TOTAL, null)).toEqual({ state: 'unknown' });
    });

    test('more than the total is enough, and says what is left', () => {
        const result = affordability(TOTAL, 1_000_000n);
        expect(result.state).toBe('enough');
        expect(result).toMatchObject({ left: 1_000_000n - TOTAL });
    });

    test('exactly the total is enough', () => {
        expect(affordability(TOTAL, TOTAL).state).toBe('enough');
    });

    test('one microALGO short is short, and names the difference', () => {
        const result = affordability(TOTAL, TOTAL - 1n);
        expect(result.state).toBe('short');
        expect(result).toMatchObject({ shortfall: 1n });
    });

    test('an empty account is short by the whole total', () => {
        expect(affordability(TOTAL, 0n)).toMatchObject({ state: 'short', shortfall: TOTAL });
    });

    test('it compares against what can be spent, so a held minimum balance still refuses', () => {
        // An account holding 0.15 ALGO against a 0.1 ALGO minimum can spend
        // 0.05, which does not cover this. Passing the raw balance here is the
        // mistake this exists to make impossible: the AVM enforces the minimum
        // balance and the console did not even read it.
        const held = 150_000n;
        const minimum = 100_000n;
        expect(affordability(TOTAL, held).state).toBe('enough');
        expect(affordability(TOTAL, held - minimum).state).toBe('short');
    });
});
