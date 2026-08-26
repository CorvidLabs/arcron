/**
 * A Test button result must never outlive the call it was a result about.
 *
 * The verdict is a sentence about a specific target and a specific set of app
 * args, sitting next to a button that commits money. Edit the signature, the
 * arguments or the target after testing and the panel would otherwise keep
 * showing a green "accepted" for a call that no longer exists. That is the
 * same class of bug as the console's confident "no upkeeps yet" before its
 * first read returned: a claim held past the point it was checked.
 *
 * Imports the real comparison the service runs.
 */

import { describe, expect, test } from 'bun:test';

import { sameSubject, subjectOf } from './target-test.service';

const KEEPER = 769_891_898;
const TARGET = 769_891_902;
const TICK = new Uint8Array([0x4d, 0x4d, 0x5f, 0x0b]);
const OTHER = new Uint8Array([0x01, 0x02, 0x03, 0x04]);
const ARG = new Uint8Array([0, 0, 0, 0, 0, 0, 0, 7]);

describe('what a test was a test of', () => {
    test('the same call twice is the same subject', () => {
        // Same values, different array instances: comparing by identity would
        // discard a perfectly good result on every keystroke elsewhere.
        expect(
            sameSubject(
                subjectOf(KEEPER, TARGET, [new Uint8Array(TICK)]),
                subjectOf(KEEPER, TARGET, [new Uint8Array(TICK)]),
            ),
        ).toBe(true);
    });

    test('a different target app is a different subject', () => {
        expect(
            sameSubject(subjectOf(KEEPER, TARGET, [TICK]), subjectOf(KEEPER, TARGET + 1, [TICK])),
        ).toBe(false);
    });

    test('a different method selector is a different subject', () => {
        expect(
            sameSubject(subjectOf(KEEPER, TARGET, [TICK]), subjectOf(KEEPER, TARGET, [OTHER])),
        ).toBe(false);
    });

    test('adding an argument is a different subject', () => {
        // The arguments are fixed at registration and are part of what the
        // target was asked to accept, so a verdict does not carry across them.
        expect(
            sameSubject(subjectOf(KEEPER, TARGET, [TICK]), subjectOf(KEEPER, TARGET, [TICK, ARG])),
        ).toBe(false);
    });

    test('changing an argument value is a different subject', () => {
        const other = new Uint8Array(ARG);
        other[7] = 8;
        expect(
            sameSubject(
                subjectOf(KEEPER, TARGET, [TICK, ARG]),
                subjectOf(KEEPER, TARGET, [TICK, other]),
            ),
        ).toBe(false);
    });

    test('a different keeper app is a different subject', () => {
        // The sender of the simulated call is the keeper app's own account, so
        // a verdict from one deployment says nothing about another. Switching
        // to a look-alike app must not inherit the canonical one's green tick.
        expect(
            sameSubject(subjectOf(KEEPER, TARGET, [TICK]), subjectOf(999_999_999, TARGET, [TICK])),
        ).toBe(false);
    });

    test('arguments cannot be run together into the same subject', () => {
        // A naive join would make [0xAA, 0xBB] and [0xAABB] compare equal.
        expect(
            sameSubject(
                subjectOf(KEEPER, TARGET, [new Uint8Array([0xaa]), new Uint8Array([0xbb])]),
                subjectOf(KEEPER, TARGET, [new Uint8Array([0xaa, 0xbb])]),
            ),
        ).toBe(false);
    });
});
