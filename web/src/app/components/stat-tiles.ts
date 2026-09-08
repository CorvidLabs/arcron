import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';

import { ArcronService, type EscrowRead } from '../core/arcron.service';
import { algos, duration } from '@corvidlabs/arcron/format';
import { isExecutable } from '@corvidlabs/arcron/upkeep';

/**
 * What the App spendable tile says under its number.
 *
 * Exported so the test binds to the copy the console actually renders. The
 * first version said "covers every escrow" whenever `solvent` was true, and
 * `solvent` was true whenever the balance covered the boxes that had been
 * read, so a node refusing half the boxes made the tile *more* confident.
 * The tile now claims coverage only over a complete read, and says the read
 * was incomplete when it was, which is a statement about the console and not
 * about the app.
 */
export function solvencyHintFor(state: {
  appId: number | null;
  read: EscrowRead;
  solvent: boolean | null;
  unreadableBoxes: number;
  listedBoxes: number;
}): string {
  if (state.appId === null) return 'no app selected';
  switch (state.read) {
    case 'unread':
      return 'not read yet';
    case 'incomplete':
      return (
        `${state.unreadableBoxes} of ${state.listedBoxes} boxes unreadable; ` +
        `solvency unknown, not in doubt`
      );
    case 'torn':
      return 'balance moved during the read; solvency unknown, retrying';
    case 'complete':
      return state.solvent === true ? 'covers every escrow' : 'below total escrow';
  }
}

/**
 * What the Escrowed tile's total is spread across, and what it leaves out.
 *
 * A total summed over the boxes the node agreed to serve is a lower bound,
 * and a hint reading "across 20 upkeeps" under it presents the bound as the
 * total. When some boxes went unread the hint says so.
 */
export function escrowedHintFor(state: {
  upkeeps: number;
  unreadableBoxes: number;
}): string {
  const { upkeeps: count, unreadableBoxes: unread } = state;
  if (unread > 0) {
    const read = count === 1 ? '1 upkeep read' : `${count} upkeeps read`;
    return `across ${read}; ${unread} unreadable, so this is a lower bound`;
  }
  if (count === 0) return 'nothing registered yet';
  return count === 1 ? 'across 1 upkeep' : `across ${count} upkeeps`;
}

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
        <dd class="hint">{{ escrowedHint() }}</dd>
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
      /* Explicit counts, because there are exactly four tiles and auto-fit does
         not know that. It resolved to three columns between 600 and 900px,
         leaving one empty cell, and to six at 1280 leaving two. The grid paints
         its gaps from --hairline, so an empty cell is not space: it is a solid
         block the colour of a border, which reads as a tile that failed to
         render. Checking every width found the 1280 case, which a review of the
         same component had not. */
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 1px;
      margin: 0;
      background: var(--hairline);
      border: 1px solid var(--hairline);
      border-radius: 3px;
      overflow: hidden;
    }

    /* One column when two would be cramped. */
    @media (max-width: 26rem) {
      .tiles {
        grid-template-columns: minmax(0, 1fr);
      }
    }

    /* All four across once there is room, and never more than four. */
    @media (min-width: 56rem) {
      .tiles {
        grid-template-columns: repeat(4, minmax(0, 1fr));
      }
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

  /**
   * What the escrow total is spread across.
   *
   * This used to be the identical expression to `escrowed`, so the tile printed
   * its own value twice while every sibling tile's hint said something new.
   * Side by side with App spendable, which can hold the same number, it made
   * the whole row look like it had failed to compute.
   */
  protected readonly escrowedHint = computed(() =>
    escrowedHintFor({
      upkeeps: this.arcron.upkeeps().length,
      unreadableBoxes: this.arcron.unreadableBoxes(),
    }),
  );

  protected readonly spendable = computed(() => {
    const account = this.arcron.appAccount();
    return account === null ? '-' : algos(account.spendable);
  });

  /** The tightest cadence on the app, expressed as time ("a heartbeat every ~28 s"). */
  protected readonly scheduleHint = computed(() => {
    const upkeeps = this.arcron.upkeeps();
    if (upkeeps.length === 0) return 'nothing scheduled';
    const fastest = upkeeps.reduce(
      (best, upkeep) => (upkeep.intervalRounds < best ? upkeep.intervalRounds : best),
      upkeeps[0].intervalRounds,
    );
    return `fastest every ~${duration(Number(fastest) * this.arcron.secondsPerRound())}`;
  });

  /**
   * Upkeeps past their round that no keeper can run, because the escrow will
   * not cover one fee.
   *
   * These are the reason the tile and the table appeared to disagree. The rows
   * say "overdue by ~1 d 19 h" from the schedule, and `dueNow` counts only what
   * is executable, so a registry showing six overdue rows above a tile reading
   * "Due now 1" left the reader to work out that five of them were broke.
   */
  protected readonly starved = computed(() => {
    const round = this.arcron.round();
    return this.arcron
      .upkeeps()
      .filter((upkeep) => upkeep.nextExecutionRound <= round && !isExecutable(upkeep, round))
      .length;
  });

  protected readonly dueHint = computed(() => {
    const stuck = this.starved();
    if (this.dueNow() === 0) {
      return stuck > 0
        ? `none executable; ${stuck} overdue but out of escrow`
        : 'all caught up';
    }
    return stuck > 0
      ? `executable by anyone; ${stuck} more overdue but out of escrow`
      : 'executable by anyone, right now';
  });

  protected readonly solvencyHint = computed(() =>
    solvencyHintFor({
      appId: this.arcron.appId(),
      read: this.arcron.escrowRead(),
      solvent: this.arcron.solvent(),
      unreadableBoxes: this.arcron.unreadableBoxes(),
      listedBoxes: this.arcron.listedBoxes(),
    }),
  );
}
