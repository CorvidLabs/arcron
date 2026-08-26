import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';

import { ArcronService } from '../core/arcron.service';
import type { Standing } from '../core/quarantine';

interface Notice {
  readonly tone: 'warn' | 'bad';
  readonly headline: string;
  readonly detail: string;
}

export function noticesFor(state: {
  appId: number | null;
  standing: Standing;
  networkLabel: string;
  status: string;
  frozen: boolean | null;
  undecodableBoxes: number;
}): Notice[] {
  const { appId } = state;
  if (appId === null) return [];
  const found: Notice[] = [];

  // The identity of the app is not decided here any more. A foreign app id is
  // quarantined rather than warned about, which is a state of the whole page
  // and not a paragraph in a list: see `quarantine-panel.ts`. What is left
  // here is the case that cannot be quarantined, because there is nothing to
  // compare against.
  //
  // Deliberately not gated on connection status. This needs no chain data at
  // all, and gating it on a successful read meant one malformed box in a
  // hostile app switched the whole control off.
  if (state.standing === 'unverifiable') {
    found.push({
      tone: 'warn',
      headline: `No published app is recorded for ${state.networkLabel}.`,
      detail:
        `Nothing here can tell you whether app ${appId} is the one you meant. That is ` +
        `normal on a local network, where the app is whatever you just deployed. On any ` +
        `network carrying real value, treat it as unverified.`,
    });
  }

  if (state.status === 'error') {
    found.push({
      tone: 'bad',
      headline: 'This page is not showing you the current state of the app.',
      detail:
        `The last read failed, so what is below is stale or incomplete, and the freeze ` +
        `warning cannot be shown at all. Do not escrow anything until it recovers.`,
    });
  }

  const undecodable = state.undecodableBoxes;
  if (undecodable > 0) {
    found.push({
      tone: 'bad',
      headline: `${undecodable} box here does not decode as an upkeep.`,
      detail:
        `An honest deployment has none. This app is holding data shaped like an upkeep ` +
        `and is not one, which usually means it is a different contract wearing these ` +
        `box names.`,
    });
  }

  if (state.frozen === false) {
    found.push({
      tone: 'warn',
      headline: 'This deployment is not frozen.',
      detail:
        `Its creator can still replace the programs, which means they can reach every ` +
        `upkeep escrowed here, including yours. That is deliberate while the network is ` +
        `in alpha, because it is what lets a bug be fixed without asking everyone to ` +
        `cancel and re-register. It is still a power over your money, and only the ` +
        `creator calling freeze ends it.`,
    });
  }
  return found;
}

/**
 * What is worth knowing about this app that is not its identity.
 *
 * Identity moved out to `quarantine-panel.ts`, because a look-alike app is
 * not a paragraph to read past: it is a state the whole page is in. What is
 * left here is everything the console can only learn by reading, plus the one
 * identity case it cannot decide at all, which is a network with no published
 * deployment recorded.
 *
 * The freeze flag is the important half of what remains. Until a creator
 * calls `freeze`, they can replace the programs and reach every escrow in the
 * app. That is disclosed in the docs and was invisible here, which is the
 * wrong way round: the disclosure belongs where the money is committed.
 */
@Component({
  selector: 'arcron-trust-banner',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div aria-live="assertive">
      @for (notice of notices(); track notice.headline) {
        <aside class="banner" [class]="notice.tone">
          <h2 class="headline">{{ notice.headline }}</h2>
          <p class="detail">{{ notice.detail }}</p>
        </aside>
      }
    </div>
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
    .banner + .banner { margin-top: 0.6rem; }
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

  /**
   * Every notice that applies, most severe first.
   *
   * Ranked rather than exclusive. The first version returned at most one, so
   * an honest self-hoster saw the identity notice permanently and therefore
   * never saw the freeze warning for their own unfrozen app.
   */
  protected readonly notices = computed(() =>
    noticesFor({
      appId: this.arcron.appId(),
      standing: this.arcron.standing(),
      networkLabel: this.arcron.config().label,
      status: this.arcron.status(),
      frozen: this.arcron.frozen(),
      undecodableBoxes: this.arcron.undecodableBoxes(),
    }),
  );
}
