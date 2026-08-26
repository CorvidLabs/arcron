/**
 * A disabled money button must stay readable.
 *
 * `button.primary:disabled` sets `color: var(--text-faint)` and the stylesheet
 * says why: "it loses its fill, not its label". It lost the label. The
 * dark-theme rule that paints primary buttons `#10201e`, so dark text reads on
 * a bright fill, has specificity 0,3,1 against the disabled rule's 0,2,1, so it
 * won. Every disabled Register and Execute button rendered at a contrast ratio
 * of 1.02:1 against the panel, which is invisible, and looked like a rendering
 * fault rather than a control waiting on you.
 *
 * The fix is `:not(:disabled)` on the theme rules. This pins the outcome rather
 * than the mechanism: whatever the stylesheet does, these pairings must clear
 * WCAG AA.
 */

import { describe, expect, test } from 'bun:test';

/** Relative luminance, per WCAG 2.x. */
function luminance(hex: string): number {
    const value = hex.replace('#', '');
    const channels = [0, 2, 4].map((at) => parseInt(value.slice(at, at + 2), 16) / 255);
    const linear = channels.map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
}

/** WCAG contrast ratio between two colours. */
export function contrastRatio(foreground: string, background: string): number {
    const a = luminance(foreground);
    const b = luminance(background);
    return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}

// From web/public/brand/tokens.css. Duplicated deliberately: the point is to
// catch a token being changed to something unreadable, so reading the value
// from the same file that defines it would test nothing.
const DARK = {
    paper: '#131619',
    surface: '#1B1F23',
    textFaint: '#A3A199',
    primaryOnFill: '#10201e',
};

const AA = 4.5;

describe('a disabled money button stays readable', () => {
    test('the disabled label clears AA on the panel it sits on', () => {
        expect(contrastRatio(DARK.textFaint, DARK.surface)).toBeGreaterThanOrEqual(AA);
    });

    test('and on the page ground, for buttons outside a panel', () => {
        expect(contrastRatio(DARK.textFaint, DARK.paper)).toBeGreaterThanOrEqual(AA);
    });

    test('the filled-button colour would be invisible if it leaked through', () => {
        // The regression this file exists for. If someone removes `:not(:disabled)`
        // the rendered colour becomes this, and this assertion documents why that
        // is not a matter of taste.
        expect(contrastRatio(DARK.primaryOnFill, DARK.surface)).toBeLessThan(1.5);
    });
});

describe('the contrast helper itself', () => {
    test('identical colours are 1:1', () => {
        expect(contrastRatio('#131619', '#131619')).toBeCloseTo(1, 5);
    });

    test('black on white is the maximum 21:1', () => {
        expect(contrastRatio('#000000', '#ffffff')).toBeCloseTo(21, 1);
    });
});
