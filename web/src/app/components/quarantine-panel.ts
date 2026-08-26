import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';

import { ArcronService } from '../core/arcron.service';
import type { Standing } from '../core/quarantine';

/**
 * What the visitor is told when the console is pointed somewhere it cannot
 * vouch for. Null when there is nothing to say.
 *
 * Exported as a function of plain state so its test runs the copy the console
 * actually renders, rather than a paraphrase of it. The two ids are the whole
 * message: "not the published app" is useless without the number you are on
 * and the number you should be on.
 */
export interface QuarantineNotice {
  readonly headline: string;
  readonly detail: string;
  readonly linkedAppId: number;
  readonly canonicalAppId: number;
  /** True while every money button on the page is dead because of this. */
  readonly moneyLocked: boolean;
}

export function quarantineNotice(state: {
  appId: number | null;
  canonicalAppId: number | null;
  networkLabel: string;
  standing: Standing;
  accepted: boolean;
}): QuarantineNotice | null {
  if (state.standing !== 'foreign') return null;
  const { appId, canonicalAppId } = state;
  if (appId === null || canonicalAppId === null) return null;
  return {
    headline: `This is not the Arcron deployment on ${state.networkLabel}.`,
    detail:
      `You are pointed at app ${appId}. The Arcron deployment is app ${canonicalAppId}. ` +
      `Anyone can deploy a contract with this contract's ABI and box layout, so a ` +
      `look-alike shows the same registry, accepts the same register form, and keeps ` +
      `whatever you escrow in it. If you arrived here from a link somebody sent you, go ` +
      `back to app ${canonicalAppId}. Continue only if app ${appId} is a deployment you ` +
      `run yourself.`,
    linkedAppId: appId,
    canonicalAppId,
    moneyLocked: !state.accepted,
  };
}

/**
 * The quarantine: a link naming an app that is not the published deployment.
 *
 * This used to be one notice among several in the trust banner, which warned
 * and gated nothing. Shareable links are how the console is meant to spread,
 * and a shareable link is the only attack this product has, so the warning had
 * to become a state rather than a paragraph: money is locked until the visitor
 * says otherwise, the id is never remembered (`entry.ts::storeAppId`), and the
 * way back is one button rather than a number to retype.
 */
@Component({
  selector: 'arcron-quarantine-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (notice(); as notice) {
      <!-- A div rather than an aside: alert is not an allowed role for a
           complementary landmark, and axe is right to say so. The assertive
           announcement matters more than the landmark here. -->
      <div class="quarantine" role="alert">
        <p class="eyebrow">Quarantined</p>
        <h2 class="headline">{{ notice.headline }}</h2>

        <dl class="ids">
          <div>
            <dt class="eyebrow">You are on</dt>
            <dd class="mono bad">app {{ notice.linkedAppId }}</dd>
          </div>
          <div>
            <dt class="eyebrow">Arcron is</dt>
            <dd class="mono good">app {{ notice.canonicalAppId }}</dd>
          </div>
        </dl>

        <p class="detail">{{ notice.detail }}</p>

        <div class="choices">
          <button type="button" class="primary" (click)="useCanonical()">
            Open app {{ notice.canonicalAppId }}
          </button>
          @if (notice.moneyLocked) {
            <button type="button" class="ghost" (click)="accept()">
              Continue to app {{ notice.linkedAppId }} anyway
            </button>
          }
        </div>

        <p class="state">
          @if (notice.moneyLocked) {
            Every button that spends or escrows ALGO is disabled until you continue. This
            app id is not being remembered: close the tab and the console comes back to
            app {{ notice.canonicalAppId }}.
          } @else {
            You chose to continue to app {{ notice.linkedAppId }}, so the actions below are
            live again. This app id is still not being remembered: reloading asks again.
          }
        </p>
      </div>
    }
  `,
  styles: `
    .quarantine {
      border: 2px solid var(--danger);
      border-radius: 3px;
      background: var(--surface);
      padding: 1.1rem 1.25rem;
      display: grid;
      gap: 0.7rem;
    }
    .eyebrow { margin: 0; }
    .quarantine > .eyebrow { color: var(--danger); }
    .headline { margin: 0; font-size: 1.15rem; color: var(--danger); }
    .ids {
      display: flex;
      flex-wrap: wrap;
      gap: 0.5rem 2rem;
      margin: 0;
      padding: 0.65rem 0.85rem;
      border: 1px solid var(--hairline);
      border-radius: 2px;
      background: var(--ink-06);
    }
    .ids dd { margin: 0.15rem 0 0; font-size: 1rem; font-weight: 600; }
    .ids .bad { color: var(--danger); }
    .ids .good { color: var(--success); }
    .detail { margin: 0; font-size: 0.86rem; max-width: 72ch; }
    .choices { display: flex; flex-wrap: wrap; gap: 0.5rem; }
    .state { margin: 0; color: var(--text-faint); font-size: 0.8rem; max-width: 72ch; }
  `,
})
export class QuarantinePanel {
  private readonly arcron = inject(ArcronService);

  protected readonly notice = computed(() =>
    quarantineNotice({
      appId: this.arcron.appId(),
      canonicalAppId: this.arcron.canonicalAppId(),
      networkLabel: this.arcron.config().label,
      standing: this.arcron.standing(),
      accepted: this.arcron.accepted(),
    }),
  );

  protected useCanonical(): void {
    this.arcron.useCanonicalApp();
  }

  protected accept(): void {
    this.arcron.acceptCurrentApp();
  }
}
