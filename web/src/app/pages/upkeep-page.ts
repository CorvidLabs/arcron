import { ChangeDetectionStrategy, Component, computed, inject, input } from '@angular/core';
import { RouterLink } from '@angular/router';

import { ArcronService } from '../core/arcron.service';
import type { Standing } from '../core/quarantine';
import {
  algos,
  dueLabel,
  duration,
  intervalLabel,
  rounds,
  roundsAsTime,
  runwayLabel,
  shortAddress,
} from '@corvidlabs/arcron/format';
import { KeeperService } from '../core/keeper.service';
import { WalletService } from '../core/wallet.service';
import {
  boxMbr,
  effectiveFee,
  escalates,
  executionsRemaining,
  isExecutable,
  roundsUntilDue,
  SKIP_AHEAD,
  toHex,
  type Upkeep,
} from '@corvidlabs/arcron';

/**
 * What the console can say about one upkeep right now.
 *
 * `looking` is not `missing`. The registry is read on a poll, and before the
 * first read returns the console does not know whether upkeep 23 exists. The
 * front door used to collapse those two and announce "no upkeeps yet" in the
 * confident voice reserved for a fact it had checked; a page addressed by id
 * would make the same mistake louder, because the honest answer to "is there
 * an upkeep 23" is sometimes "not yet known".
 */
export type Presence = 'looking' | 'unreadable' | 'missing' | 'found';

export function presenceOf(state: {
  found: boolean;
  status: string;
  appId: number | null;
}): Presence {
  if (state.found) return 'found';
  if (state.appId === null) return 'missing';
  if (state.status === 'error') return 'unreadable';
  return state.status === 'ready' ? 'missing' : 'looking';
}

/** The three words the rest of the console uses for where an upkeep stands. */
export type UpkeepStanding = 'due' | 'scheduled' | 'needs funding';

export function standingOfUpkeep(upkeep: Upkeep, round: bigint): UpkeepStanding {
  // Against the escalated fee, not the base one: an upkeep starves at a
  // balance its creator counted as several runs.
  if (upkeep.balance < effectiveFee(upkeep, round)) return 'needs funding';
  return isExecutable(upkeep, round) ? 'due' : 'scheduled';
}

@Component({
  selector: 'arcron-upkeep-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink],
  template: `
    <nav class="back" aria-label="Breadcrumb">
      <a routerLink="/">Back to the registry</a>
    </nav>

    @switch (presence()) {
      @case ('looking') {
        <section class="panel note">
          <h2>Reading the registry…</h2>
          <p class="detail">
            Upkeep {{ id() }} has not been read yet. This is the first read of app
            {{ arcron.appId() }} returning, not an answer about whether it exists.
          </p>
        </section>
      }
      @case ('unreadable') {
        <section class="panel note" role="alert">
          <h2>The registry could not be read.</h2>
          <p class="detail">
            The last read of app {{ arcron.appId() }} failed, so nothing here can say
            whether upkeep {{ id() }} exists. Nothing will be sent until it recovers.
          </p>
        </section>
      }
      @case ('missing') {
        <section class="panel note">
          <h2>No upkeep {{ id() }} on app {{ arcron.appId() ?? 'none' }}.</h2>
          <p class="detail">
            Either it was never registered on this app, or its creator has cancelled it,
            which deletes the box and returns the escrow. Upkeep ids are per app, so the
            same number on another deployment is a different upkeep.
          </p>
          <p><a routerLink="/">See what is registered</a></p>
        </section>
      }
      @default {
        @if (view(); as view) {
          <article class="upkeep">
            <header class="panel identity">
              <div class="title">
                <p class="eyebrow">Upkeep</p>
                <h2 class="mono">#{{ view.id }}</h2>
                <p class="standing" [class]="view.standingClass">{{ view.standing }}</p>
              </div>
              <p class="detail">{{ view.whatHappensNext }}</p>

              <dl class="facts">
                <div>
                  <dt class="eyebrow">Calls</dt>
                  <dd class="mono">
                    @if (view.targetLink; as link) {
                      <a [href]="link" target="_blank" rel="noreferrer noopener">
                        app {{ view.target }}
                      </a>
                    } @else {
                      app {{ view.target }}
                    }
                  </dd>
                </div>
                <div>
                  <dt class="eyebrow">Selector</dt>
                  <dd class="mono">{{ view.selector }}</dd>
                </div>
                <div>
                  <dt class="eyebrow">Arguments</dt>
                  <dd class="mono args">{{ view.argumentSummary }}</dd>
                </div>
                <div>
                  <dt class="eyebrow">Registered by</dt>
                  <dd class="mono">
                    @if (view.creatorLink; as link) {
                      <a [href]="link" target="_blank" rel="noreferrer noopener">
                        {{ view.creatorShort }}
                      </a>
                    } @else {
                      {{ view.creatorShort }}
                    }
                    @if (view.yours) {
                      <span class="yours">yours</span>
                    }
                  </dd>
                </div>
              </dl>
              <p class="full mono">{{ view.creator }}</p>
            </header>

            <section class="panel">
              <h3>Schedule</h3>
              <dl class="facts">
                <div>
                  <dt class="eyebrow">Cadence</dt>
                  <dd>{{ view.interval }}</dd>
                </div>
                <div>
                  <dt class="eyebrow">Next run</dt>
                  <dd>
                    <span class="mono">round {{ view.nextRound }}</span>
                    <span class="sub">{{ view.due }}</span>
                  </dd>
                </div>
                <div>
                  <dt class="eyebrow">Last serviced</dt>
                  <dd>{{ view.lastRan }}</dd>
                </div>
                <div>
                  <dt class="eyebrow">Runs so far</dt>
                  <dd class="mono">{{ view.executed }}</dd>
                </div>
              </dl>
              <p class="detail">{{ view.policyNote }}</p>
            </section>

            <section class="panel">
              <h3>Money</h3>
              <dl class="facts">
                <div>
                  <dt class="eyebrow">Fee per run</dt>
                  <dd class="mono">{{ view.fee }}</dd>
                </div>
                <div>
                  <dt class="eyebrow">Charged now</dt>
                  <dd class="mono" [class.escalated]="view.escalated">{{ view.feeNow }}</dd>
                </div>
                <div>
                  <dt class="eyebrow">Escrow</dt>
                  <dd class="mono">{{ view.balance }}</dd>
                </div>
                <div>
                  <dt class="eyebrow">Runway</dt>
                  <dd>{{ view.runway }}</dd>
                </div>
              </dl>
              <p class="detail">{{ view.feeNote }}</p>
              @if (view.bonus; as bonus) {
                <p class="detail">{{ bonus }}</p>
              }
            </section>

            <section class="panel actions">
              <h3>What anyone can do here</h3>

              <div class="row">
                <button
                  type="button"
                  class="primary"
                  [disabled]="!view.canExecute || keeper.busy() !== null"
                  (click)="execute(view.upkeep)"
                >
                  Run it now
                </button>
                <p class="detail">{{ view.executeNote }}</p>
              </div>

              <form class="row top-up" (submit)="topUp($event, view.upkeep)">
                <label>
                  <span class="eyebrow">Add to escrow (ALGO)</span>
                  <input
                    type="number"
                    name="amount"
                    step="any"
                    min="0.000001"
                    [value]="view.suggestedTopUp"
                    required
                  />
                </label>
                <button
                  type="submit"
                  class="ghost"
                  [disabled]="!wallet.connected() || keeper.busy() !== null || !arcron.canWrite()"
                >
                  Top up
                </button>
                <p class="detail">{{ view.topUpNote }}</p>
              </form>

              @if (view.yours) {
                <div class="row">
                  <button
                    type="button"
                    class="ghost danger"
                    [disabled]="keeper.busy() !== null || !arcron.canWrite()"
                    (click)="cancel(view.upkeep)"
                  >
                    Cancel this upkeep
                  </button>
                  <p class="detail">{{ view.cancelNote }}</p>
                </div>
              }

              @if (!wallet.connected()) {
                <p class="detail">Connect a wallet above to do any of this.</p>
              } @else if (!arcron.canWrite()) {
                <p class="detail">
                  Nothing can be sent while the console is not showing the current state of
                  the app it is pointed at.
                </p>
              }
            </section>

            <section class="panel">
              <h3>Where this upkeep lives</h3>
              <p class="detail">{{ view.provenance }}</p>
              @if (view.keeperAppLink; as link) {
                <p>
                  <a [href]="link" target="_blank" rel="noreferrer noopener">
                    App {{ arcron.appId() }} on the explorer
                  </a>
                </p>
              }
            </section>
          </article>
        }
      }
    }
  `,
  styles: `
    :host { display: grid; gap: 1.25rem; align-content: start; }
    .back { font-size: 0.85rem; }
    .upkeep { display: grid; gap: 1.25rem; }
    h2 { margin: 0; font-size: 1.6rem; }
    h3 { margin: 0 0 0.8rem; font-size: 1rem; }
    .note h2 { font-size: 1.1rem; margin-bottom: 0.4rem; }
    .identity { display: grid; gap: 0.9rem; }
    .title { display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.4rem 0.8rem; }
    .title .eyebrow { margin: 0; }
    .standing {
      margin: 0;
      font-family: var(--font-mono);
      font-size: 0.72rem;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      padding: 0.1rem 0.45rem;
      border: 1px solid var(--hairline);
      border-radius: 2px;
    }
    .standing.due { border-color: var(--sheen); color: var(--sheen-strong); }
    .standing.needs-funding { border-color: var(--warning); color: var(--warning); }
    .facts {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr));
      gap: 0.9rem 1.5rem;
      margin: 0;
    }
    .facts dd { margin: 0.2rem 0 0; font-size: 0.95rem; }
    .facts .args { font-size: 0.8rem; overflow-wrap: anywhere; }
    .sub { display: block; color: var(--text-faint); font-size: 0.78rem; }
    .escalated { color: var(--warning); font-weight: 600; }
    .detail { margin: 0; color: var(--text-faint); font-size: 0.84rem; max-width: 72ch; }
    .full { margin: 0; color: var(--text-faint); font-size: 0.74rem; overflow-wrap: anywhere; }
    .yours {
      display: inline-block;
      margin-left: 0.35rem;
      padding: 0 0.3rem;
      border: 1px solid var(--sheen);
      border-radius: 2px;
      color: var(--sheen);
      font-size: 0.62rem;
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }
    .actions { display: grid; gap: 1rem; }
    .row { display: flex; flex-wrap: wrap; align-items: center; gap: 0.6rem 0.9rem; }
    .row .detail { flex: 1 1 22rem; }
    .top-up { align-items: end; }
    .top-up label { display: grid; gap: 0.3rem; }
  `,
})
export class UpkeepPage {
  /** Bound from `/u/:id` by the router's component input binding. */
  readonly id = input.required<string>();

  protected readonly arcron = inject(ArcronService);
  protected readonly keeper = inject(KeeperService);
  protected readonly wallet = inject(WalletService);

  private readonly upkeepId = computed(() => {
    const raw = this.id();
    return /^\d+$/.test(raw) ? BigInt(raw) : null;
  });

  private readonly upkeep = computed(() => {
    const id = this.upkeepId();
    if (id === null) return null;
    return this.arcron.upkeeps().find((candidate) => candidate.id === id) ?? null;
  });

  protected readonly presence = computed(() =>
    presenceOf({
      found: this.upkeep() !== null,
      status: this.arcron.status(),
      appId: this.arcron.appId(),
    }),
  );

  protected readonly view = computed(() => {
    const upkeep = this.upkeep();
    if (upkeep === null) return null;
    const round = this.arcron.round();
    const pace = this.arcron.secondsPerRound();
    const config = this.arcron.config();
    const fee = effectiveFee(upkeep, round);
    const standing = standingOfUpkeep(upkeep, round);
    const yours = this.wallet.activeAddress() === upkeep.creator;
    const refund = upkeep.balance + BigInt(boxMbr(upkeep.callArgs));
    const worstCase = upkeep.feeCap > upkeep.feePerExecution ? upkeep.feeCap : upkeep.feePerExecution;
    const lastRanAgo = roundsAsTime(round - upkeep.lastServicedRound, pace);

    return {
      upkeep,
      id: String(upkeep.id),
      standing,
      standingClass: standing === 'needs funding' ? 'needs-funding' : standing,
      whatHappensNext: whatHappensNext(standing, upkeep, round, pace),
      target: String(upkeep.targetApp),
      targetLink: config.explorerApp?.(upkeep.targetApp) ?? null,
      keeperAppLink:
        this.arcron.appId() === null ? null : (config.explorerApp?.(this.arcron.appId() ?? 0) ?? null),
      selector: `0x${toHex(upkeep.callArgs[0] ?? new Uint8Array())}`,
      argumentSummary: argumentSummary(upkeep),
      creator: upkeep.creator,
      creatorShort: shortAddress(upkeep.creator),
      creatorLink: config.explorerAccount?.(upkeep.creator) ?? null,
      yours,

      interval: intervalLabel(upkeep.intervalRounds, pace),
      nextRound: String(upkeep.nextExecutionRound),
      due: dueLabel(roundsUntilDue(upkeep, round), pace),
      lastRan:
        upkeep.timesExecuted === 0n
          ? 'never run'
          : lastRanAgo === null
            ? `round ${upkeep.lastServicedRound}`
            : `round ${upkeep.lastServicedRound}, about ${lastRanAgo} ago`,
      executed: String(upkeep.timesExecuted),
      policyNote:
        upkeep.policy === SKIP_AHEAD
          ? 'If a run is missed this upkeep skips ahead: it runs once and moves to the next slot, and the missed intervals are never replayed.'
          : `If runs are missed this upkeep catches up: every missed interval is replayed, one fee each. The number of fees is bounded by how long it went unkept and not by the escrow, so a long outage on a short cadence can drain everything and still not arrive.`,

      fee: algos(upkeep.feePerExecution),
      feeNow: algos(fee),
      escalated: fee > upkeep.feePerExecution,
      balance: algos(upkeep.balance),
      runway: runwayLabel(executionsRemaining(upkeep), upkeep.intervalRounds, pace),
      feeNote: escalates(upkeep)
        ? `The fee rises with lateness up to a ceiling of ${algos(upkeep.feeCap)}, so the escrow has to cover the ceiling rather than the base fee to stay executable once late. The fee cannot be changed after registration.`
        : 'This upkeep pays the same fee however late it gets, and that fee cannot be changed after registration.',
      bonus:
        upkeep.feeAsset === 0n
          ? null
          : `It also pays a bonus of ${upkeep.assetFee} base units of asset ${upkeep.feeAsset} per run, with ${upkeep.assetBalance} left in bonus escrow, to keepers opted in to that asset.`,

      canExecute: standing === 'due' && this.wallet.connected() && this.arcron.canWrite(),
      executeNote:
        standing === 'due'
          ? `Anyone can run a due upkeep and be paid its fee. This one pays ${algos(fee)} and costs about ${algos(3_000n)} in group fees to send, so it nets ${algos(fee - 3_000n, { sign: true })}.`
          : standing === 'needs funding'
            ? 'No keeper can run this until its escrow covers one fee. That is the creator\'s to fix, and topping it up is what fixes it.'
            : `Not due yet. Anyone may run it once round ${upkeep.nextExecutionRound} arrives.`,
      suggestedTopUp: (Number(worstCase * 3n) / 1e6).toString(),
      topUpNote: yours
        ? 'Adding to your own escrow buys more runs. It cannot change the fee, the cadence or the ceiling, which are fixed at registration.'
        : `This upkeep belongs to ${upkeep.creator}. Anything you add becomes theirs: only they can cancel it, and cancelling returns the whole remaining escrow to them. Add funds only because you want this call to keep happening.`,
      cancelNote: `Deletes the box and returns ${algos(refund)} to you: ${algos(upkeep.balance)} of remaining escrow plus the ${algos(BigInt(boxMbr(upkeep.callArgs)))} box deposit. It cannot be undone, and re-registering gets a new id.`,
      provenance: provenance(this.arcron.appId(), this.arcron.standing(), this.arcron.frozen()),
    };
  });

  protected execute(upkeep: Upkeep): void {
    void this.keeper.execute(upkeep);
  }

  protected cancel(upkeep: Upkeep): void {
    void this.keeper.cancel(upkeep);
  }

  protected topUp(event: Event, upkeep: Upkeep): void {
    event.preventDefault();
    const form = event.target as HTMLFormElement;
    const algo = Number(new FormData(form).get('amount'));
    const microAlgo = Math.round(algo * 1e6);
    if (Number.isFinite(microAlgo) && microAlgo > 0) void this.keeper.topUp(upkeep, microAlgo);
  }
}

function whatHappensNext(
  standing: UpkeepStanding,
  upkeep: Upkeep,
  round: bigint,
  pace: number,
): string {
  if (standing === 'needs funding') {
    return `Its escrow of ${algos(upkeep.balance)} is below the ${algos(effectiveFee(upkeep, round))} one run now costs, so no keeper can execute it. Nothing happens until somebody tops it up.`;
  }
  if (standing === 'due') {
    const late = roundsUntilDue(upkeep, round);
    const behind = roundsAsTime(late, pace);
    return late === 0n
      ? 'It is due this round. The first keeper to reach it runs it and is paid.'
      : `It has been executable for ${rounds(late)}${behind === null ? '' : `, about ${behind}`}, and any keeper can run it and be paid.`;
  }
  const wait = roundsAsTime(roundsUntilDue(upkeep, round), pace);
  return `It is on schedule. Nothing can run it until round ${upkeep.nextExecutionRound}${wait === null ? '' : `, roughly ${duration(Number(roundsUntilDue(upkeep, round)) * pace)} away`}.`;
}

/** The call args after the selector, as hex. Decoding them needs the signature, which the chain does not store. */
function argumentSummary(upkeep: Upkeep): string {
  const args = upkeep.callArgs.slice(1);
  if (args.length === 0) return 'none, so the call is the selector alone';
  return args.map((arg) => `0x${toHex(arg)}`).join('  ');
}

function provenance(
  appId: number | null,
  standing: Standing,
  frozen: boolean | null,
): string {
  const identity =
    standing === 'canonical'
      ? `This upkeep lives in app ${appId}, the Arcron deployment this console ships pointing at.`
      : standing === 'unverifiable'
        ? `This upkeep lives in app ${appId}. No published deployment is recorded for this network, so nothing here can tell you it is the one you meant.`
        : `This upkeep lives in app ${appId}, which is not the Arcron deployment. Whoever deployed it wrote the code holding this escrow.`;
  const governance =
    frozen === null
      ? 'Whether its creator can still replace its programs is not known yet.'
      : frozen
        ? 'Its creator has frozen it, so the programs holding this escrow can no longer be replaced.'
        : 'Its creator has not frozen it, so they can still replace its programs and reach every escrow in it, including this one.';
  return `${identity} ${governance}`;
}
