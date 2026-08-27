import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { toSignal } from '@angular/core/rxjs-interop';
import { map } from 'rxjs';

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
                    <!-- The legend below defines due, scheduled and starved.
                         Until this chip existed it was a key to a code that
                         appeared nowhere on the page. -->
                    <span class="chip" [class]="'chip ' + row.state">{{ row.state }}</span>
                    @if (row.yours) {
                      <span class="yours" title="Registered by the connected account">yours</span>
                    }
                  </th>
                  <td data-label="Target">
                    <span class="mono">app <arcron-explorer-link kind="app" [value]="row.target" /></span>
                    <span class="sub mono">{{ row.selector }}</span>
                  </td>
                  <td data-label="Cadence">
                    <span class="mono">{{ row.interval }}</span>
                  </td>
                  <td data-label="Next run">
                    <span class="mono">{{ row.nextRound }}</span>
                    <span class="sub" [class.now]="row.state === 'due'">{{ row.due }}</span>
                  </td>
                  <td class="mono" data-label="Fee" [title]="row.feeExact">
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
                  <td class="mono" data-label="Escrow">{{ row.balance }}</td>
                  <td data-label="Runway">
                    <span class="sub">{{ row.runway }}</span>
                  </td>
                  <td class="mono" data-label="Runs">{{ row.executed }}</td>
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
    /* Cards until the table actually fits, which is 1074px, not 480px.
       Measured across the range: at 481px the layout flipped back to a table
       that needed 1074px and scrolled sideways inside its wrapper, so every
       tablet, every small laptop and every phone in landscape still had the
       Execute button off the right edge. The rendered-page audit drives 768px
       and passed it, because the content was inside a legitimate scroller and
       nothing asks whether a scroller's contents can be reached.

       1260px is measured, and re-measured. The first measurement gave 1220
       against a table needing 1074. Then a state chip was added to every row,
       the table grew to 1127, and 1220 quietly became too small: at 1226 the
       table needed 1127 and had 1097, so the Execute column sat 30px off the
       right edge behind a scrollbar. A screenshot found it.

       The lesson is in the failure, not the number. Any column added to this
       table moves this breakpoint, and nothing computes it: it is measured by
       stepping the viewport until .scroll stops overflowing. Re-measure after
       touching the columns. Below that the cards
       tile: one column on a phone, more as there is width for them, so a tablet
       gets a readable grid rather than a single 700px-wide card. */
    @media (max-width: 1259px) {
      /* A nine-column table is not a table on a 390px screen. It was a
         sideways scroller: the Execute button sat off the right edge, the
         Cadence header was clipped mid-word, and reading one upkeep meant
         scrolling right and then back. Every rendered-page check passed on
         that, because the scroller is a legitimate scroller and nothing in the
         audit asks whether the content inside it can be reached.

         So each row becomes a card. The head is hidden, every cell names itself
         from its data-label, and Execute is a full-width button at the foot
         where a thumb is. */
      table,
      thead,
      tbody,
      tr,
      th,
      td {
        display: block;
      }

      /* Gone, not clipped.

         This was .sr-only on the element, with a rule outside this block
         cancelling it for desktop. That rule had no media guard, so it cancelled
         .sr-only at every width: the head rendered as nine full-width grey
         bands, 371px of "# TARGET CADENCE NEXT RUN FEE ESCROW RUNWAY RUNS"
         stacked above the first card on an 844px screen.

         The audit did not catch it because it skips anything inside .sr-only by
         design. So the class was acting as an exemption marker while the CSS
         made the exemption false, which is the precise failure the rendered-page
         rule exists to prevent. display:none here needs no exemption and no
         second rule to undo it.

         display:none does drop the head from the accessibility tree, and
         changing display away from table already drops the table role in every
         engine, so the explicit roles on the markup carry the semantics at this
         width instead. */
      thead {
        display: none;
      }

      /* One column on a phone, then as many as fit. minmax(0, 1fr) rather than
         auto-fill with a fixed track, so a card never forces the row wider than
         the viewport. */
      /* stretch, not start. Cards size to their content, and content differs by
         a line or two: a fee with a ceiling adds "up to 0.012 ALGO", an overdue
         upkeep adds "overdue by ~44 min", a long cadence wraps. So a row of
         cards came out ragged with the Execute buttons at three different
         heights, which reads as a layout fault rather than as data. */
      tbody {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(min(320px, 100%), 1fr));
        gap: 0.85rem;
        align-items: stretch;
      }

      /* The stripe is always 3px and only its colour changes. Giving it only to
         due and starved left scheduled cards 2px narrower with their content 2px
         to the left, which reads as jitter down a list.

         --surface, not --paper: paper is the page ground, and a card sitting on
         a --surface panel painted in the page ground is inverted elevation. The
         keeper board's job cards already use --surface. */
      tbody tr {
        display: flex;
        flex-direction: column;
        border: 1px solid var(--hairline);
        border-left: 3px solid transparent;
        border-radius: 4px;
        padding: 0.75rem;
        background: var(--surface);
      }

      tbody tr.due {
        border-left-color: var(--success);
      }

      tbody tr.starved {
        border-left-color: var(--warning);
      }

      /* The title bar is a table affordance. On a card the 3px edge already says
         it, and both together is two stripes on one card — visible on a starved
         card as an amber edge and an amber bar 12px apart. */
      tbody tr.due th[scope='row'],
      tbody tr.starved th[scope='row'] {
        box-shadow: none;
      }

      /* The chip carries the state name, so the card edge is reinforcement
         rather than the only signal. */
      tbody th[scope='row'] .chip {
        margin-left: auto;
      }

      /* A 44px button in a cell measured 61px: padding plus the phantom
         descender under an inline-block. flex removes the descender. */
      /* The id is the card's title. */
      tbody th[scope='row'] {
        display: flex;
        align-items: center;
        gap: 0.5rem;
        font-size: 1.05rem;
        border: 0;
        padding: 0 0 0.5rem;
      }

      tbody th[scope='row']::before {
        content: '#';
        color: var(--text-faint);
      }

      /* Grid rather than flex, with both tracks allowed to shrink to zero. A
         flex row let the value keep its intrinsic width and run off the card:
         the cadence read "25 rounds - ~1 min 10" with the rest past the edge,
         and the runs column lost its digits. minmax(0, ...) is what lets a
         track go narrower than its content so the value can wrap instead. */
      /* One shared label track, and values left-aligned.

         The label track was minmax(0, auto), so every cell sized its own label
         independently and nothing lined up down the card: Fee's label track
         measured 25.67px against Next run's 70.06px. The values were
         right-aligned, which pays off when values form a column and card mode is
         exactly what destroys that column, so a value too long for its track
         wrapped and right-aligned its own orphan. And gap is a shorthand, so it
         set row-gap too: 13.5px between a value and its own second line, which
         made multi-line values look detached from themselves. */
      tbody td {
        display: grid;
        grid-template-columns: 6.5rem minmax(0, 1fr);
        column-gap: 0.75rem;
        row-gap: 0;
        align-items: baseline;
        border: 0;
        padding: 0.35rem 0;
        text-align: left;
      }

      /* Guarded on the attribute: the actions cell has none, and attr() on a
         missing attribute yields an empty string rather than nothing, so an
         unguarded rule generates an empty inline box inheriting the label's
         type. */
      tbody td[data-label]::before {
        content: attr(data-label);
        color: var(--text-faint);
        text-transform: uppercase;
        letter-spacing: 0.06em;
        font-size: 0.72rem;
        white-space: nowrap;
      }

      /* Values that carry a second line, like the next run's "overdue by", put
         it under the number rather than beside the label. */
      tbody td > .sub {
        grid-column: 2;
        display: block;
      }

      /* Target needs no special case now. It had one: flex-direction on a
         grid container, which does nothing at all, and align-items:flex-start,
         which opted this single cell out of the baseline alignment every
         other cell uses and was half the reason the card looked crooked. */

      /* Execute is the reason somebody opened this on a phone. Full width, at
         the foot of the card, above the thumb rather than off the right edge.

         margin-top:auto rather than a fixed gap, so it takes whatever slack the
         stretched card has and every Execute in a row lands on the same line
         however much data the card above it carried. There were two rules on
         this selector for a while and the fixed 0.5rem in the second one won,
         which is why the buttons stayed ragged after the cards were levelled:
         the computed margin-top read 8px, not auto.

         flex rather than block, because an inline-block button leaves a
         phantom descender under it and the spacing stops being the spacing
         written here. */
      tbody td.actions {
        display: flex;
        padding-top: 0.6rem;
        margin-top: auto;
        border-top: 1px solid var(--hairline);
      }

      tbody td.actions button {
        width: 100%;
        min-height: 44px;
      }

      /* The wrapper has nothing left to scroll, but it keeps overflow-x:auto as
         a backstop: a card that somehow exceeds the viewport should scroll
         inside this box rather than widen the document, which is what happened
         when this said visible. */
      .scroll {
        border: 0;
        max-width: 100%;
      }

      table {
        width: 100%;
        max-width: 100%;
        table-layout: auto;
      }

      tbody tr,
      tbody td,
      tbody th {
        max-width: 100%;
        overflow-wrap: anywhere;
      }

      /* Values wrap rather than truncate. The table sets nowrap on its mono
         cells so columns stay aligned, which is right for a table and wrong for
         a card: the cadence read "25 rounds - ~1 min 10" with the trailing "s"
         clipped inside the cell. The card had not overflowed, so a check for
         cells escaping their card saw nothing; only comparing scrollWidth with
         clientWidth found it. */
      tbody td,
      tbody td span,
      tbody th,
      tbody th span {
        white-space: normal;
      }

      /* No anchor rule here.

         There was one, and it did not do what it looked like. Angular compiles
         a component selector to td[_ngcontent-x] a[href][_ngcontent-x], and the
         explorer link is rendered by another component, so it carries a
         different _ngcontent and this rule could never reach it. On the id link
         it fired on top of the global rule instead, giving a two-digit link a
         44x72 box. The global rule in styles.css handles both. */
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
    /* Not faint. A starved upkeep cannot pay its own fee, which is the one
       state a creator has to act on, and greying it out is the convention for
       "inactive, ignore me". It was the faintest thing on the page. The row
       keeps ordinary text and carries a warning edge instead; the chip in the
       id cell says which state it is. */
    tr.starved th[scope='row'] { box-shadow: inset 3px 0 0 var(--warning); }
    tr.due th[scope='row'] { box-shadow: inset 3px 0 0 var(--success); }
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
    .chip.scheduled { border: 1px solid var(--hairline); color: var(--text-faint); }
    /* Starved was styled fainter than scheduled, so the two were near enough
       identical and the one that reads as inert was the one needing a person.
       An upkeep below one fee cannot run until its creator tops it up. */
    .chip.starved {
      border: 1px solid var(--warning);
      color: var(--warning);
      background: color-mix(in srgb, var(--warning) 12%, transparent);
    }
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

  /**
   * Whether the drawer's "Your upkeeps" link is filtering the table.
   *
   * A query parameter rather than component state, so the filtered view has an
   * address somebody can bookmark or send, and so leaving the page and coming
   * back does not silently drop the filter.
   */
  protected readonly mineOnly = toSignal(
    inject(ActivatedRoute).queryParamMap.pipe(map((params) => params.get('mine') === '1')),
    { initialValue: false },
  );

  /**
   * What order the registry is in.
   *
   * There was none. `rows()` mapped `upkeeps()` straight through, and that is
   * box-read order, which is ascending id, which is registration order. So a
   * starved upkeep needing its creator sat wherever it happened to be
   * registered, and the one thing a reader can act on was as likely to be last
   * as first.
   *
   * Due first, because anyone can run those now. Then starved, because they
   * need a person and hiding the network's failures helps nobody. Then
   * scheduled. Ties keep ascending id, so the order is stable between polls
   * rather than shuffling as rounds advance.
   */
  private static readonly STATE_ORDER: Record<string, number> = {
    due: 0,
    starved: 1,
    scheduled: 2,
  };

  protected readonly rows = computed<Row[]>(() => {
    const round = this.arcron.round();
    const pace = this.arcron.secondsPerRound();
    const signedInAs = this.wallet.activeAddress();
    const canSign = this.wallet.connected();
    const mineOnly = this.mineOnly();
    return this.arcron
      .upkeeps()
      .filter((upkeep) => !mineOnly || upkeep.creator === signedInAs)
      .map((upkeep) => {
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
        state: (starved ? 'starved' : executable ? 'due' : 'scheduled') as Row['state'],
        canExecute: executable && canSign,
      };
    })
      .sort((left, right) => {
        const byState =
          RegistryTable.STATE_ORDER[left.state] - RegistryTable.STATE_ORDER[right.state];
        // Ascending id breaks ties, so the order is stable between polls
        // rather than shuffling as rounds advance.
        return byState !== 0 ? byState : Number(left.upkeep.id - right.upkeep.id);
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
