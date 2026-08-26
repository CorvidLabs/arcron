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
      <a [href]="url" target="_blank" rel="noopener noreferrer" [attr.aria-label]="description()">
        {{ text() }}<span class="away" aria-hidden="true">↗</span>
      </a>
    } @else {
      <span class="mono">{{ text() }}</span>
    }
  `,
  styles: `
    a {
      font-family: var(--font-mono);
      font-variant-numeric: tabular-nums;
      overflow-wrap: anywhere;
    }
    .away { margin-left: 0.2em; font-size: 0.85em; }
    span.mono { overflow-wrap: anywhere; }
  `,
})
export class ExplorerLink {
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
