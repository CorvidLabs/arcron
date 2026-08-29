/**
 * What gets audited: which widths, which themes, which pages.
 *
 * Its own module because two things need it. `console.pw.ts` walks it to
 * declare the tests, and `report.ts` needs its size to know whether a run
 * covered everything: the stale-baseline check is only meaningful over a full
 * run, and firing it after `--grep` would make filtering unusable.
 */

import { expect, type Page } from '@playwright/test';

import { CANONICAL_APP_ID, FOREIGN_APP_ID } from './chain';

export interface Viewport {
  readonly name: string;
  readonly width: number;
  readonly height: number;
}

/**
 * A phone, a tablet, a laptop and a large desktop. The two ends are the ones
 * that matter: 390 is where "isn't mobile responsive" is decided, and 1920 is
 * where "doesn't use the full screen" is.
 */
export const VIEWPORTS: readonly Viewport[] = [
  { name: 'phone-390', width: 390, height: 844 },
  { name: 'tablet-768', width: 768, height: 1024 },
  { name: 'laptop-1280', width: 1280, height: 800 },
  { name: 'desktop-1920', width: 1920, height: 1080 },
];

/**
 * Both, always. The bug that started this suite was theme-dependent in one
 * direction and, once measured, turned out to be broken in the other too.
 * `?theme=` is the design system's own override, so this drives the console
 * exactly the way a QA screenshot link does.
 */
export const THEMES = ['light', 'dark'] as const;

export interface Scenario {
  readonly name: string;
  readonly path: string;
  /** Anything that has to happen after the first read lands. */
  readonly settle?: (page: Page) => Promise<void>;
}

/**
 * Every destination, plus the two states that only exist inside one: the
 * Keeper board tab, and the quarantine a link naming another app puts the
 * whole page into.
 */
export const SCENARIOS: readonly Scenario[] = [
  { name: 'registry', path: `/?app=${CANONICAL_APP_ID}` },
  {
    name: 'keeper-board',
    path: `/?app=${CANONICAL_APP_ID}`,
    settle: async (page) => {
      await page.getByRole('button', { name: 'Keeper board' }).click();
      await expect(page.getByRole('heading', { name: 'Work available' })).toBeVisible();
    },
  },
  { name: 'upkeep', path: `/u/7?app=${CANONICAL_APP_ID}` },
  { name: 'register', path: `/register?app=${CANONICAL_APP_ID}` },
  { name: 'rain', path: `/rain?app=${CANONICAL_APP_ID}` },
  { name: 'quarantined', path: `/?app=${FOREIGN_APP_ID}` },
];

export const MATRIX_SIZE = VIEWPORTS.length * THEMES.length * SCENARIOS.length;
