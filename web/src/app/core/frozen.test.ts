/**
 * The freeze flag decides whether the console warns someone that the app's
 * creator can still reach their escrow. Reading it wrongly does not throw, it
 * just silently stops warning, so the parse is worth pinning.
 */

import { describe, expect, test } from 'bun:test';

/** The parse used by ArcronService.refreshApp, isolated from algod. */
function isFrozen(entries: { key: Uint8Array; value: { uint?: number | bigint } }[]): boolean {
    const found = entries.find((entry) => new TextDecoder().decode(entry.key) === 'frozen');
    if (!found) return true;
    return BigInt(found.value.uint ?? 0) !== 0n;
}

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
