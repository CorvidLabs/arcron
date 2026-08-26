/**
 * The arithmetic the browser suite trusts.
 *
 * `e2e/console.pw.ts` reads colours out of a live Chromium and hands them
 * straight to these functions, so a bug here is a suite that reports 21:1 for
 * an invisible button. The anchor case at the bottom is the exact pair that
 * shipped: the dark theme's hardcoded `#10201e` label on the `--surface` a
 * disabled primary button sits on.
 */

import { describe, expect, test } from 'bun:test';

import {
  AA_NORMAL_TEXT,
  contrastRatio,
  DISABLED_TEXT,
  over,
  parseColor,
  relativeLuminance,
  requiredRatio,
} from './contrast';

const WHITE = parseColor('rgb(255, 255, 255)');
const BLACK = parseColor('rgb(0, 0, 0)');

describe('parseColor', () => {
  test('reads the rgb() and rgba() Chromium returns for plain colours', () => {
    expect(parseColor('rgb(21, 24, 27)')).toEqual({ red: 21, green: 24, blue: 27, alpha: 1 });
    expect(parseColor('rgba(21, 24, 27, 0.12)')).toEqual({
      red: 21,
      green: 24,
      blue: 27,
      alpha: 0.12,
    });
  });

  test('reads the space-separated form', () => {
    expect(parseColor('rgb(69 208 188 / 0.5)')).toEqual({
      red: 69,
      green: 208,
      blue: 188,
      alpha: 0.5,
    });
  });

  test('reads the color(srgb ...) that color-mix() computes to', () => {
    // `tr.due` in registry-table.ts is `color-mix(in srgb, var(--sheen) 8%, transparent)`.
    const mixed = parseColor('color(srgb 0.0549 0.4353 0.4 / 0.08)');
    expect(Math.round(mixed.red)).toBe(14);
    expect(Math.round(mixed.green)).toBe(111);
    expect(Math.round(mixed.blue)).toBe(102);
    expect(mixed.alpha).toBeCloseTo(0.08, 5);
  });

  test('treats both spellings of nothing as nothing', () => {
    expect(parseColor('transparent').alpha).toBe(0);
    expect(parseColor('rgba(0, 0, 0, 0)').alpha).toBe(0);
  });

  test('throws rather than guessing', () => {
    // A colour read as black is how an invisible control gets reported as 21:1.
    expect(() => parseColor('lab(50% 40 59.5)')).toThrow();
    expect(() => parseColor('')).toThrow();
  });
});

describe('relativeLuminance', () => {
  test('anchors at the two ends of the scale', () => {
    expect(relativeLuminance(WHITE)).toBeCloseTo(1, 5);
    expect(relativeLuminance(BLACK)).toBeCloseTo(0, 5);
  });
});

describe('over', () => {
  test('an opaque colour hides what is under it', () => {
    expect(over(BLACK, WHITE)).toEqual(BLACK);
  });

  test('a fully transparent colour is what is under it', () => {
    expect(over(parseColor('transparent'), WHITE)).toEqual(WHITE);
  });

  test('half black over white is the midpoint', () => {
    const blended = over(parseColor('rgba(0, 0, 0, 0.5)'), WHITE);
    expect(blended.red).toBeCloseTo(127.5, 4);
    expect(blended.alpha).toBe(1);
  });
});

describe('contrastRatio', () => {
  test('black on white is the maximum', () => {
    expect(contrastRatio(BLACK, WHITE)).toBeCloseTo(21, 4);
  });

  test('a colour on itself is the minimum', () => {
    expect(contrastRatio(WHITE, WHITE)).toBeCloseTo(1, 5);
  });

  test('is symmetric in which colour is lighter', () => {
    expect(contrastRatio(WHITE, BLACK)).toBeCloseTo(contrastRatio(BLACK, WHITE), 5);
  });

  test('the brand accent clears AA on paper', () => {
    // --sheen #0E6F66 on --paper #FAF9F6. brand/tokens.css annotates this pair
    // as 5.9:1 and it measures 5.72:1, which still clears AA comfortably. The
    // token file is vendored from the design system and is not ours to edit,
    // so the number is pinned here rather than corrected there.
    const ratio = contrastRatio(parseColor('rgb(14, 111, 102)'), parseColor('rgb(250, 249, 246)'));
    expect(ratio).toBeGreaterThan(AA_NORMAL_TEXT);
    expect(ratio).toBeCloseTo(5.72, 1);
  });

  test('the disabled primary button that shipped was invisible', () => {
    // The regression this whole suite exists for: styles.css painted
    // `button.primary` #10201e in the dark theme at a higher specificity than
    // `button.primary:disabled`, so the label drew on --surface #1B1F23.
    const ratio = contrastRatio(parseColor('rgb(16, 32, 30)'), parseColor('rgb(27, 31, 35)'));
    expect(ratio).toBeLessThan(1.1);
    expect(ratio).toBeLessThan(DISABLED_TEXT);
  });
});

describe('requiredRatio', () => {
  test('body copy has to clear AA', () => {
    expect(requiredRatio({ fontSizePx: 15, fontWeight: 400, disabled: false })).toBe(4.5);
  });

  test('large text and large bold text clear the lower bar', () => {
    expect(requiredRatio({ fontSizePx: 24, fontWeight: 400, disabled: false })).toBe(3);
    expect(requiredRatio({ fontSizePx: 19, fontWeight: 700, disabled: false })).toBe(3);
    expect(requiredRatio({ fontSizePx: 19, fontWeight: 400, disabled: false })).toBe(4.5);
  });

  test('a disabled control is still held to a bar', () => {
    // WCAG exempts inactive components outright, which is the licence the
    // 1.02:1 button shipped under. A switched-off money button still has to
    // say what it is.
    expect(requiredRatio({ fontSizePx: 13, fontWeight: 500, disabled: true })).toBe(3);
  });
});
