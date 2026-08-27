import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { RouterLink } from '@angular/router';

import { ArcronService } from '../core/arcron.service';
import { algos, dueLabel, intervalLabel, runwayLabel, shortAddress } from '@corvidlabs/arcron/format';
import { ExplorerLink } from './explorer-link';
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
} from '@corvidlabs/arcron';

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
}

@Component({
  selector: 'arcron-registry-table',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ExplorerLink, RouterLink],
  template: `
    <section class="panel">
      <header>
        <div>
          <h2>Upkeep registry</h2>
          <p class="subtitle">
            One box per upkeep, read straight from algod. No wallet or indexer needed.
          </p>
        </div>
        <p class="eyebrow">{{ summary() }}</p>
      </header>

      @if (arcron.appId() === null) {
        <p class="empty">Enter a keeper app id to load its registry.</p>
      } @else if (rows().length === 0) {
        <!-- Not knowing yet and knowing there is nothing are different states,
             and this used to collapse them: "no upkeeps yet" rendered for a
             second on every load, in the confident voice reserved for a fact
             the console had checked. It is a claim about the chain, and
             before the first read returns it is not one we can make. -->
        @if (arcron.status() === 'ready') {
          <p class="empty">
            No upkeeps on app {{ arcron.appId() }} yet.
            <a routerLink="/register">Register one</a> to watch the network work.
          </p>
        } @else {
          <p class="empty">Reading app {{ arcron.appId() }}…</p>
        }
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
                    <!-- The row's one link, on its identity rather than in a
                         wider actions column: everything else about this
                         upkeep lives on its own page now. -->
                    <a [routerLink]="['/u', row.id]">{{ row.id }}</a>
                    @if (row.yours) {
                      <span class="yours" title="Registered by the connected account">yours</span>
                    }
                  </th>
                  <td>
                    <span class="mono">app <arcron-explorer-link kind="app" [value]="row.target" /></span>
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
                      [disabled]="!row.canExecute || keeper.busy() !== null || !reads()"
                      (click)="execute(row)"
                    >
                      Execute
                    </button>
                    <!-- Funding and cancelling live on the upkeep's own page.
                         Topping up a stranger's upkeep is an irreversible gift
                         to whoever registered it, and the only mention of who
                         that was used to be a short address inside a drawer
                         you had to open first. -->
                  </td>
                </tr>
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
    /* The position is not decoration. Without it this box is not the
       containing block for anything absolutely positioned inside it, and
       .sr-only is absolutely positioned: the "Actions" column label and the
       table caption escaped the scroller, landed in the viewport's coordinate
       space at the far edge of a 1,031px table, and left the whole page
       scrolling 600px sideways into blank paper at 390 wide. Scrolling the
       table rather than the page is the entire point of this element. */
    .scroll { position: relative; overflow-x: auto; border: 1px solid var(--hairline); border-radius: 3px; }
    table { width: 100%; border-collapse: collapse; font-size: 0.88rem; }

    /* The upkeep id is the only way into an upkeep from the registry, and it
       drew an 8.45x16px tap target — the worst in the console by a wide margin.
       A global rule in styles.css cannot reach here, because Angular scopes
       component styles and they win on specificity, so it lives with the table
       it belongs to. The whole cell becomes the target rather than the digits. */
    @media (max-width: 480px) {
      /* The id lives in a th scope=row, the row's identity rather than one of
         its values. A first attempt matched only td a and changed nothing,
         which the audit reported as the problem still happening at the same
         8.45px rather than as a fix. */
      th a[href],
      td a[href] {
        display: inline-block;
        box-sizing: content-box;
        min-width: 30px;
        /* Padding rather than min-height: an inline-block anchor in a table
           cell takes its height from line-box rules, and min-height on its own
           left the measured box at 8.45x16. 14px of vertical padding either
           side of a 16px line box is 44. */
        padding: 14px 7px;
        text-align: center;
      }
    }
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
    /* Right-aligned, not a flex container. A td set to display:flex is not a
       table cell any more: the table wraps it in an anonymous cell, this one
       came out 11px shorter than its row, and the row's due highlight and
       bottom border stopped at the column before it. There is one control in
       here, so text-align does the whole job. */
    .actions { text-align: right; white-space: nowrap; }
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
  protected readonly arcron = inject(ArcronService);

  /**
   * Whether the page is showing the current state of the app.
   *
   * Every one of these buttons commits money against figures read from the
   * chain. A failed read leaves the last-good figures on screen and the
   * trust warnings unrendered, which is when they should be least available
   * rather than most. The register form gained this guard and these did not,
   * which is the same omission one level down.
   */
  protected readonly reads = computed(() => this.arcron.canWrite());
  protected readonly keeper = inject(KeeperService);
  private readonly wallet = inject(WalletService);

  protected readonly rows = computed<Row[]>(() => {
    const round = this.arcron.round();
    const pace = this.arcron.secondsPerRound();
    const signedInAs = this.wallet.activeAddress();
    const canSign = this.wallet.connected();
    return this.arcron.upkeeps().map((upkeep) => {
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
        selector: `0x${toHex(upkeep.callArgs[0] ?? new Uint8Array())}`,
        creator: shortAddress(upkeep.creator),
        yours,
        interval: intervalLabel(upkeep.intervalRounds, pace),
        nextRound: String(upkeep.nextExecutionRound),
        due: dueLabel(roundsUntilDue(upkeep, round), pace),
        fee: algos(upkeep.feePerExecution),
        // The tooltip has to describe the number it is attached to: the cell
        // shows the escalated fee when there is one, and the base fee when not.
        feeExact: algos(fee > upkeep.feePerExecution ? fee : upkeep.feePerExecution),
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
      };
    });
  });

  protected readonly summary = computed(() => {
    const rows = this.rows();
    const due = rows.filter((row) => row.state === 'due').length;
    return `${rows.length} registered · ${due} due`;
  });

  protected execute(row: Row): void {
    void this.keeper.execute(row.upkeep);
  }
}
