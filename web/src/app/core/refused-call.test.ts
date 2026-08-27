/**
 * A failing test verdict has to stop a registration.
 *
 * Upkeep 77 on TestNet escrowed 8 ALGO against selector `0x62d7cc2c`, which
 * its target does not have. Every execution failed on `err` in the target's
 * own ABI router, and every future one would have: the selector is written
 * into the box at registration and there is no way to correct it. The only
 * exit is cancelling.
 *
 * The console's Test button catches this exactly. Nothing made it matter:
 * `canSubmit` consulted the form, the balance, the read path and the
 * attestation checkbox, and never the one control on the page built to answer
 * "will this call work". So a reader could press Test, be told the target had
 * no such method, and register regardless.
 *
 * `refused` is the predicate that closes it, and this is what pins its edges.
 * It has to block a verdict of no, and it has to stay out of the way of the
 * three things that are not that: no test at all, an unreachable node, and a
 * verdict about a call that has since been edited.
 */

import { describe, expect, test } from 'bun:test';

import { subjectOf, type TargetTestReport } from './target-test.service';
import type { TargetTestOutcome } from '@corvidlabs/arcron/target-test';

const CALL = [new Uint8Array([0x62, 0xd7, 0xcc, 0x2c])];
const OTHER = [new Uint8Array([0x50, 0x6e, 0x5d, 0xd0])];

function outcome(accepted: boolean, failure: string | null = null): TargetTestOutcome {
    return { accepted, failure, failureKind: null, grade: null, counts: null };
}

/** The predicate under test, kept identical to the component's `refused`. */
function refusedFrom(report: TargetTestReport | null): TargetTestOutcome | null {
    const found = report?.outcome ?? null;
    return found !== null && !found.accepted ? found : null;
}

describe('a verdict of no about this exact call', () => {
    test('blocks, which is upkeep 77', () => {
        const report: TargetTestReport = {
            subject: subjectOf(769891898, 770029154, CALL),
            outcome: outcome(false, 'err opcode executed'),
            unreachable: null,
        };
        expect(refusedFrom(report)).not.toBeNull();
    });

    test('and carries the target words, so the reason is not guessed at', () => {
        const report: TargetTestReport = {
            subject: subjectOf(769891898, 770029154, CALL),
            outcome: outcome(false, 'err opcode executed'),
            unreachable: null,
        };
        expect(refusedFrom(report)?.failure).toBe('err opcode executed');
    });
});

describe('what must not block', () => {
    test('a pass', () => {
        const report: TargetTestReport = {
            subject: subjectOf(769891898, 770029154, OTHER),
            outcome: outcome(true),
            unreachable: null,
        };
        expect(refusedFrom(report)).toBeNull();
    });

    test('no test at all, because the attestation is a judgement not a check', () => {
        // docs/first-upkeep.md is explicit that a Test pass does not satisfy
        // the attestation. The converse has to hold too: never testing is a
        // choice the console allows.
        expect(refusedFrom(null)).toBeNull();
    });

    test('an unreachable node, which is not a verdict about the call', () => {
        const report: TargetTestReport = {
            subject: subjectOf(769891898, 770029154, CALL),
            outcome: null,
            unreachable: 'the node could not be reached',
        };
        expect(refusedFrom(report)).toBeNull();
    });
});

describe('editing the call clears the block', () => {
    test('a subject built from different args is a different subject', () => {
        // This is what `resultFor` compares, and it is why the block lifts as
        // soon as the signature field changes: the report stops being an
        // answer about what the form would now send.
        const before = subjectOf(769891898, 770029154, CALL);
        const after = subjectOf(769891898, 770029154, OTHER);
        expect(before.call).not.toBe(after.call);
    });

    test('and so is the same call against a different target', () => {
        expect(subjectOf(769891898, 770029154, CALL).targetApp).not.toBe(
            subjectOf(769891898, 769891902, CALL).targetApp,
        );
    });
});
