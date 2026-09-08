/**
 * What the App spendable and Escrowed tiles say under their numbers.
 *
 * The hint is where the console makes its one claim about the app's money,
 * and it used to make it over a partial read: "covers every escrow" was
 * printed whenever the balance covered the boxes that had been read. These
 * bind the copy to the read state the service now exposes.
 */

import { describe, expect, test } from 'bun:test';

import { escrowedHintFor, solvencyHintFor } from './stat-tiles';

const hint = (overrides: Partial<Parameters<typeof solvencyHintFor>[0]> = {}) =>
    solvencyHintFor({
        appId: 769_891_898,
        read: 'complete',
        solvent: true,
        unreadableBoxes: 0,
        listedBoxes: 36,
        ...overrides,
    });

describe('the solvency hint', () => {
    test('claims coverage only over a complete read', () => {
        expect(hint()).toBe('covers every escrow');
    });

    test('a short balance on a complete read is called short', () => {
        expect(hint({ solvent: false })).toBe('below total escrow');
    });

    test('an incomplete read says how incomplete, and does not claim coverage', () => {
        const text = hint({ read: 'incomplete', solvent: null, unreadableBoxes: 3 });
        expect(text).toBe('3 of 36 boxes unreadable; solvency unknown; this says nothing about the app');
        expect(text).not.toContain('covers');
    });

    test('every box unreadable is still "unknown", not "covers every escrow"', () => {
        // The sum over zero boxes is zero and any balance covers it. That is
        // the exact case the old hint got wrong.
        const text = hint({ read: 'incomplete', solvent: null, unreadableBoxes: 36 });
        expect(text).toContain('36 of 36 boxes unreadable');
        expect(text).toContain('unknown');
    });

    test('a torn read is unknown and says the balance moved', () => {
        const text = hint({ read: 'torn', solvent: null });
        expect(text).toContain('unknown');
        expect(text).toContain('moved');
        expect(text).not.toContain('below');
    });

    test('unknown is worded as a fact about the read, not a doubt about the app', () => {
        // "solvency unknown" next to a red tile reads as "probably insolvent".
        // The copy has to close that reading off itself.
        expect(hint({ read: 'incomplete', solvent: null, unreadableBoxes: 1 })).toContain(
            'says nothing about the app',
        );
    });

    test('no app selected and not yet read are told apart', () => {
        expect(hint({ appId: null, read: 'unread', solvent: null })).toBe('no app selected');
        expect(hint({ read: 'unread', solvent: null })).toBe('not read yet');
    });
});

describe('the escrowed hint', () => {
    test('a complete read counts what the total spans', () => {
        expect(escrowedHintFor({ upkeeps: 0, unreadableBoxes: 0 })).toBe('nothing registered yet');
        expect(escrowedHintFor({ upkeeps: 1, unreadableBoxes: 0 })).toBe('across 1 upkeep');
        expect(escrowedHintFor({ upkeeps: 12, unreadableBoxes: 0 })).toBe('across 12 upkeeps');
    });

    test('an incomplete read calls the total a lower bound', () => {
        expect(escrowedHintFor({ upkeeps: 10, unreadableBoxes: 2 })).toBe(
            'across 10 upkeeps read; 2 unreadable, so this is a lower bound',
        );
    });

    test('nothing read at all is not "nothing registered"', () => {
        // Zero readable boxes out of thirty-six is not an empty registry.
        const text = escrowedHintFor({ upkeeps: 0, unreadableBoxes: 36 });
        expect(text).not.toContain('nothing registered');
        expect(text).toContain('36 unreadable');
    });
});
