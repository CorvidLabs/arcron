/**
 * Global setup and teardown: start clean, and end with one report.
 *
 * Setup empties the per-test finding dumps, because a stale file from a run
 * where a test was filtered out would keep a fixed problem alive in the
 * summary.
 *
 * Teardown does the two things no individual test can. It gathers every
 * finding from every viewport and theme into one ranked file a human can read,
 * and it checks the baseline for entries nothing produced any more. That
 * second one is what stops `baseline.json` becoming a graveyard: a problem
 * that has been fixed loses its licence to come back.
 */

import { existsSync, mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';

import { rank, type Finding, type WidthReport } from './audit';
import { loadBaseline } from './baseline';
import { MATRIX_SIZE } from './matrix';

const HERE = __dirname;
const FINDINGS = join(HERE, '.findings');
const REPORT = join(HERE, '__screenshots__', 'findings.md');

interface Dump {
  readonly scope: string;
  readonly slug: string;
  readonly findings: Finding[];
  readonly widths: WidthReport;
}

export async function globalSetup(): Promise<void> {
  rmSync(FINDINGS, { recursive: true, force: true });
  mkdirSync(FINDINGS, { recursive: true });
}

export async function globalTeardown(): Promise<void> {
  if (!existsSync(FINDINGS)) return;
  const dumps = readdirSync(FINDINGS)
    .filter((name) => name.endsWith('.json'))
    .map((name) => JSON.parse(readFileSync(join(FINDINGS, name), 'utf8')) as Dump);
  if (dumps.length === 0) return;

  writeReport(dumps);
  assertBaselineIsNotStale(dumps);
}

/** One finding per key, with every place it was seen. */
function consolidate(dumps: readonly Dump[]): Map<string, { finding: Finding; seen: string[] }> {
  const byKey = new Map<string, { finding: Finding; seen: string[] }>();
  for (const dump of dumps) {
    for (const finding of dump.findings) {
      const existing = byKey.get(finding.key);
      if (existing === undefined) {
        byKey.set(finding.key, { finding, seen: [dump.scope] });
        continue;
      }
      existing.seen.push(dump.scope);
      const isWorse = finding.lowerIsWorse
        ? finding.measured < existing.finding.measured
        : finding.measured > existing.finding.measured;
      if (isWorse) existing.finding = finding;
    }
  }
  return byKey;
}

function writeReport(dumps: readonly Dump[]): void {
  const byKey = consolidate(dumps);
  const baseline = loadBaseline();
  const ordered = rank([...byKey.values()].map((entry) => entry.finding));

  const lines: string[] = [
    '# Rendering findings',
    '',
    `Produced by \`fledge run web-render\` over ${dumps.length} page states.`,
    'Every number is measured from computed style or a bounding box in Chromium,',
    'not from a screenshot comparison.',
    '',
    `Screenshots for each state sit beside this file as \`<scenario>--<viewport>--<theme>.png\`.`,
    '',
    '## Findings, worst first',
    '',
    '| rule | key | measured | bar | in baseline | seen |',
    '| --- | --- | --- | --- | --- | --- |',
  ];
  for (const finding of ordered) {
    const entry = byKey.get(finding.key);
    const known = baseline.accepted[finding.key] === undefined ? 'no' : 'yes';
    lines.push(
      `| ${finding.rule} | \`${finding.key}\` | ${finding.measured} | ${finding.bar} | ${known} | ${entry?.seen.length ?? 0} states |`,
    );
  }

  lines.push('', '## What each one says', '');
  for (const finding of ordered) {
    lines.push(
      `- **${finding.key}**: ${finding.detail}. Seen at ${byKey.get(finding.key)?.seen[0]}.`,
    );
  }

  lines.push('', '## Layout width, reported not asserted', '');
  lines.push('| state | viewport | content column | fraction | empty each side | longest line |');
  lines.push('| --- | --- | --- | --- | --- | --- |');
  for (const dump of [...dumps].sort((left, right) => left.slug.localeCompare(right.slug))) {
    const widths = dump.widths;
    lines.push(
      `| ${dump.slug} | ${widths.viewportWidthPx}px | ${widths.contentWidthPx}px | ` +
        `${Math.round(widths.contentWidthFraction * 100)}% | ${widths.emptySidePx}px | ` +
        `${widths.longestLineChars} chars |`,
    );
  }
  lines.push('');

  mkdirSync(dirname(REPORT), { recursive: true });
  writeFileSync(REPORT, `${lines.join('\n')}\n`);
  console.log(`\nRendering findings: ${REPORT}`);
}

/**
 * A baseline entry nothing produces any more has been fixed, and leaving it
 * in place would silently re-licence the problem the next time it appeared.
 *
 * Only over a complete run. A `--grep` covering four page states cannot say
 * anything about a finding that only happens on the other thirty-six, and a
 * check that fails on every filtered run is a check people work around.
 */
function assertBaselineIsNotStale(dumps: readonly Dump[]): void {
  if (dumps.length < MATRIX_SIZE) {
    console.log(
      `Baseline staleness not checked: ${dumps.length} of ${MATRIX_SIZE} page states ran.`,
    );
    return;
  }
  const seen = new Set<string>();
  for (const dump of dumps) {
    for (const finding of dump.findings) seen.add(finding.key);
  }
  const baseline = loadBaseline();
  const stale = Object.keys(baseline.accepted).filter((key) => !seen.has(key));
  if (stale.length === 0) return;
  throw new Error(
    `e2e/baseline.json licenses ${stale.length} problem(s) that no longer happen. ` +
      `Delete these entries so they cannot come back unnoticed:\n  ${stale.join('\n  ')}`,
  );
}
