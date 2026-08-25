import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';

import { ArcronService } from '../core/arcron.service';
import { algos, duration } from '@corvidlabs/arcron/format';
import { isExecutable } from '@corvidlabs/arcron/upkeep';

@Component({
  selector: 'arcron-stat-tiles',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <dl class="tiles">
      <div class="tile">
        <dt class="eyebrow">Upkeeps</dt>
        <dd class="mono">{{ arcron.upkeeps().length }}</dd>
        <dd class="hint">{{ scheduleHint() }}</dd>
      </div>

      <div class="tile" [class.live]="dueNow() > 0">
        <dt class="eyebrow">Due now</dt>
        <dd class="mono">{{ dueNow() }}</dd>
        <dd class="hint">{{ dueHint() }}</dd>
      </div>

      <div class="tile">
        <dt class="eyebrow">Escrowed</dt>
        <dd class="mono">{{ escrowed() }}</dd>
        <dd class="hint">{{ escrowedExact() }}</dd>
      </div>

      <div class="tile" [class.bad]="arcron.solvent() === false">
        <dt class="eyebrow">App spendable</dt>
        <dd class="mono">{{ spendable() }}</dd>
        <dd class="hint">{{ solvencyHint() }}</dd>
      </div>
    </dl>
  `,
  styles: `
    .tiles {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(11.5rem, 1fr));
      gap: 1px;
      margin: 0;
      background: var(--hairline);
      border: 1px solid var(--hairline);
      border-radius: 3px;
      overflow: hidden;
    }
    .tile { background: var(--surface); padding: 0.9rem 1.1rem; }
    .tile dd { margin: 0.3rem 0 0; font-size: 1.35rem; font-weight: 500; letter-spacing: -0.01em; }
    /* --ink is the *text* colour and flips with the theme, so an emphasised
       tile lifts with --surface-strong, which stays a surface in both modes. */
    .tile.live { background: var(--surface-strong); }
    .tile.live dd { color: var(--sheen-strong); }
    .tile.live::after {
      content: '';
      display: block;
      height: 2px;
      margin-top: 0.6rem;
      background: var(--iridescence);
    }
    .tile.bad dd { color: var(--danger); }
    .hint { margin: 0.25rem 0 0; font-size: 0.74rem; color: var(--text-faint); }
    .tile dd.hint { font-size: 0.74rem; font-weight: 400; letter-spacing: 0; }
  `,
})
export class StatTiles {
  protected readonly arcron = inject(ArcronService);

  protected readonly dueNow = computed(() => {
    const round = this.arcron.round();
    return this.arcron.upkeeps().filter((upkeep) => isExecutable(upkeep, round)).length;
  });

  protected readonly escrowed = computed(() => algos(this.arcron.totalEscrowed()));
  protected readonly escrowedExact = computed(() => algos(this.arcron.totalEscrowed()));

  protected readonly spendable = computed(() => {
    const account = this.arcron.appAccount();
    return account === null ? '—' : algos(account.spendable);
  });

  /** The tightest cadence on the app, as time — "a heartbeat every ~28 s". */
  protected readonly scheduleHint = computed(() => {
    const upkeeps = this.arcron.upkeeps();
    if (upkeeps.length === 0) return 'nothing scheduled';
    const fastest = upkeeps.reduce(
      (best, upkeep) => (upkeep.intervalRounds < best ? upkeep.intervalRounds : best),
      upkeeps[0].intervalRounds,
    );
    return `fastest every ~${duration(Number(fastest) * this.arcron.secondsPerRound())}`;
  });

  protected readonly dueHint = computed(() =>
    this.dueNow() > 0 ? 'executable by anyone, right now' : 'all caught up',
  );

  protected readonly solvencyHint = computed(() => {
    const solvent = this.arcron.solvent();
    if (solvent === null) return 'no app selected';
    return solvent ? 'covers every escrow' : 'below total escrow';
  });
}
