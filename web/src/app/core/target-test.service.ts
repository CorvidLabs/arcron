/**
 * Running the register form's Test button, and holding its answer.
 *
 * The simulate itself lives in `@corvidlabs/arcron/target-test`, framework
 * free and measured against a chain. This is the UI state around it: whether
 * one is in flight, what came back, and what it was an answer *about*.
 *
 * That last part is the whole reason the result carries its subject. A verdict
 * for a call the user has since edited, sitting next to a submit button, is
 * the exact shape of bug this console keeps finding: a confident sentence
 * about something that is no longer true. `resultFor` returns nothing rather
 * than something stale.
 */

import { inject, Injectable, signal } from '@angular/core';

import { ArcronService, describe } from './arcron.service';
import { testTarget, type TargetTestOutcome } from '@corvidlabs/arcron/target-test';
import { toHex } from '@corvidlabs/arcron/upkeep';

/** What a test was a test of. */
export interface TargetSubject {
  readonly keeperAppId: number;
  readonly targetApp: number;
  /** Every app arg, hex-joined, so two calls compare by value. */
  readonly call: string;
}

export interface TargetTestReport {
  readonly subject: TargetSubject;
  /** Null when the node could not be reached at all, which is not a verdict. */
  readonly outcome: TargetTestOutcome | null;
  readonly unreachable: string | null;
}

export function subjectOf(
  keeperAppId: number,
  targetApp: number,
  callArgs: readonly Uint8Array[],
): TargetSubject {
  return { keeperAppId, targetApp, call: callArgs.map((arg) => toHex(arg)).join('.') };
}

export function sameSubject(left: TargetSubject, right: TargetSubject): boolean {
  return (
    left.keeperAppId === right.keeperAppId &&
    left.targetApp === right.targetApp &&
    left.call === right.call
  );
}

@Injectable({ providedIn: 'root' })
export class TargetTestService {
  private readonly arcron = inject(ArcronService);

  readonly running = signal(false);
  private readonly report = signal<TargetTestReport | null>(null);

  /** The report, but only if it is still an answer about `subject`. */
  resultFor(subject: TargetSubject): TargetTestReport | null {
    const report = this.report();
    if (report === null) return null;
    return sameSubject(report.subject, subject) ? report : null;
  }

  async run(subject: TargetSubject, callArgs: readonly Uint8Array[]): Promise<void> {
    this.running.set(true);
    try {
      const outcome = await testTarget(this.arcron.algod(), {
        keeperAppId: subject.keeperAppId,
        targetApp: subject.targetApp,
        callArgs,
      });
      this.report.set({ subject, outcome, unreachable: null });
    } catch (cause) {
      // A node that will not answer is not a verdict about the target, and
      // must never be rendered as one.
      this.report.set({ subject, outcome: null, unreachable: describe(cause) });
    } finally {
      this.running.set(false);
    }
  }
}
