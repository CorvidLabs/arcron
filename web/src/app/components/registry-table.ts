import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';

import { ArchonService } from '../core/archon.service';
import { algos, dueLabel, intervalLabel, microAlgos, runwayLabel, shortAddress } from '../core/format';
import { KeeperService } from '../core/keeper.service';
import { WalletService } from '../core/wallet.service';
import {
  effectiveFee,
  escalates,
  executionsRemaining,
  isExecutable,
  roundsUntilDue,
  SKIP_AHEAD,
  toHex,
  type Upkeep,
} from '../core/upkeep';

interface Row {
  readonly upkeep: Upkeep;
  readonly id: string;
  readonly target: string;
  readonly selector: string;
  readonly creator: string;
  readonly yours: boolean;
  readonly interval: string;
  readonly nextRound: string;
  readonly due: string;
  readonly fee: string;
  readonly feeExact: string;
  /** Present only while escalation has pushed the fee above its base. */
  readonly feeNow: string | null;
  readonly policy: string;
  readonly ceiling: string | null;
  readonly lastRan: string;
  readonly balance: string;
  readonly runway: string;
  readonly executed: string;
  readonly state: 'due' | 'scheduled' | 'starved';
  readonly canExecute: boolean;
  readonly canCancel: boolean;
  readonly canFund: boolean;
}

@Component({
  selector: 'archon-registry-table',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="panel">
      <header>
        <div>
          <h2>Upkeep registry</h2>
          <p class="subtitle">
            One box per upkeep, read straight from algod — no wallet, no indexer, no permission.
          </p>
        </div>
        <p class="eyebrow">{{ summary() }}</p>
      </header>

      @if (archon.appId() === null) {
        <p class="empty">Enter a keeper app id to load its registry.</p>
      } @else if (rows().length === 0) {
        <p class="empty">
          No upkeeps on app {{ archon.appId() }} yet. Register one below to watch the network
          work.
        </p>
      } @else {
        <div class="scroll">
          <table>
            <caption class="sr-only">Registered upkeeps</caption>
            <thead>
              <tr>
                <th scope="col">#</th>
                <th scope="col">Target</th>
                <th scope="col">Cadence</th>
                <th scope="col">Next run</th>
                <th scope="col">Fee</th>
                <th scope="col">Escrow</th>
                <th scope="col">Runway</th>
                <th scope="col">Runs</th>
                <th scope="col"><span class="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              @for (row of rows(); track row.upkeep.id) {
                <tr [class]="row.state">
                  <th scope="row" class="mono">
                    {{ row.id }}
                    @if (row.yours) {
                      <span class="yours" title="Registered by the connected account">yours</span>
                    }
                  </th>
                  <td>
                    <span class="mono">app {{ row.target }}</span>
                    <span class="sub mono">{{ row.selector }}</span>
                  </td>
                  <td>
                    <span class="mono">{{ row.interval }}</span>
                  </td>
                  <td>
                    <span class="mono">{{ row.nextRound }}</span>
                    <span class="sub" [class.now]="row.state === 'due'">{{ row.due }}</span>
                  </td>
                  <td class="mono" [title]="row.feeExact">
                    @if (row.feeNow) {
                      <span class="escalated" title="escalated: this upkeep is late">{{ row.feeNow }}</span>
                      <span class="sub">base {{ row.fee }}</span>
                    } @else {
                      {{ row.fee }}
                      @if (row.ceiling) {
                        <span class="sub">up to {{ row.ceiling }}</span>
                      }
                    }
                  </td>
                  <td class="mono">{{ row.balance }}</td>
                  <td>
                    <span class="sub">{{ row.runway }}</span>
                  </td>
                  <td class="mono">{{ row.executed }}</td>
                  <td class="actions">
                    <button
                      type="button"
                      class="primary small"
                      [disabled]="!row.canExecute || keeper.busy() !== null"
                      (click)="execute(row)"
                    >
                      Execute
                    </button>
                    <button
                      type="button"
                      class="ghost small"
                      [disabled]="!row.canFund"
                      (click)="toggle(row)"
                    >
                      Top up
                    </button>
                    @if (row.canCancel) {
                      <button
                        type="button"
                        class="ghost small danger"
                        [disabled]="keeper.busy() !== null"
                        (click)="cancel(row)"
                      >
                        Cancel
                      </button>
                    }
                  </td>
                </tr>
                @if (expanded() === row.upkeep.id) {
                  <tr class="drawer">
                    <td colspan="9">
                      <form class="top-up" (submit)="topUp($event, row)">
                        <label>
                          <span class="eyebrow">Add to escrow (ALGO)</span>
                          <input
                            type="number"
                            name="amount"
                            step="any"
                            min="0.000001"
                            [value]="defaultTopUp(row)"
                            required
                          />
                        </label>
                        <button type="submit" class="primary small" [disabled]="keeper.busy() !== null">
                          Fund upkeep {{ row.id }}
                        </button>
                        <p class="hint">
                          Anyone can top up an upkeep — funding is not creator-only. Registered by
                          {{ row.creator }}. If a run is missed it {{ row.policy }}; last ran
                          {{ row.lastRan }}.
                          @if (row.ceiling) {
                            A late run pays up to {{ row.ceiling }}, so the escrow needs that much
                            to stay executable.
                          }
                        </p>
                      </form>
                    </td>
                  </tr>
                }
              }
            </tbody>
          </table>
        </div>

        <p class="legend">
          <span class="chip due">due</span> executable now
          <span class="chip scheduled">scheduled</span> waiting for its round
          <span class="chip starved">starved</span> escrow below one fee
        </p>
      }
    </section>
  `,
  styles: `
    .panel { display: grid; gap: 1.1rem; }
    header { display: flex; flex-wrap: wrap; gap: 0.5rem 1.5rem; align-items: baseline; justify-content: space-between; }
    header h2 { margin: 0; font-size: 1.1rem; }
    .subtitle { margin: 0.2rem 0 0; color: var(--text-faint); font-size: 0.85rem; }
    .empty {
      margin: 0;
      padding: 1.75rem;
      border: 1px dashed var(--hairline);
      border-radius: 3px;
      color: var(--text-faint);
      text-align: center;
    }
    .scroll { overflow-x: auto; border: 1px solid var(--hairline); border-radius: 3px; }
    table { width: 100%; border-collapse: collapse; font-size: 0.88rem; }
    th, td { padding: 0.65rem 0.8rem; text-align: left; border-bottom: 1px solid var(--hairline); vertical-align: top; }
    thead th {
      font-family: var(--font-mono);
      font-size: 0.68rem;
      font-weight: 500;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      color: var(--text-faint);
      background: var(--ink-06);
    }
    tbody tr:last-child td, tbody tr:last-child th { border-bottom: none; }
    tbody th { color: var(--text-faint); font-weight: 500; }
    .sub { display: block; color: var(--text-faint); font-size: 0.76rem; }
    .escalated { color: var(--warning); font-weight: 600; }
    td .mono, .sub { text-wrap: nowrap; }
    .sub.now { color: var(--sheen); font-weight: 500; }
    .yours {
      display: inline-block;
      margin-left: 0.35rem;
      padding: 0 0.3rem;
      border: 1px solid var(--sheen);
      border-radius: 2px;
      color: var(--sheen);
      font-family: var(--font-mono);
      font-size: 0.62rem;
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }
    tr.due { background: color-mix(in srgb, var(--sheen) 8%, transparent); }
    tr.due th[scope='row'] { box-shadow: inset 2px 0 0 var(--sheen); }
    tr.starved td, tr.starved th { color: var(--text-faint); }
    .actions { display: flex; gap: 0.35rem; justify-content: flex-end; }
    .drawer td { background: var(--ink-06); }
    .top-up { display: flex; flex-wrap: wrap; align-items: end; gap: 0.85rem; }
    .top-up label { display: grid; gap: 0.3rem; }
    .hint { margin: 0; color: var(--text-faint); font-size: 0.76rem; }
    .legend { margin: 0; color: var(--text-faint); font-size: 0.76rem; display: flex; flex-wrap: wrap; gap: 0.4rem 0.75rem; align-items: center; }
    .chip {
      font-family: var(--font-mono);
      font-size: 0.66rem;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      padding: 0.05rem 0.4rem;
      border-radius: 2px;
    }
    .chip.due { background: color-mix(in srgb, var(--sheen) 18%, transparent); color: var(--sheen-strong); }
    .chip.scheduled { border: 1px solid var(--hairline); }
    .chip.starved { border: 1px solid var(--hairline); color: var(--text-faint); }
  `,
})
export class RegistryTable {
  protected readonly archon = inject(ArchonService);
  protected readonly keeper = inject(KeeperService);
  private readonly wallet = inject(WalletService);

  protected readonly expanded = signal<bigint | null>(null);

  protected readonly rows = computed<Row[]>(() => {
    const round = this.archon.round();
    const pace = this.archon.secondsPerRound();
    const signedInAs = this.wallet.activeAddress();
    const canSign = this.wallet.connected();
    return this.archon.upkeeps().map((upkeep) => {
      const executable = isExecutable(upkeep, round);
      const fee = effectiveFee(upkeep, round);
      // Against the escalated fee: an upkeep can starve at a balance its
      // creator counted as several runs.
      const starved = upkeep.balance < fee;
      const yours = signedInAs === upkeep.creator;
      return {
        upkeep,
        id: String(upkeep.id),
        target: String(upkeep.targetApp),
        selector: `0x${toHex(upkeep.callData)}`,
        creator: shortAddress(upkeep.creator),
        yours,
        interval: intervalLabel(upkeep.intervalRounds, pace),
        nextRound: String(upkeep.nextExecutionRound),
        due: dueLabel(roundsUntilDue(upkeep, round), pace),
        fee: algos(upkeep.feePerExecution),
        feeExact: microAlgos(fee),
        feeNow: fee > upkeep.feePerExecution ? algos(fee) : null,
        policy: upkeep.policy === SKIP_AHEAD ? 'skips ahead' : 'catches up',
        ceiling: escalates(upkeep) ? algos(upkeep.feeCap) : null,
        lastRan:
          upkeep.timesExecuted > 0n ? `round ${upkeep.lastServicedRound}` : 'never run',
        balance: algos(upkeep.balance),
        runway: runwayLabel(executionsRemaining(upkeep), upkeep.intervalRounds, pace),
        executed: String(upkeep.timesExecuted),
        state: starved ? 'starved' : executable ? 'due' : 'scheduled',
        canExecute: executable && canSign,
        canCancel: canSign && yours,
        canFund: canSign,
      };
    });
  });

  protected readonly summary = computed(() => {
    const rows = this.rows();
    const due = rows.filter((row) => row.state === 'due').length;
    return `${rows.length} registered · ${due} due`;
  });

  protected toggle(row: Row): void {
    this.expanded.update((current) => (current === row.upkeep.id ? null : row.upkeep.id));
  }

  /** Three more runs is the friendliest default top-up. */
  protected defaultTopUp(row: Row): string {
    return (Number(row.upkeep.feePerExecution * 3n) / 1e6).toString();
  }

  protected execute(row: Row): void {
    void this.keeper.execute(row.upkeep);
  }

  protected cancel(row: Row): void {
    void this.keeper.cancel(row.upkeep);
  }

  protected topUp(event: Event, row: Row): void {
    event.preventDefault();
    const form = event.target as HTMLFormElement;
    const algo = Number(new FormData(form).get('amount'));
    const microAlgo = Math.round(algo * 1e6);
    if (Number.isFinite(microAlgo) && microAlgo > 0) {
      void this.keeper.topUp(row.upkeep, microAlgo);
      this.expanded.set(null);
    }
  }
}
