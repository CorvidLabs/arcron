/**
 * What is already wrong, written down on purpose.
 *
 * Dropping a rendering audit onto an existing page leaves two bad options:
 * set the bars where the page happens to sit, which catches nothing, or set
 * them where they belong and hand somebody a permanently red lane, which
 * catches nothing either because it gets skipped. So the bars are set where
 * they belong and every deviation that exists today is listed in
 * `baseline.json` with its measurement and the reason it was left.
 *
 * The suite then fails on three things:
 *
 *  1. a finding whose key is not in the baseline - something new broke, or a
 *     page nobody had audited turned out to be worse;
 *  2. a baseline finding that got measurably worse than recorded;
 *  3. a baseline entry nothing produces any more - it was fixed, and leaving
 *     the licence in place would let it come back silently.
 *
 * That last one is the difference between a baseline and an excuse.
 *
 * This is not screenshot diffing. Keys name rules, not pixels: rewording a
 * button, adding a registry row or changing a colour that still clears its bar
 * moves nothing here.
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import type { Finding } from './audit';

export interface AcceptedFinding {
  /** The measurement recorded when this was accepted. */
  readonly worst: number;
  /** Why it is still here, in a sentence a reviewer can disagree with. */
  readonly why: string;
}

interface BaselineFile {
  readonly note: string;
  readonly accepted: Readonly<Record<string, AcceptedFinding>>;
}

const BASELINE_PATH = join(__dirname, 'baseline.json');

export function loadBaseline(): BaselineFile {
  return JSON.parse(readFileSync(BASELINE_PATH, 'utf8')) as BaselineFile;
}

/**
 * Slack, so that a font falling back or a sub-pixel rounding difference on
 * another machine is not a failure. Anything that moves by more than this is
 * a real change in the page.
 */
const RELATIVE_SLACK = 0.03;
const ABSOLUTE_SLACK = 1;

export function isWorseThanAccepted(finding: Finding, accepted: AcceptedFinding): boolean {
  if (finding.lowerIsWorse) {
    return finding.measured < accepted.worst * (1 - RELATIVE_SLACK) - 0.01;
  }
  return finding.measured > accepted.worst * (1 + RELATIVE_SLACK) + ABSOLUTE_SLACK;
}

export interface Partitioned {
  /** Findings with no entry in the baseline. These fail the run. */
  readonly unexpected: readonly Finding[];
  /** Findings that got worse than the baseline records. These fail the run. */
  readonly regressed: readonly { readonly finding: Finding; readonly accepted: AcceptedFinding }[];
  /** Findings matching the baseline. Reported, not failed. */
  readonly known: readonly Finding[];
}

export function partition(
  findings: readonly Finding[],
  baseline: BaselineFile = loadBaseline(),
): Partitioned {
  const unexpected: Finding[] = [];
  const regressed: { finding: Finding; accepted: AcceptedFinding }[] = [];
  const known: Finding[] = [];
  for (const finding of findings) {
    const accepted = baseline.accepted[finding.key];
    if (accepted === undefined) {
      unexpected.push(finding);
    } else if (isWorseThanAccepted(finding, accepted)) {
      regressed.push({ finding, accepted });
    } else {
      known.push(finding);
    }
  }
  return { unexpected, regressed, known };
}

export function describeFinding(finding: Finding): string {
  return `[${finding.rule}] ${finding.key}\n    ${finding.detail}\n    at ${finding.where}`;
}
