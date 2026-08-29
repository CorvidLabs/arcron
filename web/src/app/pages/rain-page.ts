import {
  ChangeDetectionStrategy,
  Component,
  computed,
  DestroyRef,
  inject,
  signal,
} from '@angular/core';
import { RouterLink } from '@angular/router';

import { ExplorerLink } from '../components/explorer-link';
import { RainService } from '../core/rain.service';
import { ArcronService } from '../core/arcron.service';
import { dueLabel, rounds, roundsAsTime } from '@corvidlabs/arcron/format';
import {
  modeLabel,
  rainStanding,
  roundsUntilRain,
  type RainRec,
  type RainStanding,
} from '@corvidlabs/arcron/rain';
import type { YouStatus } from '../core/rain.service';

interface Row {
  readonly rain: RainRec;
  readonly id: string;
  readonly name: string;
  readonly who: string;
  readonly cadence: string;
  readonly cadenceDetail: string | null;
  readonly drip: string;
  readonly pot: string;
  readonly prizeName: string | null;
  readonly prizeId: string | null;
  readonly inCount: string;
  readonly next: string;
  readonly state: RainStanding;
  readonly gate: string;
  readonly gateName: string;
  readonly gateId: string | null;
  readonly you: string;
  readonly youKind: YouStatus;
  readonly thumb: string | null;
}

/**
 * The hub, in the same chrome as the registry: a table of rains and a
 * primary action that leaves the list. Opening one is `/rain/new`, the way
 * registering an upkeep is `/register`. Arcron fires `draw`; this page lists
 * and links into `/rain/:id`.
 */
@Component({
  selector: 'arcron-rain-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ExplorerLink, RouterLink],
  template: `
    @if (!rain.available()) {
      <section class="panel">
        <header>
          <h2>Rain lives on TestNet</h2>
          <p class="subtitle">Switch the console to TestNet to see the hub.</p>
        </header>
        <p><a routerLink="/">Back to the registry</a></p>
      </section>
    } @else {
      <div class="head">
        <p class="lede">Anyone can open a pot on this hub.</p>
        <a class="primary" routerLink="/rain/new">Open a rain</a>
      </div>

      <section class="panel">
        <header class="spread">
          <div>
            <h2>Rains</h2>
            <p class="subtitle">
              One box per rain, read from the hub
              @if (rain.deployment(); as deployment) {
                <arcron-explorer-link kind="app" [value]="deployment.appId.toString()" />.
              }
              Upkeep
              <a routerLink="/u/{{ rain.deployment()?.upkeepId }}">{{ rain.deployment()?.upkeepId }}</a>
              fires it. A Corvid NFT rain checks a TestNet Nevermore token, not the CORVID ASA.
            </p>
          </div>
          <p class="eyebrow">{{ summary() }}</p>
        </header>

        @if (rain.status() !== 'ready' && rows().length === 0) {
          <p class="empty">Reading the hub…</p>
        } @else if (rows().length === 0) {
          <p class="empty">
            No rains on this hub yet.
            <a routerLink="/rain/new">Open one</a> to start a drip.
          </p>
        } @else {
          <div class="scroll">
            <table>
              <caption class="sr-only">Open rains</caption>
              <thead>
                <tr>
                  <th scope="col">Rain</th>
                  <th scope="col">Pays</th>
                  <th scope="col">Who</th>
                  <th scope="col">Cadence</th>
                  <th scope="col">Next</th>
                </tr>
              </thead>
              <tbody>
                @for (row of rows(); track row.rain.id) {
                  <tr [class]="row.state">
                    <th scope="row">
                      <a class="row-link" [routerLink]="['/rain', row.id]" [attr.aria-label]="row.name"></a>
                      <span class="identity">
                        @if (row.gate !== 'Open') {
                          @if (row.thumb; as src) {
                            <img
                              class="thumb"
                              [src]="src"
                              [alt]="row.gateName"
                              (error)="markBroken(src)"
                            />
                          } @else {
                            <span class="thumb empty" aria-hidden="true"></span>
                          }
                        }
                        <span class="copy">
                          <span class="title">{{ row.name }}</span>
                          <span class="meta">
                            <span class="mono">#{{ row.id }}</span>
                            <span class="dot" aria-hidden="true">·</span>
                            <span class="mono">{{ row.inCount }} in</span>
                            <span class="dot" aria-hidden="true">·</span>
                            <span [class]="'you ' + row.youKind">{{ row.you }}</span>
                          </span>
                          <span class="gate">
                            {{ row.gate }}
                            @if (row.gateId) {
                              · ASA <span class="mono">{{ row.gateId }}</span>
                            }
                          </span>
                        </span>
                      </span>
                    </th>
                    <td data-label="Pays">
                      <span class="mono">{{ row.drip }}</span>
                      <span class="sub">each fire</span>
                      <span class="mono pot">{{ row.pot }}</span>
                      <span class="sub">in the pot</span>
                      @if (row.prizeId) {
                        <span class="sub prize">
                          {{ row.prizeName ?? 'ASA' }} · <span class="mono">{{ row.prizeId }}</span>
                        </span>
                      }
                    </td>
                    <td data-label="Who">{{ row.who }}</td>
                    <td data-label="Cadence">
                      <span class="mono">{{ row.cadence }}</span>
                      @if (row.cadenceDetail) {
                        <span class="sub">{{ row.cadenceDetail }}</span>
                      }
                    </td>
                    <td data-label="Next">
                      <span class="chip" [class]="'chip ' + row.state">{{ row.state }}</span>
                      <span class="sub" [class.now]="row.state === 'due'">{{ row.next }}</span>
                    </td>
                  </tr>
                }
              </tbody>
            </table>
          </div>
          <p class="legend">
            <span class="chip due">due</span> a fire can run
            <span class="chip scheduled">scheduled</span> waiting for its round
            <span class="chip waiting">waiting</span> no tickets or an empty pot
          </p>
          <p class="note">
            Entering always takes a little ALGO for the ticket box, even when the pot is an ASA.
            To claim an ASA rain, the connected account has to opt in to that asset first — open
            the rain for the id and the opt-in.
          </p>
        }
      </section>
    }
  `,
  styles: `
    :host { display: grid; gap: 1.75rem; align-content: start; }
    .head {
      display: flex;
      flex-wrap: wrap;
      gap: 0.6rem 1rem;
      align-items: center;
      justify-content: space-between;
    }
    .lede { margin: 0; color: var(--text-faint); }
    .panel { display: grid; gap: 1.1rem; }
    header h2 { margin: 0; font-size: 1.1rem; }
    .spread { display: flex; flex-wrap: wrap; gap: 0.5rem 1.5rem; align-items: baseline; justify-content: space-between; }
    .subtitle { margin: 0.2rem 0 0; color: var(--text-faint); font-size: 0.85rem; max-width: 62ch; }
    .empty {
      margin: 0;
      padding: 1.75rem;
      border: 1px dashed var(--hairline);
      border-radius: 3px;
      color: var(--text-faint);
      text-align: center;
    }
    .scroll { overflow-x: auto; border: 1px solid var(--hairline); border-radius: 3px; }
    table { width: 100%; font-size: 0.88rem; }
    /* A table-row is not a containing block for an absolutely positioned
       overlay, so the last rain ate every click. Rows are a grid so each
       overlay stays inside its rain. */
    table, thead, tbody { display: block; }
    thead tr,
    tbody tr {
      display: grid;
      grid-template-columns: minmax(17rem, 2.4fr) minmax(8.5rem, 1.15fr) minmax(6.5rem, 0.8fr) minmax(7rem, 0.85fr) minmax(7.5rem, 0.9fr);
      width: 100%;
    }
    th, td { padding: 0.95rem 1rem; text-align: left; border-bottom: 1px solid var(--hairline); vertical-align: top; min-width: 0; }
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
    tbody tr { position: relative; isolation: isolate; }
    tbody tr:hover { background: color-mix(in srgb, var(--sheen) 6%, transparent); }
    tbody th { font-weight: 500; }
    a.row-link {
      position: absolute;
      inset: 0;
      z-index: 1;
      color: inherit;
      text-decoration: none;
    }
    .identity {
      display: flex;
      align-items: center;
      gap: 0.85rem;
      min-width: 0;
    }
    .thumb {
      width: 3.5rem;
      height: 3.5rem;
      border-radius: 3px;
      object-fit: cover;
      background: var(--ink-06);
      border: 1px solid var(--hairline);
      flex: 0 0 auto;
      image-rendering: pixelated;
    }
    .thumb.empty { display: block; }
    .copy { display: grid; gap: 0.18rem; min-width: 0; }
    .title { font-weight: 700; font-size: 1rem; letter-spacing: -0.015em; }
    .meta {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 0.3rem 0.35rem;
      color: var(--text-faint);
      font-size: 0.76rem;
    }
    .dot { color: var(--text-faint); }
    .you.yes, .you.in { color: var(--sheen); }
    .you.no { color: var(--text-faint); }
    .gate { color: var(--text-faint); font-size: 0.76rem; }
    .sub { display: block; color: var(--text-faint); font-size: 0.76rem; }
    .sub.now { color: var(--sheen); font-weight: 500; }
    .pot { display: block; margin-top: 0.35rem; }
    .prize { margin-top: 0.35rem; }
    tr.due { background: color-mix(in srgb, var(--sheen) 8%, transparent); }
    tr.due th[scope='row'] { box-shadow: inset 3px 0 0 var(--success); }
    tr.waiting th[scope='row'] { box-shadow: inset 3px 0 0 var(--hairline); }
    .legend { margin: 0; color: var(--text-faint); font-size: 0.76rem; display: flex; flex-wrap: wrap; gap: 0.4rem 0.75rem; align-items: center; }
    .note { margin: 0; color: var(--text-faint); font-size: 0.78rem; max-width: 72ch; }
    .chip {
      display: inline-block;
      font-family: var(--font-mono);
      font-size: 0.66rem;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      padding: 0.18rem 0.5rem;
      border-radius: 2px;
      white-space: nowrap;
    }
    .chip.due { background: color-mix(in srgb, var(--sheen) 18%, transparent); color: var(--sheen-strong); }
    .chip.scheduled { border: 1px solid var(--hairline); }
    .chip.waiting { border: 1px solid var(--hairline); color: var(--text-faint); }

    @media (max-width: 1219px) {
      table, thead, tbody, th, td { display: block; }
      /* The rule above is two type selectors, and a media query adds no
         specificity of its own, so a bare tr here loses to it and the row
         stays a grid whose five tracks need ~744px. Match it to beat it. */
      thead tr, tbody tr { display: block; }
      thead { display: none; }
      tbody {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(min(320px, 100%), 1fr));
        gap: 0.85rem;
        align-items: start;
      }
      tbody tr {
        border: 1px solid var(--hairline);
        border-left: 3px solid transparent;
        border-radius: 4px;
        padding: 0.85rem;
        background: var(--surface);
      }
      tbody tr.due { border-left-color: var(--success); }
      tbody tr.waiting { border-left-color: var(--hairline); }
      tbody th[scope='row'] { box-shadow: none; padding: 0 0 0.7rem; }
      tbody td {
        display: grid;
        grid-template-columns: 6.5rem minmax(0, 1fr);
        column-gap: 0.75rem;
        border: 0;
        padding: 0.35rem 0;
      }
      tbody td[data-label]::before {
        content: attr(data-label);
        color: var(--text-faint);
        text-transform: uppercase;
        letter-spacing: 0.06em;
        font-size: 0.72rem;
        white-space: nowrap;
      }
      .scroll { border: 0; }
    }
  `,
})
export class RainPage {
  protected readonly rain = inject(RainService);
  private readonly arcron = inject(ArcronService);
  private readonly broken = signal<ReadonlySet<string>>(new Set());

  constructor() {
    const stop = this.rain.watch();
    inject(DestroyRef).onDestroy(stop);
  }

  protected markBroken(src: string): void {
    this.broken.update((current) => new Set([...current, src]));
  }

  protected readonly rows = computed(() => {
    const round = this.arcron.round();
    const pace = this.arcron.secondsPerRound();
    return this.rain.rains().map((item) => this.toRow(item, round, pace));
  });

  protected summary(): string {
    const count = this.rain.rains().length;
    if (this.rain.status() !== 'ready') return 'reading';
    return `${count} rain${count === 1 ? '' : 's'}`;
  }

  private toRow(rain: RainRec, round: bigint, pace: number | null): Row {
    const state = rainStanding(rain, round);
    const until = roundsUntilRain(rain, round);
    const inCount =
      rain.mode.toString() === '2'
        ? `${rain.waveCount.toString()} / ${rain.waveCap.toString()}`
        : rain.tickets.toString();
    const time = roundsAsTime(rain.intervalRounds, pace);
    const waiting = state === 'waiting' ? this.rain.waitingHint(rain) : null;
    return {
      rain,
      id: rain.id.toString(),
      name: rain.label || `Rain #${rain.id.toString()}`,
      who: modeLabel(rain.mode),
      cadence: time === null ? rounds(rain.intervalRounds) : `~${time}`,
      cadenceDetail: time === null ? null : rounds(rain.intervalRounds),
      drip: this.rain.prizeText(rain.drip, rain),
      pot: this.rain.prizeText(rain.pot, rain),
      prizeName: this.rain.prizeName(rain),
      prizeId: this.rain.prizeId(rain),
      inCount,
      next: waiting ?? dueLabel(until, pace),
      state,
      gate: this.rain.gateLabel(rain),
      gateName: this.rain.gateName(rain),
      gateId: this.rain.gateAssetId(rain),
      you: this.rain.youLabel(rain),
      youKind: this.rain.youStatus(rain),
      thumb: this.thumbFor(rain),
    };
  }

  private thumbFor(rain: RainRec): string | null {
    const src = this.rain.thumbnail(rain);
    if (src === null) return null;
    return this.broken().has(src) ? null : src;
  }
}
