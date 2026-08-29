import {
  ChangeDetectionStrategy,
  Component,
  computed,
  DestroyRef,
  inject,
} from '@angular/core';
import { RouterLink } from '@angular/router';

import { ExplorerLink } from '../components/explorer-link';
import { ArcronService } from '../core/arcron.service';
import { RainService } from '../core/rain.service';
import { WalletService } from '../core/wallet.service';
import {
  algos,
  roundsAsTime,
  shortAddress,
} from '@corvidlabs/arcron/format';
import { roundsUntilDue } from '@corvidlabs/arcron/upkeep';
import { ZERO_ADDRESS, abandonOpen, resolveOpen } from '@corvidlabs/arcron/rain';

/**
 * The holder-facing Rain draw.
 *
 * This is not the keeper console. Tickets persist across draws, so the page
 * says "enter once" and never "come back tomorrow". Arcron still fires
 * `draw`; this page is enter, deposit, resolve, claim.
 */
@Component({
  selector: 'arcron-rain-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ExplorerLink, RouterLink],
  template: `
    <header class="intro">
      <p class="eyebrow">TestNet · Corvid holders</p>
      <h2>Rain</h2>
      <p class="lede">
        Hold a Corvid NFT. Enter once. You stay in every draw after that —
        daily, weekly, whatever cadence the keeper is on. You do not check in
        again. Arcron calls the contract on a schedule; a winner pulls the pot.
      </p>
    </header>

    @if (!rain.available()) {
      <section class="panel" aria-labelledby="missing-heading">
        <h3 id="missing-heading">Rain lives on TestNet</h3>
        <p>
          This draw is the TestNet dogfood, app
          <span class="mono">770029154</span>. Switch the console to TestNet
          to enter it.
        </p>
        <p>
          <a routerLink="/">Back to the keeper registry</a>
        </p>
      </section>
    } @else {
      @if (rain.status() === 'loading' && rain.state() === null) {
        <p class="quiet">Reading the draw…</p>
      }

      @if (rain.state(); as state) {
        <dl class="tiles">
          <div class="tile">
            <dt class="eyebrow">Pot</dt>
            <dd class="mono">{{ algos(state.pot) }}</dd>
            <dd class="hint">Anyone can add to it</dd>
          </div>
          <div class="tile">
            <dt class="eyebrow">Tickets</dt>
            <dd class="mono">{{ state.tickets.toString() }}</dd>
            <dd class="hint">One ticket is every future draw</dd>
          </div>
          <div class="tile" [class.live]="drawOpen()">
            <dt class="eyebrow">Draw</dt>
            <dd class="mono">{{ drawLabel() }}</dd>
            <dd class="hint">{{ cadenceHint() }}</dd>
          </div>
          <div class="tile">
            <dt class="eyebrow">Last winner</dt>
            <dd class="mono">{{ winnerLabel() }}</dd>
            <dd class="hint">{{ state.drawsResolved.toString() }} resolved</dd>
          </div>
        </dl>

        <section class="panel" aria-labelledby="you-heading">
          <h3 id="you-heading">You</h3>

          @if (!wallet.connected()) {
            <p>
              Connect a wallet that holds a TestNet Corvid NFT (unit name starting
              <span class="mono">corvid</span>, minted by
              <span class="mono">{{ short(state.gateCreator) }}</span>).
            </p>
            <p class="hint">Reads work without a wallet. Entering, depositing and claiming need a signature.</p>
          } @else if (rain.qualifying().length === 0) {
            <p>
              This account does not hold a qualifying Corvid NFT, so it cannot
              enter or claim. The gate is the collection minter, not a single
              asset id.
            </p>
          } @else if (rain.entered()) {
            <p class="yes">
              You are in. {{ rain.ticketCount() }}
              ticket{{ rain.ticketCount() === 1 ? '' : 's' }}. You stay in every
              draw; you do not enter again for a daily or weekly fire.
            </p>
            <p class="hint">
              Holding
              @for (nft of rain.qualifying(); track nft.id; let last = $last) {
                <span class="mono">{{ nft.unitName || nft.name }}</span>{{ last ? '.' : ', ' }}
              }
              Sell the NFT before a claim and a win is forfeit.
            </p>
          } @else {
            <p>
              You hold
              @for (nft of rain.qualifying(); track nft.id; let last = $last) {
                <span class="mono">{{ nft.unitName || nft.name }}</span>{{ last ? '.' : ', ' }}
              }
              Enter once. The ticket is a box that never expires.
            </p>
            <button
              type="button"
              class="primary"
              [disabled]="!rain.canEnter() || rain.busy() !== null"
              (click)="rain.enter()"
            >
              {{ rain.busy() === 'enter' ? 'Entering…' : 'Enter this draw, once' }}
            </button>
            <p class="hint">Costs {{ rain.ticketCost() }} of box minimum balance, paid to the app.</p>
          }

          @if (hasPrize()) {
            <p class="yes">This account has {{ algos(rain.allocation()) }} waiting to be claimed.</p>
            <button
              type="button"
              class="primary"
              [disabled]="!rain.canClaim()"
              (click)="rain.claim()"
            >
              {{ rain.busy() === 'claim' ? 'Claiming…' : 'Claim' }}
            </button>
            <p class="hint">You must still hold a Corvid NFT in this account to collect.</p>
          }
        </section>

        <section class="panel" aria-labelledby="pot-heading">
          <h3 id="pot-heading">Fund the pot</h3>
          <p>Anyone can deposit TestNet ALGO. The next keeper call that finds tickets and a pot opens a draw.</p>
          <form class="row" (submit)="deposit($event)">
            <label>
              <span class="eyebrow">ALGO</span>
              <input
                name="algo"
                type="number"
                min="0.1"
                step="0.1"
                value="1"
                inputmode="decimal"
                [disabled]="!wallet.connected() || rain.busy() !== null"
              />
            </label>
            <button
              type="submit"
              class="ghost"
              [disabled]="!wallet.connected() || rain.busy() !== null"
            >
              {{ rain.busy() === 'deposit' ? 'Sending…' : 'Deposit' }}
            </button>
          </form>
        </section>

        @if (state.drawOpen) {
          <section class="panel" aria-labelledby="open-heading">
            <h3 id="open-heading">A draw is open</h3>
            <p>
              Prize {{ algos(state.prize) }} · committed at round
              <span class="mono">{{ state.commitRound.toString() }}</span>
              · {{ state.ticketsSnapshot.toString() }} tickets in the snapshot.
            </p>
            @if (canResolveNow()) {
              <button type="button" class="primary" [disabled]="rain.busy() !== null" (click)="rain.resolve()">
                {{ rain.busy() === 'resolve' ? 'Asking the beacon…' : 'Resolve this draw' }}
              </button>
              <p class="hint">Permissionless. Needs the Foundation randomness beacon as a foreign app.</p>
            } @else if (canAbandonNow()) {
              <p>The beacon window has closed. Resolve will never work; abandon puts the prize back.</p>
              <button type="button" class="ghost" [disabled]="rain.busy() !== null" (click)="rain.abandon()">
                {{ rain.busy() === 'abandon' ? 'Abandoning…' : 'Abandon and return the pot' }}
              </button>
            } @else {
              <p class="hint">Waiting for the committed round to pass so the beacon can answer.</p>
            }
          </section>
        }

        <section class="panel quiet-panel" aria-labelledby="how-heading">
          <h3 id="how-heading">How this is wired</h3>
          <ol>
            <li>You enter once. The ticket lives in a box on the rain app.</li>
            <li>
              Arcron upkeep
              <a routerLink="/u/{{ rain.deployment()?.upkeepId }}">{{ rain.deployment()?.upkeepId }}</a>
              calls <span class="mono">draw()uint64</span> on a schedule. Missed runs skip ahead.
            </li>
            <li>A keeper you do not control executes that upkeep. That is the clock.</li>
            <li>Someone resolves against the beacon, then the winner claims.</li>
          </ol>
          <p>
            Rain app
            <arcron-explorer-link kind="app" [value]="state.appId.toString()" />.
            Keeper
            <a routerLink="/">console</a>.
          </p>
        </section>
      }

      @if (rain.error(); as message) {
        <p class="banner" role="alert">{{ message }}</p>
      }

      @if (rain.activity().length > 0) {
        <ol class="log">
          @for (entry of rain.activity(); track entry.txId) {
            <li>
              {{ entry.message }}
              <span class="hint">round {{ entry.round.toString() }}</span>
            </li>
          }
        </ol>
      }
    }
  `,
  styles: `
    :host { display: grid; gap: 1.5rem; align-content: start; max-width: 42rem; }
    .intro { display: grid; gap: 0.45rem; }
    h2 { margin: 0; font-size: 2rem; }
    h3 { margin: 0 0 0.4rem; font-size: 1.05rem; }
    .lede { margin: 0; color: var(--text-faint); max-width: 36rem; }
    .tiles {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(10.5rem, 1fr));
      gap: 0.7rem;
      margin: 0;
    }
    .tile {
      margin: 0;
      padding: 0.75rem 0.85rem;
      border: 1px solid var(--hairline);
      border-radius: 3px;
      background: var(--ink-06);
    }
    .tile.live { border-color: var(--sheen); }
    .tile dt { margin: 0; }
    .tile dd { margin: 0; }
    .tile .mono { font-size: 1.15rem; font-weight: 600; }
    .panel {
      display: grid;
      gap: 0.65rem;
      padding: 1rem 1.05rem;
      border: 1px solid var(--hairline);
      border-radius: 3px;
    }
    .quiet-panel { color: var(--text-faint); }
    .quiet-panel ol { margin: 0; padding-left: 1.2rem; display: grid; gap: 0.35rem; }
    .yes { margin: 0; }
    .hint { margin: 0; color: var(--text-faint); font-size: 0.85rem; }
    .quiet { margin: 0; color: var(--text-faint); }
    .row {
      display: flex;
      flex-wrap: wrap;
      gap: 0.6rem;
      align-items: end;
    }
    .row label { display: grid; gap: 0.25rem; }
    .row input { width: 8rem; }
    .banner {
      margin: 0;
      padding: 0.75rem 1rem;
      border: 1px solid var(--danger);
      border-radius: 3px;
      color: var(--danger);
      font-size: 0.88rem;
    }
    .log { margin: 0; padding-left: 1.2rem; display: grid; gap: 0.3rem; font-size: 0.9rem; }
    .primary:disabled, .ghost:disabled { opacity: 0.55; }
  `,
})
export class RainPage {
  protected readonly rain = inject(RainService);
  protected readonly wallet = inject(WalletService);
  protected readonly arcron = inject(ArcronService);

  constructor() {
    const stop = this.rain.watch();
    inject(DestroyRef).onDestroy(stop);
  }

  protected readonly algos = algos;

  protected short(address: string): string {
    return address === ZERO_ADDRESS ? 'ungated' : shortAddress(address);
  }

  protected drawOpen(): boolean {
    return this.rain.state()?.drawOpen === true;
  }

  protected drawLabel(): string {
    const state = this.rain.state();
    if (state === null) return '—';
    if (state.drawOpen) return 'Open';
    if (state.tickets === 0n || state.pot === 0n) return 'Waiting';
    return 'Armed';
  }

  protected cadenceHint(): string {
    const upkeep = this.rain.upkeep();
    const round = this.arcron.round();
    if (upkeep === null) return 'Keeper upkeep not read yet';
    const due = roundsUntilDue(upkeep, round);
    const time = roundsAsTime(due < 0n ? 0n : due, this.arcron.secondsPerRound());
    if (due <= 0n) return 'Keeper draw is due';
    return time === null ? `Next draw in ${due.toString()} rounds` : `Next draw in ${time}`;
  }

  protected winnerLabel(): string {
    const winner = this.rain.state()?.lastWinner;
    if (winner === undefined || winner === ZERO_ADDRESS) return 'None yet';
    return shortAddress(winner);
  }

  protected hasPrize(): boolean {
    return this.rain.allocation() > 0n;
  }

  protected canResolveNow(): boolean {
    const state = this.rain.state();
    if (state === null) return false;
    return resolveOpen(state, this.arcron.round());
  }

  protected canAbandonNow(): boolean {
    const state = this.rain.state();
    if (state === null) return false;
    return abandonOpen(state, this.arcron.round());
  }

  protected deposit(event: Event): void {
    event.preventDefault();
    const form = event.target as HTMLFormElement;
    const field = form.elements.namedItem('algo');
    const value = field instanceof HTMLInputElement ? Number(field.value) : 0;
    void this.rain.depositAlgo(value);
  }
}
