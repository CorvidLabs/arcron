/**
 * The console, as a rendered page, at four widths and in both themes.
 *
 * Nothing had ever looked at this page's pixels or its computed style. Four
 * agent reviews, an axe-core pass with zero violations and 91 unit tests all
 * missed a disabled Register button rendering at 1.02:1, because none of them
 * ask the browser what colour anything ended up. This does.
 *
 * Named `.pw.ts` rather than `.spec.ts` so `bun test` - which owns
 * `*.test.ts` and `*.spec.ts` in this package - does not try to run a
 * Playwright suite in its own runner.
 */

import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

import { expect, test, type Page } from '@playwright/test';

import { findingsFor, rank, widthReportFor, type Finding } from './audit';
import { describeFinding, partition } from './baseline';
import { collect } from './collect';
import { stubAlgod, stubWebFonts } from './chain';
import { SCENARIOS, THEMES, VIEWPORTS } from './matrix';

const HERE = __dirname;
const SCREENSHOTS = join(HERE, '__screenshots__');
const FINDINGS = join(HERE, '.findings');

/**
 * Wait for the console to be showing the chain rather than reaching for it.
 *
 * Keyed on the status line the network bar renders, which is the one thing on
 * the page that reports the state of the read itself. Auditing before this is
 * auditing a page of skeleton copy.
 */
async function settled(page: Page): Promise<void> {
  await expect(page.locator('.status .mono')).not.toHaveText(/connecting/, { timeout: 15_000 });
  await expect(page.locator('.status .mono')).toContainText('testnet-v1.0');
}

test.describe('rendered console', () => {
  for (const viewport of VIEWPORTS) {
    for (const theme of THEMES) {
      for (const scenario of SCENARIOS) {
        const scope = `${scenario.name} @ ${viewport.width}x${viewport.height} ${theme}`;
        const slug = `${scenario.name}--${viewport.name}--${theme}`;

        test(scope, async ({ page }, testInfo) => {
          await stubWebFonts(page);
          await stubAlgod(page);
          await page.setViewportSize({ width: viewport.width, height: viewport.height });

          const separator = scenario.path.includes('?') ? '&' : '?';
          await page.goto(`${scenario.path}${separator}theme=${theme}`);
          await settled(page);
          await scenario.settle?.(page);

          // The poll repaints every 2.5 s against a chain that never moves, so
          // the DOM is stable; this only lets the first repaint land.
          await page.waitForTimeout(250);

          const collected = await page.evaluate(collect);
          const findings = findingsFor(collected, scope);
          const widths = widthReportFor(collected, scope);

          mkdirSync(SCREENSHOTS, { recursive: true });
          mkdirSync(FINDINGS, { recursive: true });
          const screenshot = join(SCREENSHOTS, `${slug}.png`);
          await page.screenshot({ path: screenshot, fullPage: true, animations: 'disabled' });
          writeFileSync(
            join(FINDINGS, `${slug}.json`),
            `${JSON.stringify({ scope, slug, findings, widths }, null, 2)}\n`,
          );

          // Attached whichever way the test goes: a passing run's screenshots
          // are the only way anybody sees what the page looks like at 390.
          await testInfo.attach(`${slug}.png`, { path: screenshot, contentType: 'image/png' });
          await testInfo.attach(`${slug}-findings.json`, {
            body: JSON.stringify({ findings: rank(findings), widths }, null, 2),
            contentType: 'application/json',
          });

          const { unexpected, regressed } = partition(findings);

          expect(unexpected, unexpectedMessage(unexpected, scope)).toEqual([]);
          expect(
            regressed.map((entry) => entry.finding),
            regressedMessage(regressed),
          ).toEqual([]);
        });
      }
    }
  }
});

function unexpectedMessage(findings: readonly Finding[], scope: string): string {
  if (findings.length === 0) return '';
  return (
    `${findings.length} rendering problem(s) at ${scope} that the baseline does not cover.\n` +
    `Fix them, or record them in e2e/baseline.json with the reason they stand:\n\n` +
    rank(findings).map(describeFinding).join('\n\n')
  );
}

function regressedMessage(
  regressed: readonly { finding: Finding; accepted: { worst: number; why: string } }[],
): string {
  if (regressed.length === 0) return '';
  return (
    `${regressed.length} known problem(s) got worse than e2e/baseline.json records:\n\n` +
    regressed
      .map(
        (entry) =>
          `${describeFinding(entry.finding)}\n    recorded at ${entry.accepted.worst}, now ${entry.finding.measured}`,
      )
      .join('\n\n')
  );
}
