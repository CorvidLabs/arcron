/**
 * Measurements in, findings out.
 *
 * Every rule here is a property of the rendered page rather than a picture of
 * it. That is the whole design: a suite that diffs screenshots fails on every
 * legitimate change, and the only thing it teaches is to press "accept". A
 * suite that says "`button.primary` renders its disabled label at 1.02:1
 * against `rgb(27, 31, 35)`, and the bar is 3" says what is wrong, where, and
 * by how much, and stays quiet when a heading is reworded.
 *
 * Screenshots are still taken at every viewport, but as evidence attached to
 * the finding, not as the assertion.
 */

import {
  contrastRatio,
  over,
  parseColor,
  requiredRatio,
  type Rgba,
} from '../src/app/core/contrast';
import type { Collected, CollectedControl } from './collect';

export type Rule =
  'overflow' | 'contrast' | 'text-size' | 'touch-target' | 'clip' | 'overlap' | 'table-cell';

export interface Finding {
  readonly rule: Rule;
  /**
   * A stable name for the *rule that produced this*, not for the element
   * instance. Twelve Execute buttons are one finding, and rewording a label or
   * adding a row does not invent a new one.
   */
  readonly key: string;
  /** One sentence, with the number in it. */
  readonly detail: string;
  /** The measurement. */
  readonly measured: number;
  /** What it had to clear. */
  readonly bar: number;
  /** True when a smaller number is worse (contrast, text size, target size). */
  readonly lowerIsWorse: boolean;
  /** Where a human should look. */
  readonly where: string;
}

/** WCAG 2.5.5 Target Size (Enhanced), which is the bar phones are designed to. */
export const TOUCH_TARGET_PX = 44;
/**
 * The floor for rendered text.
 *
 * 14px, stated as a bar rather than inherited from a framework default. The
 * maintainer's complaint is "text and everything is small"; 14px is the point
 * below which body copy stops being comfortable at arm's length on a phone and
 * is the floor most design systems settle on. Nothing here is a WCAG
 * requirement - WCAG governs zoom and reflow, not absolute size - which is
 * exactly why an automated pass that only ran axe-core reported zero problems
 * on a console whose smallest live text is 9.3px.
 */
export const MIN_TEXT_PX = 14;
/** Viewports at or below this are treated as touch. */
export const MOBILE_MAX_PX = 480;

/** Composite a colour stack, element first, into the single colour painted. */
export function paintedBackground(stack: readonly string[]): Rgba {
  let result: Rgba = { red: 255, green: 255, blue: 255, alpha: 1 };
  for (let index = stack.length - 1; index >= 0; index -= 1) {
    result = over(parseColor(stack[index]), result);
  }
  return result;
}

function round(value: number, places = 2): number {
  const factor = 10 ** places;
  return Math.round(value * factor) / factor;
}

/** Keep only the worst finding per key, so one rule reports once. */
function worst(findings: readonly Finding[]): Finding[] {
  const byKey = new Map<string, Finding>();
  for (const finding of findings) {
    const existing = byKey.get(finding.key);
    if (existing === undefined) {
      byKey.set(finding.key, finding);
      continue;
    }
    const isWorse = finding.lowerIsWorse
      ? finding.measured < existing.measured
      : finding.measured > existing.measured;
    if (isWorse) byKey.set(finding.key, finding);
  }
  return [...byKey.values()].sort((left, right) => left.key.localeCompare(right.key));
}

function controlState(control: CollectedControl): string {
  return control.disabled ? 'disabled' : 'enabled';
}

function describeControl(control: CollectedControl): string {
  const label = control.label === '' ? '(no label)' : `"${control.label}"`;
  return `${control.signature} ${label}`;
}

/**
 * Everything wrong with one rendered page.
 *
 * `scope` names the page and theme so a finding can be traced back to a
 * screenshot without re-running anything.
 */
export function findingsFor(collected: Collected, scope: string): Finding[] {
  const found: Finding[] = [];
  const width = collected.viewport.width;
  const theme = collected.theme;

  // --- 1. Horizontal overflow ---------------------------------------------
  const overhang = collected.documentScrollWidth - collected.documentClientWidth;
  if (overhang > 1) {
    found.push({
      rule: 'overflow',
      key: `overflow:document@${width}`,
      detail:
        `the document scrolls ${round(overhang)}px wider than its ${collected.documentClientWidth}px ` +
        `viewport (scrollWidth ${collected.documentScrollWidth})`,
      measured: round(overhang),
      bar: 1,
      lowerIsWorse: false,
      where: scope,
    });
  }
  for (const element of collected.overflows) {
    found.push({
      rule: 'overflow',
      key: `overflow:${element.signature}@${width}`,
      detail:
        `${element.signature} reaches ${round(element.overhangPx)}px past the ${collected.documentClientWidth}px ` +
        `viewport and is not inside a scroller`,
      measured: round(element.overhangPx),
      bar: 1,
      lowerIsWorse: false,
      where: `${scope} | ${element.label}`,
    });
  }

  // --- 2. Contrast, on computed style, in every state ----------------------
  for (const control of collected.controls) {
    const background = paintedBackground(control.backgrounds);
    const ratio = contrastRatio(parseColor(control.color), background);
    const bar = requiredRatio({
      fontSizePx: control.fontSizePx,
      fontWeight: control.fontWeight,
      disabled: control.disabled,
    });
    if (ratio >= bar) continue;
    const state = controlState(control);
    found.push({
      rule: 'contrast',
      key: `contrast:${control.signature}:${state}@${theme}`,
      detail:
        `${describeControl(control)} draws ${control.color} on ${cssColor(background)} at ` +
        `${round(ratio)}:1, and a ${state} control at ${round(control.fontSizePx)}px has to clear ${bar}:1`,
      measured: round(ratio),
      bar,
      lowerIsWorse: true,
      where: scope,
    });
  }
  for (const text of collected.texts) {
    // Already covered, in enabled and disabled form, by the control pass.
    if (text.isControlLabel) continue;
    const background = paintedBackground(text.backgrounds);
    const ratio = contrastRatio(parseColor(text.color), background);
    const bar = requiredRatio({
      fontSizePx: text.fontSizePx,
      fontWeight: text.fontWeight,
      disabled: false,
    });
    if (ratio >= bar) continue;
    found.push({
      rule: 'contrast',
      key: `contrast:${text.signature}:text@${theme}`,
      detail:
        `text in ${text.signature} draws ${text.color} on ${cssColor(background)} at ${round(ratio)}:1, ` +
        `and ${round(text.fontSizePx)}px copy has to clear ${bar}:1`,
      measured: round(ratio),
      bar,
      lowerIsWorse: true,
      where: `${scope} | ${text.text}`,
    });
  }

  // --- 3. Minimum text size ------------------------------------------------
  for (const text of collected.texts) {
    if (text.fontSizePx >= MIN_TEXT_PX) continue;
    found.push({
      rule: 'text-size',
      key: `text-size:${text.signature}`,
      detail: `${text.signature} renders at ${round(text.fontSizePx)}px, below the ${MIN_TEXT_PX}px floor`,
      measured: round(text.fontSizePx),
      bar: MIN_TEXT_PX,
      lowerIsWorse: true,
      where: `${scope} | ${text.text}`,
    });
  }

  // --- 4. Touch targets, on phones only ------------------------------------
  if (width <= MOBILE_MAX_PX) {
    for (const control of collected.controls) {
      const smallest = Math.min(control.rect.width, control.rect.height);
      if (smallest >= TOUCH_TARGET_PX) continue;
      found.push({
        rule: 'touch-target',
        key: `touch-target:${control.signature}@${width}`,
        detail:
          `${describeControl(control)} is ${round(control.rect.width)}x${round(control.rect.height)} CSS px, ` +
          `under the ${TOUCH_TARGET_PX}x${TOUCH_TARGET_PX} WCAG 2.5.5 target`,
        measured: round(smallest),
        bar: TOUCH_TARGET_PX,
        lowerIsWorse: true,
        where: scope,
      });
    }
  }

  // --- 5. Clipping ---------------------------------------------------------
  for (const clip of collected.clips) {
    found.push({
      rule: 'clip',
      key: `clip:${clip.signature}<${clip.containerSignature}:${clip.axis}@${width}`,
      detail:
        `${clip.signature} runs ${round(clip.overhangPx)}px past ${clip.containerSignature}, which hides its ` +
        `overflow-${clip.axis}, so that much of it is cut off`,
      measured: round(clip.overhangPx),
      bar: 1,
      lowerIsWorse: false,
      where: scope,
    });
  }

  // --- 6. Overlapping controls ---------------------------------------------
  for (const overlap of collected.overlaps) {
    found.push({
      rule: 'overlap',
      key: `overlap:${[overlap.first, overlap.second].sort().join('+')}@${width}`,
      detail: `${overlap.first} and ${overlap.second} overlap over ${overlap.areaPx}px² and both take clicks`,
      measured: overlap.areaPx,
      bar: 1,
      lowerIsWorse: false,
      where: scope,
    });
  }

  // --- 7. Cells that are not cells -----------------------------------------
  // Found by looking at a screenshot rather than by any of the rules above,
  // which is why the screenshots are taken on a passing run too. The row
  // highlight and the bottom border stopped short of the last column and
  // nothing measured it, so now something does.
  for (const cell of collected.shortCells) {
    found.push({
      rule: 'table-cell',
      key: `table-cell:${cell.signature}@${width}`,
      detail:
        `${cell.signature} is display:${cell.display}, so the table wraps it in an anonymous cell ` +
        `and it stands ${round(cell.shortfallPx)}px short of its ${round(cell.rowHeightPx)}px row. ` +
        `The row's background and bottom border stop at the last real cell`,
      measured: round(cell.shortfallPx),
      bar: 1,
      lowerIsWorse: false,
      where: scope,
    });
  }

  return worst(found);
}

/**
 * Layout width, reported rather than asserted.
 *
 * "Doesn't use the full screen" is a real complaint and it is also a design
 * decision: whether a 1,312px measure inside a 1,920px window is restraint or
 * waste is not something a test gets to rule on. So this produces numbers for
 * a human and never a failure.
 */
export interface WidthReport {
  readonly scope: string;
  readonly viewportWidthPx: number;
  readonly contentWidthPx: number;
  readonly contentWidthFraction: number;
  readonly emptySidePx: number;
  readonly ceilings: readonly {
    readonly signature: string;
    readonly maxWidthPx: number;
    readonly usedWidthPx: number;
    readonly availableWidthPx: number;
  }[];
  /** The longest line of running text on the page, in characters. */
  readonly longestLineChars: number;
  readonly longestLineWhere: string;
}

export function widthReportFor(collected: Collected, scope: string): WidthReport {
  const longest = [...collected.texts]
    .filter((text) => text.lines > 0 && text.text.length > 25)
    .sort((left, right) => right.charactersPerLine - left.charactersPerLine)[0];
  const ceilings = new Map<string, Collected['widths'][number]>();
  for (const entry of collected.widths) {
    const existing = ceilings.get(entry.signature);
    if (existing === undefined || entry.usedWidthPx > existing.usedWidthPx) {
      ceilings.set(entry.signature, entry);
    }
  }
  return {
    scope,
    viewportWidthPx: collected.viewport.width,
    contentWidthPx: collected.contentWidthPx,
    contentWidthFraction: collected.contentWidthFraction,
    emptySidePx: round((collected.viewport.width - collected.contentWidthPx) / 2),
    ceilings: [...ceilings.values()].sort((left, right) => right.maxWidthPx - left.maxWidthPx),
    longestLineChars: longest?.charactersPerLine ?? 0,
    longestLineWhere: longest === undefined ? '' : `${longest.signature} | ${longest.text}`,
  };
}

function cssColor(color: Rgba): string {
  const channel = (value: number): number => Math.round(value);
  return `rgb(${channel(color.red)}, ${channel(color.green)}, ${channel(color.blue)})`;
}

/** Sorted worst-first within a rule, for a report a human reads top-down. */
export function rank(findings: readonly Finding[]): Finding[] {
  const severity = (finding: Finding): number =>
    finding.lowerIsWorse ? finding.measured / finding.bar : finding.bar / finding.measured;
  return [...findings].sort((left, right) => severity(left) - severity(right));
}
