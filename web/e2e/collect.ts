/**
 * What the browser is asked for, and nothing more.
 *
 * Everything in this file runs inside the page, so it may not close over
 * anything from the test process and may only return structured-cloneable
 * data. It measures and reports; it decides nothing. The judgement - which
 * ratio clears which bar, which target is too small, which overflow matters -
 * happens in `audit.ts`, in Node, where the same functions the unit tests
 * cover can be reused.
 *
 * That split is deliberate. An in-page assertion can only ever say "something
 * failed"; a payload of measurements says which element, at which viewport,
 * by how much, and can be written next to a screenshot for a human to read.
 */

/** A colour stack: the element's own background, then each ancestor's. */
export interface Painted {
  readonly color: string;
  /** Bottom-up compositing happens in Node; this is top-down, element first. */
  readonly backgrounds: readonly string[];
}

export interface Box {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
}

export interface CollectedControl extends Painted {
  /** A short, stable name for the rule that produced this: `button.primary.small`. */
  readonly signature: string;
  readonly tag: string;
  readonly label: string;
  readonly disabled: boolean;
  readonly fontSizePx: number;
  readonly fontWeight: number;
  readonly rect: Box;
  /** The painted border, which is all a ghost button has to mark its edge. */
  readonly borderColor: string;
  readonly borderWidthPx: number;
}

export interface CollectedText extends Painted {
  readonly signature: string;
  readonly text: string;
  /** True when this text is a button's or link's own label. */
  readonly isControlLabel: boolean;
  readonly fontSizePx: number;
  readonly fontWeight: number;
  readonly rect: Box;
  /** Characters per rendered line, averaged over the line boxes this text drew. */
  readonly charactersPerLine: number;
  readonly lines: number;
}

export interface CollectedOverflow {
  readonly signature: string;
  readonly label: string;
  /** How far past the document's client width this element's box reaches. */
  readonly overhangPx: number;
  readonly rect: Box;
}

export interface CollectedClip {
  readonly signature: string;
  readonly containerSignature: string;
  readonly axis: 'x' | 'y';
  readonly overhangPx: number;
}

export interface CollectedOverlap {
  readonly first: string;
  readonly second: string;
  readonly areaPx: number;
}

/** A `td` or `th` whose box is shorter than the row it is supposed to fill. */
export interface CollectedShortCell {
  readonly signature: string;
  readonly display: string;
  readonly shortfallPx: number;
  readonly rowHeightPx: number;
}

export interface CollectedWidth {
  readonly signature: string;
  readonly maxWidthPx: number;
  readonly usedWidthPx: number;
  readonly availableWidthPx: number;
}

export interface Collected {
  readonly viewport: { readonly width: number; readonly height: number };
  readonly documentScrollWidth: number;
  readonly documentClientWidth: number;
  readonly theme: string;
  readonly controls: readonly CollectedControl[];
  readonly texts: readonly CollectedText[];
  readonly overflows: readonly CollectedOverflow[];
  readonly clips: readonly CollectedClip[];
  readonly overlaps: readonly CollectedOverlap[];
  readonly shortCells: readonly CollectedShortCell[];
  readonly widths: readonly CollectedWidth[];
  /** The fraction of the viewport width the main content column actually uses. */
  readonly contentWidthFraction: number;
  readonly contentWidthPx: number;
}

/**
 * The whole audit, as one function to hand to `page.evaluate`.
 *
 * Everything it needs is declared inside it: Playwright serialises the source
 * and evaluates it in a fresh scope, so a reference to anything at module level
 * would be a `ReferenceError` in the page rather than a compile error here.
 */
export function collect(): Collected {
  const INTERACTIVE =
    'button, a[href], input, select, textarea, summary, [role="button"], [role="link"]';
  const root = document.documentElement;
  const viewport = { width: window.innerWidth, height: window.innerHeight };
  const clientWidth = root.clientWidth;

  const signatureOf = (element: Element): string => {
    const tag = element.tagName.toLowerCase();
    const classes = Array.from(element.classList)
      // Angular's emulated encapsulation stamps `_ngcontent-*` attributes, not
      // classes, but framework-generated state classes do appear and change
      // per interaction. Keeping them would make every signature unstable.
      .filter((name) => !name.startsWith('ng-') && !name.startsWith('_ng'))
      .sort();
    return classes.length === 0 ? tag : `${tag}.${classes.join('.')}`;
  };

  const textOf = (element: Element): string => {
    const text = (element.textContent ?? '').replace(/\s+/g, ' ').trim();
    return text.length > 60 ? `${text.slice(0, 57)}…` : text;
  };

  const isHiddenOffscreen = (rect: DOMRect): boolean =>
    // The skip link parks itself at -9999px until focused. That is not overflow.
    rect.left < -1000 || rect.top < -1000;

  const visible = (element: Element, style: CSSStyleDeclaration, rect: DOMRect): boolean => {
    if (style.display === 'none' || style.visibility === 'hidden') return false;
    if (Number(style.opacity) === 0) return false;
    if (rect.width < 2 || rect.height < 2) return false;
    if (isHiddenOffscreen(rect)) return false;
    // `.sr-only` is a 1px clipped box; the size check above catches it, but a
    // screen-reader-only wrapper can still be larger, so the clip is checked too.
    if (style.clip === 'rect(0px, 0px, 0px, 0px)') return false;
    // Deliberately NOT `closest('.sr-only')`. Treating the class as an exemption
    // marker meant a rule that un-hid the element also hid it from this suite: a
    // thead carried `.sr-only` while a second, unguarded rule cancelled it, and
    // nine full-width header bands rendered above the registry at every width
    // while every check passed. The class now has to be doing its job to earn
    // the skip, which the two lines above already decide, so an element that is
    // visibly rendered is measured whatever class it carries.
    if (element.closest('.sr-only') !== null && !isVisuallyClipped(element)) {
      return true;
    }
    if (element.closest('.sr-only') !== null) return false;
    return true;
  };

  /**
   * Whether an element carrying `.sr-only` is actually clipped.
   *
   * The class is a promise, not a proof. This checks the promise is kept: the
   * box is 1px-ish, or clipped by `clip`/`clip-path`, or genuinely out of flow
   * and off-screen. Anything else claiming `.sr-only` is on the page and is
   * measured like everything else on the page.
   */
  const isVisuallyClipped = (element: Element): boolean => {
    const marker = element.closest('.sr-only');
    if (marker === null) return true;
    const style = window.getComputedStyle(marker);
    const rect = marker.getBoundingClientRect();
    if (rect.width < 2 || rect.height < 2) return true;
    if (style.clip === 'rect(0px, 0px, 0px, 0px)') return true;
    if (style.clipPath !== 'none') return true;
    if (isHiddenOffscreen(rect)) return true;
    return false;
  };

  const boxOf = (rect: DOMRect): Box => ({
    x: Math.round(rect.x * 100) / 100,
    y: Math.round(rect.y * 100) / 100,
    width: Math.round(rect.width * 100) / 100,
    height: Math.round(rect.height * 100) / 100,
  });

  /**
   * The element's own background, then every ancestor's, element first.
   *
   * The whole chain is returned rather than stopping at the first opaque
   * layer, because deciding which layer is opaque means parsing four different
   * colour syntaxes and that parser already exists, unit-tested, in
   * `src/app/core/contrast.ts`. Compositing the stack in Node reuses it
   * instead of growing a second copy in here that nothing covers.
   */
  const backgroundsOf = (element: Element): string[] => {
    const stack: string[] = [];
    let current: Element | null = element;
    while (current !== null) {
      stack.push(window.getComputedStyle(current).backgroundColor);
      current = current.parentElement;
    }
    // The canvas takes the body's background when html has none, so a stack
    // that reached the top with nothing opaque still ends on paper.
    stack.push(window.getComputedStyle(document.body).backgroundColor);
    return stack;
  };

  const controls: CollectedControl[] = [];
  for (const element of Array.from(document.querySelectorAll(INTERACTIVE))) {
    const style = window.getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    if (!visible(element, style, rect)) continue;
    controls.push({
      signature: signatureOf(element),
      tag: element.tagName.toLowerCase(),
      label:
        textOf(element) ||
        element.getAttribute('aria-label') ||
        (element as HTMLInputElement).placeholder ||
        '',
      disabled:
        (element as HTMLButtonElement).disabled === true ||
        element.getAttribute('aria-disabled') === 'true',
      fontSizePx: Number.parseFloat(style.fontSize),
      fontWeight: Number(style.fontWeight) || 400,
      rect: boxOf(rect),
      color: style.color,
      backgrounds: backgroundsOf(element),
      borderColor: style.borderTopColor,
      borderWidthPx: Number.parseFloat(style.borderTopWidth) || 0,
    });
  }

  const texts: CollectedText[] = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node !== null; node = walker.nextNode()) {
    const content = (node.nodeValue ?? '').replace(/\s+/g, ' ').trim();
    if (content === '') continue;
    const parent = node.parentElement;
    if (parent === null) continue;
    const style = window.getComputedStyle(parent);
    const rect = parent.getBoundingClientRect();
    if (!visible(parent, style, rect)) continue;

    // Line boxes, not the parent's box: a paragraph that wraps four times has
    // four client rects, and the text length over that is characters per line.
    const range = document.createRange();
    range.selectNodeContents(node);
    const lineBoxes = Array.from(range.getClientRects()).filter((line) => line.width > 1);
    range.detach();

    texts.push({
      signature: signatureOf(parent),
      text: content.length > 60 ? `${content.slice(0, 57)}…` : content,
      // A button's own label is audited by the control pass, in every state.
      // Its size still matters here; its contrast would be the same finding
      // under a second key.
      isControlLabel: parent.matches(INTERACTIVE),
      fontSizePx: Number.parseFloat(style.fontSize),
      fontWeight: Number(style.fontWeight) || 400,
      rect: boxOf(rect),
      lines: lineBoxes.length,
      charactersPerLine: lineBoxes.length === 0 ? 0 : Math.round(content.length / lineBoxes.length),
      color: style.color,
      backgrounds: backgroundsOf(parent),
    });
  }

  // --- Horizontal overflow -------------------------------------------------
  // The document scroll width is the headline number, but it says nothing
  // about what caused it, so every element whose box reaches past the client
  // width is named. An element inside a deliberate `overflow-x: auto` scroller
  // is excluded: that content is reachable by design.
  const scrollable = (element: Element): boolean => {
    let current: Element | null = element.parentElement;
    while (current !== null && current !== document.documentElement) {
      const overflow = window.getComputedStyle(current).overflowX;
      if (overflow === 'auto' || overflow === 'scroll') return true;
      current = current.parentElement;
    }
    return false;
  };

  const overflows: CollectedOverflow[] = [];
  for (const element of Array.from(document.body.querySelectorAll('*'))) {
    const style = window.getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    if (!visible(element, style, rect)) continue;
    const overhang = rect.right - clientWidth;
    if (overhang <= 1) continue;
    if (scrollable(element)) continue;
    overflows.push({
      signature: signatureOf(element),
      label: textOf(element),
      overhangPx: Math.round(overhang * 100) / 100,
      rect: boxOf(rect),
    });
  }

  // --- Clipping ------------------------------------------------------------
  // Content cut off by an ancestor that hides its overflow. `auto`/`scroll`
  // are excluded on the same reasoning as above: those can be scrolled to.
  const clips: CollectedClip[] = [];
  for (const element of Array.from(document.body.querySelectorAll('*'))) {
    const style = window.getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    if (!visible(element, style, rect)) continue;
    let container: Element | null = element.parentElement;
    while (container !== null && container !== document.documentElement) {
      const containerStyle = window.getComputedStyle(container);
      const containerRect = container.getBoundingClientRect();
      if (containerStyle.overflowX === 'hidden' || containerStyle.overflowX === 'clip') {
        const overhang = rect.right - containerRect.right;
        if (overhang > 1) {
          clips.push({
            signature: signatureOf(element),
            containerSignature: signatureOf(container),
            axis: 'x',
            overhangPx: Math.round(overhang * 100) / 100,
          });
        }
      }
      if (containerStyle.overflowY === 'hidden' || containerStyle.overflowY === 'clip') {
        const overhang = rect.bottom - containerRect.bottom;
        if (overhang > 1) {
          clips.push({
            signature: signatureOf(element),
            containerSignature: signatureOf(container),
            axis: 'y',
            overhangPx: Math.round(overhang * 100) / 100,
          });
        }
      }
      container = container.parentElement;
    }
  }

  /**
   * How much of two elements genuinely sits on top of each other.
   *
   * Compares every line box of one against every line box of the other, so a
   * wrapped inline is measured as the lines a pointer can hit rather than as
   * the rectangle enclosing all of them.
   */
  const overlapArea = (first: Element, second: Element): number => {
    const firstLines = Array.from(first.getClientRects()).filter((r) => r.width > 1 && r.height > 1);
    const secondLines = Array.from(second.getClientRects()).filter((r) => r.width > 1 && r.height > 1);
    let total = 0;
    for (const a of firstLines) {
      for (const b of secondLines) {
        const width = Math.min(a.right, b.right) - Math.max(a.left, b.left);
        const height = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
        if (width > 1 && height > 1) total += width * height;
      }
    }
    return total;
  };

  // --- Overlapping controls ------------------------------------------------
  // Two things you can click sitting on top of each other. Nesting is not
  // overlap: a label wrapping its own input is the normal case.
  const overlaps: CollectedOverlap[] = [];
  const clickable = Array.from(document.querySelectorAll(INTERACTIVE)).filter((element) => {
    const style = window.getComputedStyle(element);
    return visible(element, style, element.getBoundingClientRect());
  });
  for (let i = 0; i < clickable.length; i += 1) {
    for (let j = i + 1; j < clickable.length; j += 1) {
      const first = clickable[i];
      const second = clickable[j];
      if (first.contains(second) || second.contains(first)) continue;
      // Per-line boxes, not the bounding rect. An inline element that wraps has
      // a bounding rect covering every line it occupies, so a 58-character
      // address broken over three lines reported a tall rectangle that
      // horizontally overlapped the id link beside it on the first line, 1452
      // square pixels of overlap that nothing could ever click. What a pointer
      // actually hits is the individual line boxes, and those did not touch.
      //
      // This is strictly more accurate rather than more permissive: a genuine
      // overlap of two boxes still overlaps line for line.
      const area = overlapArea(first, second);
      if (area <= 1) continue;
      overlaps.push({
        first: signatureOf(first),
        second: signatureOf(second),
        areaPx: Math.round(area),
      });
    }
  }

  // --- Cells that are not cells --------------------------------------------
  // A `display` that is not `table-cell` takes a `td` out of the table's own
  // layout: the browser wraps it in an anonymous cell, and the row's
  // background and bottom border stop at the last real cell. The measurable
  // symptom is a cell shorter than its row, which cannot happen to a cell the
  // table is actually laying out.
  const shortCells: CollectedShortCell[] = [];
  for (const cell of Array.from(document.querySelectorAll('td, th'))) {
    const row = cell.closest('tr');
    if (row === null) continue;
    const style = window.getComputedStyle(cell);
    const cellRect = cell.getBoundingClientRect();
    const rowRect = row.getBoundingClientRect();
    if (!visible(cell, style, cellRect)) continue;
    const shortfall = rowRect.height - cellRect.height;
    if (shortfall <= 1) continue;
    shortCells.push({
      signature: signatureOf(cell),
      display: style.display,
      shortfallPx: Math.round(shortfall * 100) / 100,
      rowHeightPx: Math.round(rowRect.height * 100) / 100,
    });
  }

  // --- Layout width --------------------------------------------------------
  // "Doesn't use the full screen", made into two numbers: what the content
  // column actually measures, and every max-width that is holding it in.
  const widths: CollectedWidth[] = [];
  for (const element of Array.from(document.body.querySelectorAll('*'))) {
    const style = window.getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    if (!visible(element, style, rect)) continue;
    const maxWidth = style.maxWidth;
    if (maxWidth === 'none' || maxWidth === '') continue;
    const parent = element.parentElement;
    const available = parent === null ? clientWidth : parent.getBoundingClientRect().width;
    const resolved = Number.parseFloat(maxWidth);
    if (!Number.isFinite(resolved)) continue;
    // Only report a ceiling that is actually binding right now.
    if (rect.width >= available - 1) continue;
    widths.push({
      signature: signatureOf(element),
      maxWidthPx: Math.round(resolved * 100) / 100,
      usedWidthPx: Math.round(rect.width * 100) / 100,
      availableWidthPx: Math.round(available * 100) / 100,
    });
  }

  const main = document.querySelector('main');
  const contentWidthPx = main === null ? 0 : main.getBoundingClientRect().width;

  return {
    viewport,
    documentScrollWidth: root.scrollWidth,
    documentClientWidth: clientWidth,
    theme: root.dataset['theme'] ?? 'system',
    controls,
    texts,
    overflows,
    clips,
    overlaps,
    shortCells,
    widths,
    contentWidthPx: Math.round(contentWidthPx * 100) / 100,
    contentWidthFraction:
      viewport.width === 0 ? 0 : Math.round((contentWidthPx / viewport.width) * 1000) / 1000,
  };
}
