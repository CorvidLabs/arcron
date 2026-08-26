import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';

import { ArcronService } from '../core/arcron.service';
import { type BoardEntry, type SortKey, sortEntries, summarise, toEntry } from '@corvidlabs/arcron/board';
import { algos, dueLabel, intervalLabel, rounds, runwayLabel } from '@corvidlabs/arcron/format';
import { KeeperService } from '../core/keeper.service';
import { roundsUntilDue, toHex } from '@corvidlabs/arcron/upkeep';
import { WalletService } from '../core/wallet.service';

interface Row {
  readonly entry: BoardEntry;
  readonly id: string;
  readonly target: string;
  readonly selector: string;
  readonly reward: string;
  readonly netReward: string;
  /** True while lateness has pushed this upkeep's fee above its base. */
  readonly escalated: boolean;
  readonly cadence: string;
  readonly due: string;
  readonly runway: string;
  readonly lastRun: string;
  readonly canExecute: boolean;
}

const SORTS: readonly { key: SortKey; label: string }[] = [
  { key: 'reward', label: 'Reward' },
  { key: 'overdue', label: 'Most overdue' },
  { key: 'runway', label: 'Running out' },
  { key: 'cadence', label: 'Cadence' },
  { key: 'id', label: 'Number' },
];

@Component({
  selector: 'arcron-upkeep-board',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="panel">
      <header>
        <div>
          <h2>Work available</h2>
          <p class="subtitle">
            The registry as a keeper sees it. Every figure here comes from box state. No
            indexer, no backend.
          </p>
        </div>
        <label class="sort">
          <span class="eyebrow">Sort by</span>
          <select (change)="setSort($event)">
            @for (option of sorts; track option.key) {
              <option [value]="option.key" [selected]="option.key === sort()">
                {{ option.label }}
              </option>
            }
          </select>
        </label>
      </header>

      <dl class="stats">
        <div><dt class="eyebrow">Due now</dt><dd class="mono">{{ stats().due }}</dd></div>
        <div><dt class="eyebrow">Executions</dt><dd class="mono">{{ stats().totalExecutions }}</dd></div>
        <div><dt class="eyebrow">Paid to keepers</dt><dd class="mono">{{ paidToKeepers() }}</dd></div>
        <div><dt class="eyebrow">Median lateness</dt><dd class="mono">{{ medianLateness() }}</dd></div>
      </dl>

      @if (due().length > 0) {
        <div class="group">
          <h3>Claimable now <span class="count">{{ due().length }}</span></h3>
          <ul class="work">
            @for (row of due(); track row.entry.upkeep.id) {
              <li class="job">
                <div class="what">
                  <span class="mono id">#{{ row.id }}</span>
                  <span class="mono">app {{ row.target }}</span>
                  <span class="sub mono">{{ row.selector }}</span>
                </div>
                <div class="pay">
                  <strong class="mono" [class.escalated]="row.escalated">{{ row.netReward }}</strong>
                  <span class="sub">net of the {{ executionCost }} it costs to run</span>
                </div>
                <div class="when">
                  <span>{{ row.due }}</span>
                  <span class="sub">{{ row.runway }}</span>
                </div>
                <button
                  type="button"
                  class="primary small"
                  [disabled]="!row.canExecute || keeper.busy() !== null || !arcron.canWrite()"
                  (click)="execute(row)"
                >
                  Execute
                </button>
              </li>
            }
          </ul>
          @if (!wallet.connected()) {
            <p class="hint">Connect a wallet to claim any of these yourself.</p>
          }
        </div>
      } @else {
        <p class="empty">Nothing is due. Every funded upkeep has been serviced.</p>
      }

      @if (dormant().length > 0) {
        <div class="group">
          <h3 class="warn">Stuck: escrow below one fee <span class="count">{{ dormant().length }}</span></h3>
          <p class="subtitle">
            No keeper can execute these until the creator tops them up. Shown because
            hiding the network's failures helps nobody.
          </p>
          <ul class="work">
            @for (row of dormant(); track row.entry.upkeep.id) {
              <li class="job stuck">
                <div class="what">
                  <span class="mono id">#{{ row.id }}</span>
                  <span class="mono">app {{ row.target }}</span>
                </div>
                <div class="pay">
                  <span class="sub">
                    {{ row.reward }} fee@if (row.escalated) { <span class="escalated">(escalated)</span> }, {{ row.runway }}
                  </span>
                </div>
                <div class="when"><span class="sub">{{ row.due }}</span></div>
              </li>
            }
          </ul>
        </div>
      }

      @if (scheduled().length > 0) {
        <div class="group">
          <h3>Coming up <span class="count">{{ scheduled().length }}</span></h3>
          <ul class="work">
            @for (row of scheduled(); track row.entry.upkeep.id) {
              <li class="job">
                <div class="what">
                  <span class="mono id">#{{ row.id }}</span>
                  <span class="mono">app {{ row.target }}</span>
                </div>
                <div class="pay"><span class="sub">{{ row.netReward }} net</span></div>
                <div class="when">
                  <span class="sub">{{ row.due }}</span>
                  <span class="sub">{{ row.cadence }}</span>
                </div>
              </li>
            }
          </ul>
        </div>
      }
    </section>
  `,
  styles: `
    .panel { display: grid; gap: 1.25rem; }
    header { display: flex; flex-wrap: wrap; gap: 0.75rem 1.5rem; align-items: end; justify-content: space-between; }
    header h2 { margin: 0; font-size: 1.1rem; }
    .subtitle { margin: 0.2rem 0 0; color: var(--text-faint); font-size: 0.85rem; max-width: 62ch; }
    .sort { display: grid; gap: 0.25rem; }
    .stats {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(9rem, 1fr));
      gap: 1px;
      margin: 0;
      background: var(--hairline);
      border: 1px solid var(--hairline);
      border-radius: 3px;
      overflow: hidden;
    }
    .stats > div { background: var(--surface); padding: 0.7rem 0.9rem; }
    .stats dd { margin: 0.2rem 0 0; font-size: 1.05rem; }
    .group { display: grid; gap: 0.6rem; }
    .group h3 { margin: 0; font-size: 0.95rem; display: flex; align-items: center; gap: 0.5rem; }
    .group h3.warn { color: var(--warning); }
    .count {
      font-family: var(--font-mono);
      font-size: 0.72rem;
      padding: 0.05rem 0.4rem;
      border: 1px solid var(--hairline);
      border-radius: 999px;
      color: var(--text-faint);
    }
    .work { list-style: none; margin: 0; padding: 0; display: grid; gap: 0.4rem; }
    .job {
      display: grid;
      grid-template-columns: minmax(0, 2fr) minmax(0, 1.6fr) minmax(0, 1.4fr) auto;
      gap: 0.75rem;
      align-items: center;
      padding: 0.65rem 0.85rem;
      border: 1px solid var(--hairline);
      border-radius: 3px;
      background: var(--surface);
    }
    .job.stuck { background: var(--ink-06); }
    .id { color: var(--text-faint); margin-right: 0.4rem; }
    .pay strong { font-size: 1rem; color: var(--sheen-strong); }
    .sub { display: block; color: var(--text-faint); font-size: 0.76rem; }
    .escalated { color: var(--warning); }
    .empty { margin: 0; padding: 1.5rem; border: 1px dashed var(--hairline); border-radius: 3px; color: var(--text-faint); text-align: center; }
    .hint { margin: 0; color: var(--text-faint); font-size: 0.8rem; }
    @media (max-width: 52rem) {
      .job { grid-template-columns: minmax(0, 1fr) auto; }
    }
  `,
})
export class UpkeepBoard {
  protected readonly arcron = inject(ArcronService);
  protected readonly keeper = inject(KeeperService);
  protected readonly wallet = inject(WalletService);

  protected readonly sorts = SORTS;
  protected readonly sort = signal<SortKey>('reward');
  // The ALGO-only cost. An upkeep offering an ASA bonus costs a further
  // 1,000 for the transfer, which `netReward` accounts for per upkeep; this
  // is the headline figure for the common case.
  protected readonly executionCost = algos(3_000n);

  private readonly entries = computed(() => {
    const round = this.arcron.round();
    return sortEntries(
      this.arcron.upkeeps().map((upkeep) => toEntry(upkeep, round)),
      this.sort(),
    );
  });

  protected readonly stats = computed(() => summarise(this.entries()));
  protected readonly paidToKeepers = computed(() => algos(this.stats().paidToKeepers));
  protected readonly medianLateness = computed(() => {
    const stats = this.stats();
    if (stats.due === 0) return '-';
    return rounds(stats.medianLateness);
  });

  protected readonly due = computed(() => this.rowsFor('due'));
  protected readonly dormant = computed(() => this.rowsFor('dormant'));
  protected readonly scheduled = computed(() => this.rowsFor('scheduled'));

  protected setSort(event: Event): void {
    this.sort.set((event.target as HTMLSelectElement).value as SortKey);
  }

  protected execute(row: Row): void {
    void this.keeper.execute(row.entry.upkeep);
  }

  private rowsFor(availability: BoardEntry['availability']): Row[] {
    const round = this.arcron.round();
    const pace = this.arcron.secondsPerRound();
    const canSign = this.wallet.connected();
    return this.entries()
      .filter((entry) => entry.availability === availability)
      .map((entry) => ({
        entry,
        id: String(entry.upkeep.id),
        target: String(entry.upkeep.targetApp),
        selector: `0x${toHex(entry.upkeep.callArgs[0] ?? new Uint8Array())}`,
        // What it pays now, not what it was registered at: escalation exists
        // to change which work a keeper reaches for first.
        reward: algos(entry.currentFee),
        netReward: algos(entry.netReward, { sign: true }),
        escalated: entry.escalated,
        cadence: intervalLabel(entry.upkeep.intervalRounds, pace),
        due: dueLabel(roundsUntilDue(entry.upkeep, round), pace),
        runway: runwayLabel(entry.runsRemaining, entry.upkeep.intervalRounds, pace),
        lastRun:
          entry.lastExecutionRound === null
            ? 'never run'
            : `last ran at round ${entry.lastExecutionRound}`,
        canExecute: availability === 'due' && canSign,
      }));
  }
}
