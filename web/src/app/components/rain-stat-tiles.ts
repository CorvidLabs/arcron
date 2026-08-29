import { ChangeDetectionStrategy, Component, DestroyRef, inject } from '@angular/core';

import { RainService } from '../core/rain.service';
import { ArcronService } from '../core/arcron.service';
import { algos, roundsAsTime } from '@corvidlabs/arcron/format';

/**
 * The four numbers at the top of Rain, in the same tile row the registry uses.
 *
 * Keeper tiles would lie here: this hub is a different app. Same chrome,
 * different facts.
 */
@Component({
  selector: 'arcron-rain-stat-tiles',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <dl class="tiles">
      <div class="tile">
        <dt class="eyebrow">Rains</dt>
        <dd class="mono">{{ rain.rains().length }}</dd>
        <dd class="hint">{{ rainsHint() }}</dd>
      </div>

      <div class="tile">
        <dt class="eyebrow">Tickets</dt>
        <dd class="mono">{{ rain.hubTickets().toString() }}</dd>
        <dd class="hint">across every rain</dd>
      </div>

      <div class="tile" [class.live]="rain.hubStanding() === 'due'">
        <dt class="eyebrow">Next rain</dt>
        <dd class="mono">{{ nextLabel() }}</dd>
        <dd class="hint">{{ nextHint() }}</dd>
      </div>

      <div class="tile">
        <dt class="eyebrow">Pots</dt>
        <dd class="mono">{{ algos(rain.hubAlgoPot()) }}</dd>
        <dd class="hint">{{ potsHint() }}</dd>
      </div>
    </dl>
  `,
  styles: `
    .tiles {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 1px;
      margin: 0;
      background: var(--hairline);
      border: 1px solid var(--hairline);
      border-radius: 3px;
      overflow: hidden;
    }
    @media (max-width: 26rem) {
      .tiles { grid-template-columns: minmax(0, 1fr); }
    }
    @media (min-width: 56rem) {
      .tiles { grid-template-columns: repeat(4, minmax(0, 1fr)); }
    }
    .tile { background: var(--surface); padding: 0.9rem 1.1rem; }
    .tile dd { margin: 0.3rem 0 0; font-size: 1.35rem; font-weight: 500; letter-spacing: -0.01em; }
    .tile.live { background: var(--surface-strong); }
    .tile.live dd { color: var(--sheen-strong); }
    .tile.live::after {
      content: '';
      display: block;
      height: 2px;
      margin-top: 0.6rem;
      background: var(--iridescence);
    }
    .hint { margin: 0.25rem 0 0; font-size: 0.74rem; color: var(--text-faint); }
    .tile dd.hint { font-size: 0.74rem; font-weight: 400; letter-spacing: 0; }
  `,
})
export class RainStatTiles {
  protected readonly rain = inject(RainService);
  private readonly arcron = inject(ArcronService);
  protected readonly algos = algos;

  constructor() {
    const stop = this.rain.watch();
    inject(DestroyRef).onDestroy(stop);
  }

  protected rainsHint(): string {
    const count = this.rain.rains().length;
    if (this.rain.status() !== 'ready') return 'reading the hub…';
    if (count === 0) return 'none open yet';
    return count === 1 ? 'one box on this hub' : `${count} boxes on this hub`;
  }

  protected nextLabel(): string {
    const standing = this.rain.hubStanding();
    if (standing === null) return '—';
    if (standing === 'due') return 'Due';
    if (standing === 'scheduled') return 'Armed';
    return 'Waiting';
  }

  protected nextHint(): string {
    const standing = this.rain.hubStanding();
    const next = this.rain.nextHubRain();
    if (standing === null || next === null) return 'no rains yet';
    if (standing === 'waiting') return 'no tickets, or an empty pot';
    if (standing === 'due') return 'a rain can fire on the next draw';
    const time = roundsAsTime(next, this.arcron.secondsPerRound());
    return time === null ? `in ${next.toString()} rounds` : `in ~${time}`;
  }

  protected potsHint(): string {
    const asa = this.rain.hubAsaRains();
    if (asa === 0) return 'ALGO across every rain';
    return `ALGO, plus ${asa} ASA rain${asa === 1 ? '' : 's'}`;
  }
}
