import { ChangeDetectionStrategy, Component, computed, inject, input } from '@angular/core';

import { ArcronService } from '../core/arcron.service';
import { explorerUrl, type ExplorerKind } from '../core/explorer';

/**
 * A value that can be checked somewhere we do not control.
 *
 * Renders as a link where the network has an explorer and as plain text where
 * it does not, so no caller has to ask. The accessible name says what the link
 * goes to, because "769891898" on its own tells a screen reader user nothing,
 * and a page carrying several of these would otherwise read as a row of
 * numbers.
 *
 * `rel="noreferrer"` as well as `noopener`: an explorer has no business
 * learning which app id somebody was looking at when they left.
 */
@Component({
  selector: 'arcron-explorer-link',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (href(); as url) {
      <a
        [href]="url"
        target="_blank"
        rel="noopener noreferrer"
        [class.breakable]="breakable()"
        [attr.aria-label]="description()"
      >
        {{ text() }}<span class="away" aria-hidden="true">↗</span>
      </a>
    } @else {
      <span class="mono" [class.breakable]="breakable()">{{ text() }}</span>
    }
  `,
  styles: `
    /* Never break the id.

       overflow-wrap:anywhere let an application id split mid-number: a registry
       card on a phone showed "app 769891" on one line and "902" on the next,
       which does not read as a wrap, it reads as corrupted data. An id is a
       single token and the only honest options are to fit it, shrink it, or let
       it overflow its box — not to cut it in half.

       An address is different: it is 58 characters, it will not fit anywhere,
       and breaking it is the convention. That case sets .full and keeps the
       anywhere rule. */
    a {
      font-family: var(--font-mono);
      font-variant-numeric: tabular-nums;
      overflow-wrap: normal;
      word-break: keep-all;
      white-space: nowrap;
    }
    .away { margin-left: 0.2em; font-size: 0.85em; }
    span.mono {
      overflow-wrap: normal;
      word-break: keep-all;
      white-space: nowrap;
    }
    /* An address is 58 characters. It will not fit on a phone at any font size
       this console uses, and refusing to break it pushed the register page's
       document to 554px inside a 390px viewport. Breaking it is the convention
       and the only option that fits. */
    a.breakable,
    span.breakable {
      overflow-wrap: anywhere;
      word-break: break-all;
      white-space: normal;
      /* Back to inline. The global tap-target rule makes a table anchor
         inline-flex, which is an atomic box: a 58-character address wrapping
         inside one became a three-line block that overlapped the anchor beside
         it by 1367 square pixels, with both taking clicks. An address that
         wraps to three lines is already a large target and needs no minimum. */
      display: inline;
      min-height: 0;
      min-width: 0;
    }
  `,
})
export class ExplorerLink {
  /**
   * Whether this value may be broken across lines.
   *
   * An application id is a single token: splitting it produced "app 769891" on
   * one line and "902" on the next, which reads as corrupted data rather than
   * as a wrap. An address is 58 characters and cannot fit anywhere, so breaking
   * it is the only option that does not overflow the page.
   *
   * Derived from what the value is rather than left to each caller, because a
   * caller forgetting is exactly how both failures happened.
   */
  protected readonly breakable = computed(() => this.text().length > 20);

  private readonly arcron = inject(ArcronService);

  readonly kind = input.required<ExplorerKind>();
  /** The app id, address or transaction id itself. */
  readonly value = input.required<string>();
  /** What to show, if not the value in full. */
  readonly label = input<string>('');

  protected readonly href = computed(() => explorerUrl(this.arcron.network(), this.kind(), this.value()));

  protected readonly text = computed(() => {
    const given = this.label();
    return given === '' ? this.value() : given;
  });

  protected readonly description = computed(() => {
    const where = `on the ${this.arcron.config().label} block explorer`;
    switch (this.kind()) {
      case 'app':
        return `Application ${this.value()} ${where}`;
      case 'account':
        return `Account ${this.value()} ${where}`;
      case 'transaction':
        return `Transaction ${this.value()} ${where}`;
    }
  });
}
