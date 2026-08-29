import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  effect,
  inject,
  input,
  signal,
} from '@angular/core';
import { RouterLink } from '@angular/router';

import { ExplorerLink } from '../components/explorer-link';
import { ArcronService } from '../core/arcron.service';
import { RainService } from '../core/rain.service';
import { WalletService } from '../core/wallet.service';
import { dueLabel, intervalLabel, shortAddress } from '@corvidlabs/arcron/format';
import { roundsUntilDue } from '@corvidlabs/arcron/upkeep';
import {
  ZERO_ADDRESS,
  modeHint,
  modeLabel,
  rainStanding,
  type RainRec,
} from '@corvidlabs/arcron/rain';

@Component({
  selector: 'arcron-rain-detail-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ExplorerLink, RouterLink],
  template: `
    <nav class="back" aria-label="Breadcrumb">
      <a routerLink="/rain">Back to rains</a>
    </nav>

    @if (!rain.available()) {
      <section class="panel note">
        <h2>Rain lives on TestNet</h2>
        <p class="detail"><a routerLink="/">Back to the registry</a></p>
      </section>
    } @else if (rain.status() === 'loading' && rain.current() === null) {
      <section class="panel note">
        <h2>Reading the hub…</h2>
        <p class="detail">
          Rain {{ id() }} has not been read yet. This is the first read of the hub
          returning, not an answer about whether it exists.
        </p>
      </section>
    } @else if (rain.current(); as state) {
      <article class="rain">
        <header class="panel identity">
          <div class="title">
            @if (state.gated) {
              @if (thumb(state); as src) {
                <img class="portrait" [src]="src" [alt]="nftAlt(state)" (error)="markBroken(src)" />
              } @else {
                <span class="portrait empty" aria-hidden="true"></span>
              }
            }
            <div class="names">
              <p class="eyebrow">Rain #{{ state.id.toString() }}</p>
              <h2>{{ state.label || ('Rain #' + state.id.toString()) }}</h2>
              <p class="standing" [class]="standingClass(state)">{{ standing(state) }}</p>
            </div>
          </div>
          <p class="detail">{{ modeHint(state) }}. {{ gateStory(state) }}</p>
          <p class="detail">
            Hub
            <arcron-explorer-link kind="app" [value]="(rain.deployment()?.appId ?? 0).toString()" />.
            Keeper
            <a routerLink="/">console</a>.
            Opened by
            <arcron-explorer-link kind="account" [value]="state.creator" [label]="short(state.creator)" />.
          </p>
        </header>

        <section class="panel">
          <h3>Prize</h3>
          <dl class="facts">
            <div>
              <dt class="eyebrow">Each fire</dt>
              <dd class="mono">{{ prize(state.drip, state) }}</dd>
            </div>
            <div>
              <dt class="eyebrow">In the pot</dt>
              <dd class="mono">{{ prize(state.pot, state) }}</dd>
            </div>
            <div>
              <dt class="eyebrow">Asset</dt>
              <dd>
                @if (state.prizeAsset.toString() === '0') {
                  <span class="mono">ALGO</span>
                  <span class="sub">The pot is TestNet ALGO. No opt-in.</span>
                } @else {
                  <span>{{ prizeInfo(state)?.name || 'ASA' }}</span>
                  <span class="sub mono">
                    {{ prizeInfo(state)?.unitName || 'ASA' }}
                    ·
                    <arcron-explorer-link kind="asset" [value]="state.prizeAsset.toString()" />
                  </span>
                  @if (wallet.connected()) {
                    @if (rain.isOptedIn(asAssetId(state))) {
                      <span class="sub">This account can hold it.</span>
                    } @else {
                      <button
                        type="button"
                        class="ghost small"
                        [disabled]="rain.busy() !== null"
                        (click)="rain.optIn(asAssetId(state))"
                      >
                        {{ rain.busy() === 'optin' ? 'Opting in…' : 'Opt in to ' + prizeUnit(state) }}
                      </button>
                    }
                  } @else {
                    <span class="sub">Connect to opt in to ASA {{ state.prizeAsset.toString() }} so you can claim.</span>
                  }
                }
              </dd>
            </div>
          </dl>
          <p class="detail">
            Entering still costs {{ rain.ticketCost(state.mode) }} for the ticket box,
            even when this pot is {{ prizeKind(state) }}. Claiming pulls from the pot;
            it does not spend that ticket ALGO.
          </p>
        </section>

        @if (state.gated) {
          <section class="panel">
            <h3>Who can enter</h3>
            <dl class="facts">
              <div>
                <dt class="eyebrow">Gate</dt>
                <dd>
                  {{ rain.gateLabel(state) }}
                  <span class="sub">{{ rain.gateName(state) }}</span>
                </dd>
              </div>
              <div>
                <dt class="eyebrow">Look up</dt>
                <dd>
                  @if (rain.gateAssetId(state); as assetId) {
                    <span class="mono">
                      ASA <arcron-explorer-link kind="asset" [value]="assetId" />
                    </span>
                    <span class="sub">A sample token from the collection, not the only one that qualifies.</span>
                  } @else {
                    <span class="sub">Hold any token this minter issued.</span>
                  }
                </dd>
              </div>
              <div>
                <dt class="eyebrow">Minter</dt>
                <dd class="mono">
                  <arcron-explorer-link kind="account" [value]="state.gateCreator" [label]="short(state.gateCreator)" />
                </dd>
              </div>
              <div>
                <dt class="eyebrow">This wallet</dt>
                <dd>
                  {{ rain.youLabel(state) }}
                  @if (heldName(state); as nft) {
                    <span class="sub">Holding {{ nft }}.</span>
                  }
                </dd>
              </div>
            </dl>
            <p class="detail">{{ gateStory(state) }}</p>
          </section>
        }

        <section class="panel actions">
          <h3>What anyone can do here</h3>

          @if (!wallet.connected()) {
            <p class="detail">
              Connect an account above to enter, check in, or claim.
              @if (state.gated) {
                This rain needs a {{ rain.gateLabel(state) }} in that wallet.
              }
            </p>
          } @else if (state.gated && rain.qualifying().length === 0) {
            <p class="detail">{{ gateStory(state) }}</p>
          } @else if (rain.entered()) {
            <p class="detail">You are in. You stay in every fire; you do not enter again.</p>
            @if (state.mode.toString() === '2') {
              <div class="row">
                <button
                  type="button"
                  class="primary"
                  [disabled]="!rain.canGm()"
                  (click)="rain.gm()"
                >
                  {{ rain.busy() === 'gm' ? 'Checking in…' : 'I am here' }}
                </button>
                <p class="detail">Check in for this drop. First {{ state.waveCap.toString() }} people split it.</p>
              </div>
            }
          } @else {
            <div class="row">
              <button
                type="button"
                class="primary"
                [disabled]="!rain.canEnter()"
                (click)="rain.enter()"
              >
                {{ rain.busy() === 'enter' ? 'Entering…' : 'Enter this rain' }}
              </button>
              <p class="detail">
                @if (heldName(state); as nft) {
                  Enter with {{ nft }}.
                }
                The ticket box is {{ rain.ticketCost(state.mode) }} of ALGO minimum balance
                @if (state.prizeAsset.toString() !== '0') {
                  — the pot itself is {{ prizeUnit(state) }}, not ALGO
                }.
              </p>
            </div>
          }

          @if (hasPrize()) {
            <div class="row">
              <button
                type="button"
                class="primary"
                [disabled]="!rain.canClaim()"
                (click)="rain.claim()"
              >
                {{ rain.busy() === 'claim' ? 'Claiming…' : 'Claim' }}
              </button>
              <p class="detail">{{ prize(rain.allocation(), state) }} waiting.</p>
            </div>
          }

          @if (state.mode.toString() === '1' && state.prizeLocked.toString() !== '0') {
            <div class="row">
              <button
                type="button"
                class="ghost"
                [disabled]="rain.busy() !== null || !wallet.connected()"
                (click)="rain.resolve()"
              >
                {{ rain.busy() === 'resolve' ? 'Resolving…' : 'Resolve this drop' }}
              </button>
              <p class="detail">
                Locked until round {{ state.commitRound.toString() }}. Anyone can resolve
                once that round has passed.
              </p>
            </div>
          }

          @if (rain.writeError() ?? rain.error(); as message) {
            <p class="banner" role="alert">{{ message }}</p>
          }
        </section>

        <section class="panel">
          <h3>Schedule</h3>
          <dl class="facts">
            <div>
              <dt class="eyebrow">Cadence</dt>
              <dd>{{ intervalLabel(state.intervalRounds, arcron.secondsPerRound()) }}</dd>
            </div>
            <div>
              <dt class="eyebrow">Mode</dt>
              <dd>{{ modeLabel(state.mode) }}</dd>
            </div>
            <div>
              <dt class="eyebrow">Next rain</dt>
              <dd>
                <span class="mono">{{ rainLabel() }}</span>
                <span class="sub">{{ cadenceHint() }}</span>
              </dd>
            </div>
            <div>
              <dt class="eyebrow">Fires so far</dt>
              <dd class="mono">{{ state.drawId.toString() }}</dd>
            </div>
          </dl>
          <p class="detail">
            Arcron upkeep
            <a routerLink="/u/{{ rain.deployment()?.upkeepId }}">{{ rain.deployment()?.upkeepId }}</a>
            calls <span class="mono">draw()uint64</span>. The rain itself will
            not fire until its interval has passed.
          </p>
        </section>

        <section class="panel">
          <h3>The pot</h3>
          <dl class="facts">
            <div>
              <dt class="eyebrow">Pot</dt>
              <dd class="mono">{{ prize(state.pot, state) }}</dd>
            </div>
            <div>
              <dt class="eyebrow">{{ state.mode.toString() === '2' ? 'This drop' : 'Tickets' }}</dt>
              <dd class="mono">
                @if (state.mode.toString() === '2') {
                  {{ state.waveCount.toString() }} / {{ state.waveCap.toString() }}
                } @else {
                  {{ state.tickets.toString() }}
                }
              </dd>
            </div>
            <div>
              <dt class="eyebrow">Fires left</dt>
              <dd class="mono">{{ rain.rainsLeft().toString() }}</dd>
            </div>
            @if (hasPrize()) {
              <div>
                <dt class="eyebrow">Yours</dt>
                <dd class="mono">{{ prize(rain.allocation(), state) }}</dd>
              </div>
            }
          </dl>
          <p class="detail">
            Anyone can deposit
            @if (prizeInfo(state); as info) {
              {{ info.unitName }} ({{ info.name }},
              <arcron-explorer-link kind="asset" [value]="info.id.toString()" />)
            } @else if (state.prizeAsset.toString() !== '0') {
              ASA <arcron-explorer-link kind="asset" [value]="state.prizeAsset.toString()" />
            } @else {
              ALGO
            }.
            Leftover from a fire stays for the next one.
            Entering does not take from this pot; it still costs a little ALGO for the ticket box.
          </p>

          @if (state.prizeAsset.toString() === '0') {
            <form class="row" (submit)="depositAlgo($event)">
              <label>
                <span class="eyebrow">Add ALGO</span>
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
                [disabled]="!wallet.connected() || rain.busy() !== null || !arcron.canWrite()"
              >
                {{ rain.busy() === 'deposit' ? 'Sending…' : 'Deposit' }}
              </button>
            </form>
          } @else {
            <form class="row" (submit)="depositAsset($event)">
              <label>
                <span class="eyebrow">Add {{ prizeUnit(state) }}</span>
                <input
                  name="amount"
                  type="number"
                  min="1"
                  step="1"
                  value="10000"
                  inputmode="numeric"
                  [disabled]="!wallet.connected() || rain.busy() !== null"
                />
              </label>
              <button
                type="submit"
                class="ghost"
                [disabled]="!wallet.connected() || rain.busy() !== null || !arcron.canWrite()"
              >
                {{ rain.busy() === 'deposit' ? 'Sending…' : 'Deposit' }}
              </button>
            </form>
          }
        </section>
      </article>
    } @else if (rain.status() === 'ready') {
      <section class="panel note">
        <h2>No rain {{ id() }} on this hub.</h2>
        <p class="detail">Either it was never opened, or you are looking at a different app.</p>
        <p><a routerLink="/rain">See what is open</a></p>
      </section>
    }
  `,
  styles: `
    :host { display: grid; gap: 1.25rem; align-content: start; }
    .back { font-size: 0.85rem; }
    .rain { display: grid; gap: 1.25rem; }
    h2 { margin: 0; font-size: 1.6rem; }
    h3 { margin: 0 0 0.8rem; font-size: 1rem; }
    .note h2 { font-size: 1.1rem; margin-bottom: 0.4rem; }
    .identity { display: grid; gap: 0.9rem; }
    .title { display: flex; flex-wrap: wrap; align-items: center; gap: 0.9rem 1.1rem; }
    .title .eyebrow { margin: 0; }
    .names { display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.45rem 0.85rem; }
    .portrait {
      width: 6.5rem;
      height: 6.5rem;
      border-radius: 4px;
      object-fit: cover;
      background: var(--ink-06);
      border: 1px solid var(--hairline);
      flex: 0 0 auto;
      image-rendering: pixelated;
    }
    .portrait.empty { display: block; }
    .standing {
      margin: 0;
      font-family: var(--font-mono);
      font-size: 0.72rem;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      padding: 0.18rem 0.5rem;
      border: 1px solid var(--hairline);
      border-radius: 2px;
      white-space: nowrap;
    }
    .standing.due { border-color: var(--sheen); color: var(--sheen-strong); }
    .standing.waiting { color: var(--text-faint); }
    .facts {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr));
      gap: 0.9rem 1.5rem;
      margin: 0;
    }
    .facts dd { margin: 0.2rem 0 0; font-size: 0.95rem; }
    .sub { display: block; color: var(--text-faint); font-size: 0.78rem; }
    .detail { margin: 0; color: var(--text-faint); font-size: 0.84rem; max-width: 72ch; overflow-wrap: anywhere; }
    .actions { display: grid; gap: 1rem; }
    .row { display: flex; flex-wrap: wrap; align-items: center; gap: 0.6rem 0.9rem; }
    .row .detail { flex: 1 1 22rem; }
    .row label { display: grid; gap: 0.3rem; }
    .banner {
      margin: 0;
      padding: 0.75rem 1rem;
      border: 1px solid var(--danger);
      border-radius: 3px;
      color: var(--danger);
      font-size: 0.88rem;
    }
  `,
})
export class RainDetailPage {
  readonly id = input.required<string>();
  protected readonly rain = inject(RainService);
  protected readonly wallet = inject(WalletService);
  protected readonly arcron = inject(ArcronService);
  protected readonly modeLabel = modeLabel;
  protected readonly modeHint = modeHint;
  protected readonly intervalLabel = intervalLabel;
  private readonly broken = signal<ReadonlySet<string>>(new Set());

  constructor() {
    const stop = this.rain.watch();
    effect(() => {
      const raw = this.id();
      this.rain.focus(/^\d+$/.test(raw) ? BigInt(raw) : 0n);
    });
    inject(DestroyRef).onDestroy(() => {
      this.rain.focus(null);
      stop();
    });
  }

  protected markBroken(src: string): void {
    this.broken.update((current) => new Set([...current, src]));
  }

  protected prize(amount: bigint, rain: RainRec): string {
    return this.rain.prizeText(amount, rain);
  }

  protected prizeInfo(rain: RainRec) {
    return this.rain.prizeOf(rain);
  }

  protected prizeUnit(rain: RainRec): string {
    return this.rain.prizeOf(rain)?.unitName || 'ASA';
  }

  protected prizeKind(rain: RainRec): string {
    return rain.prizeAsset.toString() === '0' ? 'ALGO' : this.prizeUnit(rain);
  }

  protected asAssetId(rain: RainRec): number {
    return Number(rain.prizeAsset);
  }

  protected short(address: string): string {
    return address === ZERO_ADDRESS ? 'ungated' : shortAddress(address);
  }

  protected thumb(state: RainRec): string | null {
    const src = this.rain.thumbnail(state);
    if (src === null) return null;
    return this.broken().has(src) ? null : src;
  }

  protected nftAlt(state: RainRec): string {
    const held = this.rain.heldFor(state);
    if (held !== null) return held.name || held.unitName;
    return this.rain.gateName(state);
  }

  protected heldName(state: RainRec): string | null {
    const held = this.rain.heldFor(state);
    if (held === null) return null;
    return held.name || held.unitName || null;
  }

  protected gateStory(state: RainRec): string {
    const kind = this.rain.gateLabel(state);
    const status = this.rain.youStatus(state);
    const held = this.heldName(state);
    if (status === 'open') return 'Anyone can enter.';
    if (status === 'in' && held !== null) return `You're in, holding ${held}.`;
    if (status === 'in') return "You're in. You stay in every fire.";
    if (status === 'yes' && held !== null) {
      return `You hold ${held}, a ${kind}. That is what this rain checks, not the CORVID token.`;
    }
    if (status === 'connect') {
      return `Needs a ${kind} in the connected wallet. Connect an account above to see if you hold one.`;
    }
    return `Needs a ${kind} (minted by ${shortAddress(state.gateCreator)}). This account does not hold one.`;
  }

  protected standing(state: Parameters<typeof rainStanding>[0]): string {
    return rainStanding(state, this.arcron.round());
  }

  protected standingClass(state: Parameters<typeof rainStanding>[0]): string {
    return this.standing(state);
  }

  protected rainLabel(): string {
    const state = this.rain.current();
    if (state === null) return '—';
    const stand = rainStanding(state, this.arcron.round());
    if (stand === 'waiting') return 'Waiting';
    if (stand === 'due') return 'Due';
    return 'Armed';
  }

  protected cadenceHint(): string {
    const state = this.rain.current();
    if (state !== null) {
      const stand = rainStanding(state, this.arcron.round());
      if (stand === 'waiting') {
        return this.rain.waitingHint(state) ?? 'waiting';
      }
    }
    const due = this.rain.roundsToRain();
    const time = dueLabel(due, this.arcron.secondsPerRound());
    const upkeep = this.rain.upkeep();
    if (due <= 0n && upkeep !== null && roundsUntilDue(upkeep, this.arcron.round()) <= 0n) {
      return 'Keeper fire is due';
    }
    return time;
  }

  protected hasPrize(): boolean {
    return this.rain.allocation() > 0n;
  }

  protected depositAlgo(event: Event): void {
    event.preventDefault();
    const form = event.target as HTMLFormElement;
    const field = form.elements.namedItem('algo');
    const value = field instanceof HTMLInputElement ? Number(field.value) : 0;
    void this.rain.depositAlgo(value);
  }

  protected depositAsset(event: Event): void {
    event.preventDefault();
    const form = event.target as HTMLFormElement;
    const field = form.elements.namedItem('amount');
    const value = field instanceof HTMLInputElement ? Number(field.value) : 0;
    void this.rain.depositAsset(value);
  }
}
