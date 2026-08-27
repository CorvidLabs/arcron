/**
 * The fee the console suggests must not be the fee the contract merely allows.
 *
 * `MIN_UPKEEP_FEE` is a floor. The console pre-filled it for months, which meant
 * every upkeep registered through the UI paid a keeper 1,000 uALGO per run after
 * its 3,000 in group fees. At that margin one keeper needs roughly 77 concurrent
 * hourly upkeeps to cover a $5 host, while a creator is better off self-hosting
 * past about 26 — so the suggested price sat below the cost of supplying it, and
 * the escalating fee had nothing to escalate from.
 *
 * This pins the separation rather than the number, so raising the floor later
 * does not silently make the floor the suggestion again.
 */

import { describe, expect, test } from 'bun:test';

import { MIN_UPKEEP_FEE, SUGGESTED_UPKEEP_FEE, MAX_UPKEEP_FEE } from '@corvidlabs/arcron';

/** What a keeper pays in group fees to execute one upkeep. */
const KEEPER_GROUP_FEES = 3_000;

describe('what the console suggests paying', () => {
    test('the suggestion is not the floor', () => {
        expect(SUGGESTED_UPKEEP_FEE).toBeGreaterThan(MIN_UPKEEP_FEE);
    });

    test('the floor is still accepted, so nobody is priced out', () => {
        expect(MIN_UPKEEP_FEE).toBe(4_000);
        expect(SUGGESTED_UPKEEP_FEE).toBeLessThan(MAX_UPKEEP_FEE);
    });

    test('the suggestion leaves a keeper enough to fund a machine', () => {
        // The failure this pins: a margin so thin that a second keeper cannot
        // pay for itself, which is the one thing the escalating fee exists to
        // fix and cannot fix from below.
        const net = SUGGESTED_UPKEEP_FEE - KEEPER_GROUP_FEES;
        expect(net).toBeGreaterThanOrEqual(5_000);

        // At ~$0.09/ALGO and 720 hourly executions a month, break-even against
        // a $5 host should land near the size of a plausible early registry
        // rather than near a hundred upkeeps.
        const upkeepsToFundFiveDollars = (5.0 / 0.09) * 1e6 / (net * 720);
        expect(upkeepsToFundFiveDollars).toBeLessThan(20);
    });

    test('the minimum is the case the suggestion exists to avoid', () => {
        const netAtFloor = MIN_UPKEEP_FEE - KEEPER_GROUP_FEES;
        const upkeepsAtFloor = (5.0 / 0.09) * 1e6 / (netAtFloor * 720);
        expect(upkeepsAtFloor).toBeGreaterThan(70);
    });
});
