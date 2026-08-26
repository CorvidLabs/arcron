import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';

import { ArcronService } from '../core/arcron.service';
import { NETWORKS } from '@corvidlabs/arcron/networks';

/**
 * What this app id is, and what its creator can still do to your money.
 *
 * The console takes an app id from a link and remembers it, which is a
 * feature for anyone running their own deployment and a phishing vector for
 * everyone else: the ABI and box layout are public, so a look-alike keeper
 * accepts the same register form and keeps the funds. Nothing else on the
 * page distinguishes the canonical deployment from a stranger's copy.
 *
 * The freeze flag is the other half. Until a creator calls `freeze`, they can
 * replace the programs and reach every escrow in the app. That is disclosed
 * in the docs and was invisible here, which is the wrong way round: the
 * disclosure belongs where the money is committed.
 */
@Component({
  selector: 'arcron-trust-banner',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (notice(); as notice) {
      <aside class="banner" [class]="notice.tone" role="note">
        <p class="headline">{{ notice.headline }}</p>
        <p class="detail">{{ notice.detail }}</p>
      </aside>
    }
  `,
  styles: `
    .banner {
      border: 1px solid var(--hairline);
      border-left-width: 3px;
      padding: 0.7rem 0.9rem;
      margin-bottom: 1.2rem;
    }
    .banner.warn { border-left-color: var(--warning); }
    .banner.bad { border-left-color: var(--danger); }
    .headline { margin: 0; font-weight: 600; font-size: 0.9rem; }
    .headline::before { content: '\\26A0\\FE0E'; margin-right: 0.5rem; }
    .detail {
      margin: 0.3rem 0 0;
      color: var(--text-faint);
      font-size: 0.82rem;
      max-width: 68ch;
    }
  `,
})
export class TrustBanner {
  private readonly arcron = inject(ArcronService);

  protected readonly notice = computed(() => {
    const appId = this.arcron.appId();
    if (appId === null || this.arcron.status() !== 'ready') return null;

    // An unrecognised app id outranks the freeze flag: a look-alike contract
    // can report whatever it likes about itself, so there is no point
    // reassuring anyone that a stranger's app says it is frozen.
    const canonical = NETWORKS[this.arcron.network()].defaultAppId;
    if (canonical !== undefined && appId !== canonical) {
      return {
        tone: 'bad',
        headline: `This is not the published app for ${this.arcron.config().label}.`,
        detail:
          `You are pointed at app ${appId}; the published one is ${canonical}. That is ` +
          `expected if you deployed your own. If you followed a link, stop: anyone can ` +
          `deploy a contract that looks exactly like this one, and anything you escrow ` +
          `here goes to whoever deployed it.`,
      };
    }

    if (this.arcron.frozen() === false) {
      return {
        tone: 'warn',
        headline: 'This deployment is not frozen.',
        detail:
          `Its creator can still replace the programs, which means they can reach every ` +
          `upkeep escrowed here, including yours. That is deliberate while the network is ` +
          `in alpha, because it is what lets a bug be fixed without asking everyone to ` +
          `cancel and re-register. It is still a power over your money, and only the ` +
          `creator calling freeze ends it.`,
      };
    }
    return null;
  });
}
