/**
 * WCAG contrast arithmetic, over the colour strings a browser actually returns.
 *
 * This exists because four agent reviews, an axe-core pass and 91 unit tests
 * all missed a primary button rendering its label at 1.02:1. None of them look
 * at computed style. A rule painting `button.primary` in the dark theme
 * outranked `button.primary:disabled`, so every disabled Register and Execute
 * drew dark ink on a dark ground and simply was not there.
 *
 * The ratio itself is not the hard part. The hard part is that
 * `getComputedStyle` hands back four different colour syntaxes depending on
 * how the value was written, that half the console's colours are alpha steps
 * over whatever is behind them, and that `background: transparent` means "ask
 * my parent". So this module parses, composites, and only then divides.
 *
 * It is deliberately dependency-free and DOM-free: `contrast.test.ts` runs it
 * under `bun test`, and `e2e/audit.ts` runs the same functions in Node over
 * colours collected from a real Chromium. One implementation, two callers.
 */

/** A colour resolved to straight (non-premultiplied) sRGB with an alpha. */
export interface Rgba {
  /** 0-255. */
  readonly red: number;
  readonly green: number;
  readonly blue: number;
  /** 0-1. */
  readonly alpha: number;
}

/** WCAG 1.4.3 bars, plus the one this repo chose for disabled controls. */
export const AA_NORMAL_TEXT = 4.5;
export const AA_LARGE_TEXT = 3;
/**
 * The bar for a control the page has switched off.
 *
 * WCAG exempts "inactive user interface components" from 1.4.3 entirely, which
 * is exactly the licence under which a 1.02:1 button shipped. A disabled money
 * button still has to say what it is and why it will not work, so it is held
 * to the non-text bar rather than to nothing.
 */
export const DISABLED_TEXT = 3;

/**
 * Text at or above this size (or bold at `LARGE_TEXT_BOLD_PX`) is "large" and
 * clears at 3:1 instead of 4.5:1. WCAG states these in points; these are the
 * CSS pixel equivalents at the standard 4:3 ratio.
 */
export const LARGE_TEXT_PX = 24;
export const LARGE_TEXT_BOLD_PX = 18.66;
export const BOLD_WEIGHT = 700;

const TRANSPARENT: Rgba = { red: 0, green: 0, blue: 0, alpha: 0 };

/**
 * Parse a computed colour.
 *
 * Chromium serialises `rgb()`/`rgba()` for plain colours, `rgb(r g b / a)` for
 * some modern syntaxes, and `color(srgb x y z / a)` for anything that went
 * through `color-mix()`. The console uses all three: `--ink-06` is an `rgba`
 * token, and `tr.due` is a `color-mix`. Anything else throws rather than
 * defaulting, because a colour silently read as black is how a contrast suite
 * reports 21:1 for an invisible control.
 */
export function parseColor(value: string): Rgba {
  const text = value.trim().toLowerCase();
  if (text === 'transparent' || text === 'rgba(0, 0, 0, 0)') return TRANSPARENT;

  const rgb = /^rgba?\(([^)]+)\)$/.exec(text);
  if (rgb) {
    const parts = splitComponents(rgb[1]);
    if (parts.length < 3) throw new Error(`Unreadable colour: ${value}`);
    return {
      red: clampByte(parts[0]),
      green: clampByte(parts[1]),
      blue: clampByte(parts[2]),
      alpha: parts.length > 3 ? clampUnit(parts[3]) : 1,
    };
  }

  const srgb = /^color\(srgb ([^)]+)\)$/.exec(text);
  if (srgb) {
    const parts = splitComponents(srgb[1]);
    if (parts.length < 3) throw new Error(`Unreadable colour: ${value}`);
    return {
      red: clampByte(parts[0] * 255),
      green: clampByte(parts[1] * 255),
      blue: clampByte(parts[2] * 255),
      alpha: parts.length > 3 ? clampUnit(parts[3]) : 1,
    };
  }

  throw new Error(`Unreadable colour: ${value}`);
}

/**
 * Paint `top` over `bottom`, the way the compositor does.
 *
 * Every alpha step in `brand/tokens.css` needs this. `--ink-06` on a panel is
 * not a colour until you know what the panel is, and the console stacks up to
 * four of them.
 */
export function over(top: Rgba, bottom: Rgba): Rgba {
  if (top.alpha >= 1) return top;
  if (top.alpha <= 0) return bottom;
  const alpha = top.alpha + bottom.alpha * (1 - top.alpha);
  if (alpha === 0) return TRANSPARENT;
  const blend = (a: number, b: number): number =>
    (a * top.alpha + b * bottom.alpha * (1 - top.alpha)) / alpha;
  return {
    red: blend(top.red, bottom.red),
    green: blend(top.green, bottom.green),
    blue: blend(top.blue, bottom.blue),
    alpha,
  };
}

/** WCAG relative luminance of an opaque colour. */
export function relativeLuminance(color: Rgba): number {
  const channel = (value: number): number => {
    const scaled = value / 255;
    return scaled <= 0.03928 ? scaled / 12.92 : ((scaled + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(color.red) + 0.7152 * channel(color.green) + 0.0722 * channel(color.blue);
}

/**
 * The WCAG contrast ratio between two colours, 1 to 21.
 *
 * Both are composited onto `ground` first, so a translucent label over a
 * translucent panel is measured as painted rather than as authored.
 */
export function contrastRatio(foreground: Rgba, background: Rgba): number {
  const front = relativeLuminance(over(foreground, background));
  const back = relativeLuminance(background);
  const lighter = Math.max(front, back);
  const darker = Math.min(front, back);
  return (lighter + 0.05) / (darker + 0.05);
}

/** The bar this text has to clear, given its size and weight. */
export function requiredRatio(state: {
  fontSizePx: number;
  fontWeight: number;
  disabled: boolean;
}): number {
  if (state.disabled) return DISABLED_TEXT;
  const large =
    state.fontSizePx >= LARGE_TEXT_PX ||
    (state.fontWeight >= BOLD_WEIGHT && state.fontSizePx >= LARGE_TEXT_BOLD_PX);
  return large ? AA_LARGE_TEXT : AA_NORMAL_TEXT;
}

function splitComponents(body: string): number[] {
  return body
    .replace('/', ' ')
    .split(/[\s,]+/)
    .filter((part) => part !== '')
    .map((part) => (part.endsWith('%') ? Number(part.slice(0, -1)) / 100 : Number(part)));
}

function clampByte(value: number): number {
  if (!Number.isFinite(value)) throw new Error(`Unreadable colour component: ${value}`);
  return Math.min(255, Math.max(0, value));
}

function clampUnit(value: number): number {
  if (!Number.isFinite(value)) throw new Error(`Unreadable alpha: ${value}`);
  return Math.min(1, Math.max(0, value));
}
