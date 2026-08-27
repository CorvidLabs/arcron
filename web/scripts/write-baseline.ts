/**
 * Rebuild `e2e/baseline.json` from the last run's findings.
 *
 *   bunx playwright test          # produces e2e/.findings/*.json
 *   bun run scripts/write-baseline.ts
 *
 * Only for the deliberate act of accepting what a run found. It is not wired
 * into any lane and must not be: a baseline that regenerates itself is not a
 * baseline, it is a rubber stamp. Every entry it writes still has to be read,
 * and anything a human is not willing to defend belongs fixed instead.
 */

import { readdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

interface Finding {
  rule: string;
  key: string;
  detail: string;
  measured: number;
  lowerIsWorse: boolean;
}

const FINDINGS = join(import.meta.dir, '..', 'e2e', '.findings');
const OUT = join(import.meta.dir, '..', 'e2e', 'baseline.json');

const NOTE =
  'Rendering problems that exist today and are being left alone on purpose. ' +
  'Each entry pins the measurement, so the same problem getting worse fails the ' +
  'run, a new one fails the run, and one that has been fixed fails the run too ' +
  '(a licence nothing uses any more is a licence to regress). Regenerate with ' +
  'scripts/write-baseline.ts only after reading what changed.';

const RULES: Record<string, string> = {
  'text-size':
    'The console renders 46 distinct text styles below 14px, from 10.56px chips ' +
    'up to 13.6px body copy, on a 15px root. That is not one style to nudge; it ' +
    'is the whole type scale, and moving it changes how every page reads. The ' +
    'measurements are here so the decision can be made with numbers, and so ' +
    'nothing gets smaller in the meantime.',
  'touch-target':
    'Every control is sized for a mouse: buttons land at 29.8px or 38.2px tall ' +
    'at 390 wide, against the 44x44 WCAG 2.5.5 target. Fixing it means a ' +
    'deliberate pass over control sizing on touch, not a min-height sprinkled ' +
    'over the button rules, so it is recorded rather than patched.',
};

const SPECIFIC: Record<string, string> = {
  'touch-target:a@390':
    'the upkeep id link in the registry table: an 8.45x16px tap target on the ' +
    'row identity, and the only way into an upkeep from the registry. The worst ' +
    'target in the console by a wide margin and the first one to fix.',
  'touch-target:input@390':
    'the native radio and checkbox in the register form, at the 13x13px the user ' +
    'agent draws them. Enlarging these means restyling the controls, not ' +
    'resizing them.',
  'text-size:span.away':
    'the decorative arrow on an outbound link. It is aria-hidden and carries no ' +
    'information, so its size is the least urgent thing on this list.',
};

const findings = new Map<string, Finding>();
for (const name of readdirSync(FINDINGS)) {
  if (!name.endsWith('.json')) continue;
  const dump = JSON.parse(readFileSync(join(FINDINGS, name), 'utf8')) as { findings: Finding[] };
  for (const finding of dump.findings) {
    const existing = findings.get(finding.key);
    if (existing === undefined) {
      findings.set(finding.key, finding);
      continue;
    }
    const isWorse = finding.lowerIsWorse
      ? finding.measured < existing.measured
      : finding.measured > existing.measured;
    if (isWorse) findings.set(finding.key, finding);
  }
}

const accepted: Record<string, { worst: number; why: string }> = {};
for (const key of [...findings.keys()].sort()) {
  const finding = findings.get(key) as Finding;
  accepted[key] = {
    worst: finding.measured,
    why: SPECIFIC[key] ?? `${finding.detail}. See rules.${finding.rule}.`,
  };
}

writeFileSync(OUT, `${JSON.stringify({ note: NOTE, rules: RULES, accepted }, null, 2)}\n`);
console.log(`wrote ${Object.keys(accepted).length} accepted findings to ${OUT}`);
