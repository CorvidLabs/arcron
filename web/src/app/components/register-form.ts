import { ChangeDetectionStrategy, Component, computed, inject, output } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { map } from 'rxjs';

import { ArcronService } from '../core/arcron.service';
import { algos, duration, runwayLabel } from '@corvidlabs/arcron/format';
import { encodeCall, PULSE_TICK_SIGNATURE } from '@corvidlabs/arcron/keeper-abi';
import { ExplorerLink } from './explorer-link';
import { KeeperService } from '../core/keeper.service';
import { affordability } from '../core/affordability';
import { PayerService } from '../core/payer.service';
import { subjectOf, TargetTestService } from '../core/target-test.service';
import {
  boxMbr,
  CATCH_UP,
  MAX_CALL_ARGS,
  MAX_INTERVAL_ROUNDS,
  MAX_UPKEEP_FEE,
  MIN_INTERVAL_ROUNDS,
  MIN_UPKEEP_FEE,
  SUGGESTED_UPKEEP_FEE,
  registrationCost,
  SKIP_AHEAD,
  toHex,
} from '@corvidlabs/arcron';
import { PULL_PATTERN_URL } from '@corvidlabs/arcron/target-test';

const CADENCES = [
  { label: '30 s', seconds: 30 },
  { label: '5 min', seconds: 300 },
  { label: '1 hour', seconds: 3_600 },
  { label: '1 day', seconds: 86_400 },
] as const;

const INTEGRATION_GUIDE_URL =
  'https://github.com/CorvidLabs/arcron/blob/main/docs/integrating.md';

@Component({
  selector: 'arcron-register-form',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ReactiveFormsModule, ExplorerLink],
  template: `
    <section class="panel">
      <header>
        <h2>Register an upkeep</h2>
        <p class="subtitle">
          Escrow ALGO to have any keeper call your app on a schedule. The call is a NoOp carrying
          a method selector, plus any arguments you fix here. A keeper chooses when it happens,
          never what it says. A fee ceiling makes a neglected upkeep more attractive,
          though only competing keepers hold the price below it. Leave it at zero unless an upkeep
          is actually going unserviced.
        </p>
        <p class="paying">
          Paying into keeper app
          @if (keeperAppText(); as appId) {
            <arcron-explorer-link kind="app" [value]="appId" />
          } @else {
            <span class="mono">none selected</span>
          }
          @if (arcron.appAccount(); as account) {
            <span class="account">
              at account
              <arcron-explorer-link kind="account" [value]="account.address" />
            </span>
          }
        </p>
      </header>

      <form [formGroup]="form" (ngSubmit)="submit()">
        <div class="grid">
          <label>
            <span class="eyebrow">Target app id</span>
            <input type="number" formControlName="targetApp" required />
            <small>{{ targetHint() }}</small>
            @if (targetAppText(); as targetApp) {
              <small>
                <arcron-explorer-link kind="app" [value]="targetApp" label="check this app id" />
              </small>
            }
          </label>

          <label>
            <span class="eyebrow">Method signature</span>
            <input type="text" formControlName="signature" spellcheck="false" />
            <small class="mono">{{ selector() }}</small>
          </label>

          @if (argumentTypes().length > 0) {
            <label>
              <span class="eyebrow">Arguments, one per line</span>
              <textarea formControlName="callArguments" rows="3" spellcheck="false"></textarea>
              <small>{{ argumentHint() }}</small>
            </label>
          }

          <label>
            <span class="eyebrow">Bonus asset id (optional)</span>
            <input type="number" formControlName="feeAsset" min="0" />
            <small>{{ assetHint() }}</small>
          </label>

          @if (value().feeAsset > 0) {
            <label>
              <span class="eyebrow">Bonus per run (base units)</span>
              <input type="number" formControlName="assetFee" min="1" step="1" />
              <small>paid on top of the ALGO fee, to keepers opted in to the asset</small>
            </label>
          }

          <label>
            <span class="eyebrow">Interval (rounds)</span>
            <input type="number" formControlName="intervalRounds" [min]="minInterval" />
            <small>{{ cadenceHint() }}</small>
          </label>

          <label>
            <span class="eyebrow">Fee per execution (ALGO)</span>
            <input type="number" step="0.001" formControlName="feePerExecution" />
            <small>min {{ minFeeAlgo }}; keepers spend ~0.003 in group fees, so at the
              minimum they net 0.001 and cannot fund a machine</small>
          </label>

          <label>
            <span class="eyebrow">Fee ceiling (ALGO)</span>
            <input type="number" step="any" formControlName="feeCap" min="0" />
            <small>{{ capHint() }}</small>
          </label>

          <label>
            <span class="eyebrow">Funding (ALGO)</span>
            <input type="number" step="0.001" formControlName="funding" />
            <small>{{ runway() }}</small>
          </label>

          <fieldset class="policy">
            <legend class="eyebrow">If a run is missed</legend>
            <label class="choice">
              <input type="radio" formControlName="policy" [value]="skipAhead" />
              <span>
                <strong>Skip ahead</strong>
                <small>Run once and move to the next slot. For work where only the latest run matters.</small>
              </span>
            </label>
            <label class="choice">
              <input type="radio" formControlName="policy" [value]="catchUp" />
              <span>
                <strong>Catch up</strong>
                <small>Replay every missed interval, one fee each. For work where every period counts.</small>
              </span>
            </label>
          </fieldset>
        </div>

        <div class="cadences">
          <span class="eyebrow">Quick cadence</span>
          @for (cadence of cadences; track cadence.label) {
            <button type="button" class="ghost small" (click)="useCadence(cadence.seconds)">
              every {{ cadence.label }}
            </button>
          }
        </div>

        <section class="tester" aria-labelledby="test-heading">
          <div class="tester-head">
            <div>
              <h3 id="test-heading">Test this call first</h3>
              <p class="note">
                Simulates the exact inner call a keeper would make, from the keeper app's own
                account, against the chain as it is now. Signs nothing, sends nothing, costs
                nothing, and needs no wallet. If you have nothing to schedule yet, the
                <a [href]="guideUrl" target="_blank" rel="noopener noreferrer">integration guide</a>
                is where a target contract starts.
              </p>
            </div>
            <button
              type="button"
              class="ghost"
              [disabled]="!canTest() || test.running()"
              (click)="runTest()"
            >
              {{ test.running() ? 'Simulating…' : 'Test the call' }}
            </button>
          </div>

          @if (!canTest()) {
            <p class="note">{{ testBlocked() }}</p>
          }

          <div aria-live="polite">
            @if (report(); as found) {
              @if (found.unreachable; as message) {
                <div class="verdict unknown">
                  <p class="verdict-line"><span class="mark" aria-hidden="true">?</span> No answer</p>
                  <p class="note">
                    The node did not answer, so this says nothing about your target: {{ message }}
                  </p>
                </div>
              } @else if (found.outcome; as outcome) {
                @if (outcome.accepted) {
                  <div class="verdict pass">
                    <p class="verdict-line">
                      <span class="mark" aria-hidden="true">✓</span> Accepted the call
                    </p>
                    <p class="note">{{ claims() }}</p>
                    @if (outcome.grade; as grade) {
                      <div class="grade" [class]="grade.key">
                        <p class="grade-line">
                          <span class="badge">{{ gradeLabel(grade.key) }}</span>
                          {{ grade.headline }}
                        </p>
                        <p class="note">{{ grade.detail }}</p>
                        @if (grade.key === 'unexecutable') {
                          <p class="note">
                            <a [href]="pullPatternUrl" target="_blank" rel="noopener noreferrer">
                              Read the pull pattern
                            </a>
                          </p>
                        }
                        @if (outcome.counts; as counts) {
                          <p class="note mono">{{ composition(counts) }}</p>
                        }
                      </div>
                    }
                    <p class="note">{{ refusals }}</p>
                  </div>
                } @else {
                  <div class="verdict fail">
                    <p class="verdict-line">
                      <span class="mark" aria-hidden="true">✕</span> Refused the call
                    </p>
                    <p class="note">{{ refusalReason(outcome.failureKind) }}</p>
                    <p class="note mono failure">{{ outcome.failure }}</p>
                    <p class="note">
                      An upkeep whose target refuses is never serviced. It still holds its box
                      deposit and its escrow, and the only way to get either back is to cancel it.
                    </p>
                  </div>
                }
              }
            }
          </div>
        </section>

        <section class="cost" aria-labelledby="cost-heading">
          <div class="cost-head">
            <div>
              <h3 id="cost-heading" class="eyebrow">Up-front cost</h3>
              <strong class="mono total">{{ totalLabel() }}</strong>
            </div>
            <p class="note">{{ pricedNote() }}</p>
          </div>

          @if (cost(); as breakdown) {
            <dl class="lines">
              <div>
                <dt>Box deposit</dt>
                <dd class="mono">{{ algo(breakdown.boxDeposit) }}</dd>
                <dd class="note">Held for the upkeep's box. Returned in full when you cancel.</dd>
              </div>
              <div>
                <dt>Escrow</dt>
                <dd class="mono">{{ algo(breakdown.escrow) }}</dd>
                <dd class="note">
                  Spent one execution at a time. Whatever is left returns when you cancel.
                </dd>
              </div>
              <div>
                <dt>Network fees</dt>
                <dd class="mono">{{ algo(breakdown.networkFees) }}</dd>
                <dd class="note">
                  Three transactions at this node's current minimum fee. Gone either way,
                  including if the group fails.
                </dd>
              </div>
            </dl>
          }

          <div class="balance">
            <p class="note">{{ balanceLine() }}</p>
            <button type="button" class="ghost small" [disabled]="payer.reading()" (click)="recheck()">
              {{ payer.reading() ? 'Reading…' : 'Re-check balance' }}
            </button>
          </div>
        </section>

        <label class="attest">
          <input type="checkbox" formControlName="attested" />
          <span>
            <strong>I have tested this call against my own app and accept the risk.</strong>
            <small>
              Arcron cannot know whether calling this method on a schedule is what you want. A
              keeper will make this call, over and over, whatever it does. The Test button above
              helps and is not a substitute for you: it reads the chain as it is now, and it can
              be skipped entirely.
            </small>
          </span>
        </label>

        <div class="submit">
          <button type="submit" class="primary" [disabled]="!canSubmit()">
            {{ keeper.busy() === 'register' ? 'Registering…' : 'Register upkeep' }}
          </button>
          @if (!keeper.canSign()) {
            <p class="hint">Connect an account to register.</p>
          } @else if (problem(); as reason) {
            <p class="hint">{{ reason }}</p>
          }
        </div>
      </form>
    </section>
  `,
  styles: `
    .panel { display: grid; gap: 1.1rem; }
    header h2 { margin: 0; font-size: 1.1rem; }
    .subtitle { margin: 0.2rem 0 0; color: var(--text-faint); font-size: 0.85rem; max-width: 52ch; }
    .paying { margin: 0.5rem 0 0; font-size: 0.78rem; color: var(--text-faint); }
    .paying .account { margin-left: 0.35rem; }
    form { display: grid; gap: 1rem; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(12.5rem, 1fr)); gap: 0.9rem; }
    textarea { font-family: var(--font-mono); font-size: 0.8rem; resize: vertical; }
    label { display: grid; gap: 0.3rem; align-content: start; }
    small, .note { color: var(--text-faint); font-size: 0.72rem; }
    .note { margin: 0.25rem 0 0; max-width: 68ch; }
    .policy { grid-column: 1 / -1; display: grid; gap: 0.5rem; margin: 0; padding: 0.7rem 0.85rem; border: 1px solid var(--hairline); }
    .policy legend { padding: 0 0.35rem; }
    .choice { display: flex; align-items: start; gap: 0.55rem; }
    .choice input { margin-top: 0.25rem; }
    .choice span { display: grid; gap: 0.1rem; }
    .choice strong { font-size: 0.85rem; font-weight: 600; }
    .cadences { display: flex; flex-wrap: wrap; align-items: center; gap: 0.4rem; }
    .cadences .eyebrow { margin-right: 0.3rem; }

    .tester, .cost {
      display: grid;
      gap: 0.6rem;
      padding: 0.8rem 0.9rem;
      border: 1px solid var(--hairline);
      border-radius: 2px;
    }
    .tester-head, .cost-head {
      display: flex;
      gap: 0.9rem;
      align-items: start;
      justify-content: space-between;
      flex-wrap: wrap;
    }
    .tester h3 { margin: 0; font-size: 0.92rem; }

    /* Every verdict carries a word and a mark as well as a rule colour, so the
       result survives being read without colour at all. */
    .verdict {
      border-left: 3px solid var(--hairline);
      padding: 0.5rem 0 0.5rem 0.7rem;
    }
    .verdict.pass { border-left-color: var(--success); }
    .verdict.fail { border-left-color: var(--danger); }
    .verdict.unknown { border-left-color: var(--warning); }
    .verdict-line { margin: 0; font-size: 0.88rem; font-weight: 600; }
    .mark { font-family: var(--font-mono); margin-right: 0.35rem; }
    .failure { overflow-wrap: anywhere; }

    .grade { margin-top: 0.55rem; }
    .grade-line { margin: 0; font-size: 0.82rem; }
    .badge {
      font-family: var(--font-mono);
      font-size: 0.66rem;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      border: 1px solid var(--hairline);
      border-radius: 2px;
      padding: 0.05rem 0.35rem;
      margin-right: 0.45rem;
    }
    .grade.none .badge, .grade.servable .badge { border-color: var(--success); color: var(--success); }
    .grade.protocol-only .badge { border-color: var(--warning); color: var(--warning); }
    .grade.unexecutable .badge { border-color: var(--danger); color: var(--danger); }

    .total { font-size: 1.25rem; display: block; margin-top: 0.15rem; }
    .lines { margin: 0; display: grid; gap: 0.5rem; }
    .lines > div {
      display: grid;
      grid-template-columns: minmax(7rem, auto) 1fr;
      column-gap: 0.8rem;
    }
    .lines dt { font-size: 0.8rem; }
    .lines dd { margin: 0; font-size: 0.8rem; }
    .lines dd.note { grid-column: 1 / -1; margin-top: 0.1rem; }
    .balance {
      display: flex;
      gap: 0.75rem;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      border-top: 1px solid var(--hairline);
      padding-top: 0.55rem;
    }
    .balance .note { margin: 0; }

    .attest {
      display: flex;
      align-items: start;
      gap: 0.55rem;
      padding: 0.7rem 0.85rem;
      border: 1px solid var(--hairline);
      border-radius: 2px;
    }
    .attest input { margin-top: 0.2rem; }
    .attest span { display: grid; gap: 0.2rem; }
    .attest strong { font-size: 0.85rem; font-weight: 600; }

    .submit { display: flex; align-items: center; gap: 0.85rem; flex-wrap: wrap; }
    .hint { margin: 0; color: var(--text-faint); font-size: 0.8rem; max-width: 68ch; }
    input.ng-invalid.ng-touched { border-color: var(--danger); }
  `,
})
export class RegisterForm {
  protected readonly keeper = inject(KeeperService);
  protected readonly arcron = inject(ArcronService);
  protected readonly payer = inject(PayerService);
  protected readonly test = inject(TargetTestService);
  private readonly builder = inject(FormBuilder);

  /**
   * The id the contract assigned, emitted once the call has landed.
   *
   * The form does not know where that should lead; its page does. Registering
   * ends on `/u/:id` rather than on a confirmation panel.
   */
  readonly registered = output<bigint>();

  protected readonly minInterval = MIN_INTERVAL_ROUNDS;
  protected readonly minFeeAlgo = MIN_UPKEEP_FEE / 1e6;
  protected readonly cadences = CADENCES;
  protected readonly catchUp = Number(CATCH_UP);
  protected readonly skipAhead = Number(SKIP_AHEAD);
  protected readonly pullPatternUrl = PULL_PATTERN_URL;
  protected readonly guideUrl = INTEGRATION_GUIDE_URL;

  /**
   * What the Test button will not certify, stated next to every pass.
   *
   * The plan's word for this is that the button grades and never shows a flat
   * PASS. These are the two things a simulation genuinely cannot see: whether
   * anybody turns up, and whether the same call will still touch the same
   * things once the target's own state has moved.
   */
  protected readonly refusals =
    'What this cannot tell you: whether a keeper will turn up, and whether this call will ' +
    'still need the same resources later. A simulation reports what it touched against the ' +
    'chain as it is now, and a target whose needs grow with its own state can touch more.';

  protected readonly form = this.builder.nonNullable.group({
    targetApp: [0, [Validators.required, Validators.min(1)]],
    signature: [PULSE_TICK_SIGNATURE, Validators.required],
    callArguments: [''],
    feeAsset: [0, [Validators.required, Validators.min(0)]],
    assetFee: [0, [Validators.required, Validators.min(0)]],
    intervalRounds: [
      MIN_INTERVAL_ROUNDS,
      [Validators.required, Validators.min(MIN_INTERVAL_ROUNDS), Validators.max(MAX_INTERVAL_ROUNDS)],
    ],
    feePerExecution: [
      SUGGESTED_UPKEEP_FEE / 1e6,
      [Validators.required, Validators.min(MIN_UPKEEP_FEE / 1e6), Validators.max(MAX_UPKEEP_FEE / 1e6)],
    ],
    funding: [(SUGGESTED_UPKEEP_FEE * 3) / 1e6, [Validators.required, Validators.min(MIN_UPKEEP_FEE / 1e6)]],
    // The encoding keeps CATCH_UP as zero so nothing registered before means
    // something new. The form defaults the other way, because "only the
    // latest run matters" is the commoner shape and the safer mistake.
    policy: [Number(SKIP_AHEAD), Validators.required],
    // Zero is off: the fee never moves. Opt in, rather than surprising a
    // creator with an escrow that drains three times faster than the headline.
    feeCap: [0, [Validators.required, Validators.min(0), Validators.max(MAX_UPKEEP_FEE / 1e6)]],
    // Deliberately carries no validator. Submitting is gated on it separately,
    // with a reason of its own, so an unticked box never falls through to
    // "check the highlighted fields" as though it were a mistyped number.
    attested: [false],
  }, {
    // Cross-field rules the chain enforces too. Catching them here turns a
    // rejected transaction into a disabled button.
    validators: (group) => {
      const fee = Number(group.get('feePerExecution')?.value ?? 0);
      const cap = Number(group.get('feeCap')?.value ?? 0);
      const funding = Number(group.get('funding')?.value ?? 0);
      if (cap !== 0 && cap < fee) return { capBelowFee: true };
      // `register` funds against the price the upkeep can actually be
      // charged, so an upkeep with a ceiling must escrow one run at it.
      if (funding < Math.max(fee, cap)) return { fundingBelowWorstCase: true };
      const asset = Number(group.get('feeAsset')?.value ?? 0);
      const bonus = Number(group.get('assetFee')?.value ?? 0);
      if (asset > 0 && bonus <= 0) return { bonusOfNothing: true };
      return null;
    },
  });

  /**
   * Reactive-forms state is not signal-based, so validity has to be pulled
   * into a signal. A computed() reading `form.valid` directly would cache the
   * first answer and the submit button would never enable.
   */
  protected readonly status = toSignal(this.form.statusChanges, { initialValue: this.form.status });

  protected readonly value = toSignal(
    this.form.valueChanges.pipe(map(() => this.form.getRawValue())),
    { initialValue: this.form.getRawValue() },
  );

  /** Measured pace where we have it; Algorand's nominal block time otherwise. */
  private readonly pace = computed(() => this.arcron.secondsPerRound());

  /** The ABI argument types the signature declares, or none if it will not parse. */
  protected readonly argumentTypes = computed<string[]>(() => {
    const inner = this.value().signature.match(/\((.*)\)/)?.[1] ?? '';
    return inner === '' ? [] : inner.split(',');
  });

  /**
   * The app args this call needs, or the reason it cannot be built.
   *
   * Encoding happens here rather than on submit so a bad argument disables the
   * button with the reason under the field, instead of being rejected on chain.
   */
  private readonly encoded = computed<{ args: Uint8Array[] } | { error: string }>(() => {
    const { signature, callArguments } = this.value();
    const values = callArguments.split('\n').map((line) => line.trim()).filter((line) => line !== '');
    try {
      const args = encodeCall(signature, values);
      if (args.length > MAX_CALL_ARGS) {
        return {
          error: `${args.length} app args, but an execution carries at most ${MAX_CALL_ARGS}, counting the selector`,
        };
      }
      return { args };
    } catch (cause) {
      return { error: (cause as Error).message };
    }
  });

  private readonly callArgs = computed(() => {
    const built = this.encoded();
    return 'args' in built ? built.args : null;
  });

  protected readonly selector = computed(() => {
    const built = this.encoded();
    if ('error' in built) return built.error;
    return `selector 0x${toHex(built.args[0])}${built.args.length > 1 ? ` + ${built.args.length - 1} argument(s)` : ''}`;
  });

  /** Null when there is no app to link to, which the template branches on. */
  protected readonly keeperAppText = computed(() => {
    const appId = this.arcron.appId();
    return appId === null ? null : String(appId);
  });

  protected readonly targetAppText = computed(() => {
    const { targetApp } = this.value();
    return targetApp > 0 ? String(targetApp) : null;
  });

  protected readonly targetHint = computed(() => {
    const { targetApp } = this.value();
    if (targetApp <= 0) {
      return 'The app Arcron will call. You fix what is called here; a keeper only ever chooses when.';
    }
    return 'You fix what is called; a keeper only ever chooses when. Nothing here checks that this app exists, so test the call below.';
  });

  protected readonly argumentHint = computed(() => {
    const types = this.argumentTypes();
    const built = this.encoded();
    if ('error' in built) return built.error;
    return `${types.join(', ')} (one value per line)`;
  });

  protected readonly assetHint = computed(() => {
    const { feeAsset } = this.value();
    if (feeAsset === 0) return 'none, so the upkeep pays its keeper in ALGO only';
    return 'the app must opt in to this asset, which costs 0.1 ALGO and cannot be undone';
  });

  protected readonly cadenceHint = computed(() => {
    const { intervalRounds } = this.value();
    if (intervalRounds < MIN_INTERVAL_ROUNDS) return `minimum ${MIN_INTERVAL_ROUNDS} rounds`;
    const basis = this.arcron.paceSource() === 'measured' ? 'measured' : 'nominal';
    return `≈ every ${duration(intervalRounds * this.pace())} at ${this.pace().toFixed(1)} s/round (${basis})`;
  });

  protected readonly runway = computed(() => {
    const { funding, feePerExecution, feeCap, intervalRounds } = this.value();
    if (feePerExecution <= 0) return 'set a fee first';
    // Priced at the ceiling when one is set: that is what the escrow can
    // actually be charged, and the number worth budgeting against.
    const worstCase = feeCap > feePerExecution ? feeCap : feePerExecution;
    const runs = BigInt(Math.floor(funding / worstCase));
    const label = runwayLabel(runs, BigInt(Math.max(intervalRounds, 0)), this.pace());
    return worstCase === feePerExecution ? label : `${label}, at the ceiling`;
  });

  protected readonly capHint = computed(() => {
    const { feeCap, feePerExecution } = this.value();
    if (feeCap === 0) return 'off, so the fee never changes';
    if (feePerExecution <= 0) return 'set a fee per execution first';
    if (feeCap < feePerExecution) return 'must be at least the fee, or zero';
    const multiple = (feeCap / feePerExecution).toFixed(1);
    // Not a worst case: a keeper with no competition is better off waiting for
    // the ceiling, so a creator should expect to pay it rather than hope not to.
    return `rises to ${multiple}× over one missed interval, so expect to pay it`;
  });

  // ---- The Test button ---------------------------------------------------

  /** What a test would be a test of: the app, the target, and the exact call. */
  private readonly subject = computed(() => {
    const appId = this.arcron.appId();
    const callArgs = this.callArgs();
    const { targetApp } = this.value();
    if (appId === null || callArgs === null || targetApp <= 0) return null;
    return subjectOf(appId, targetApp, callArgs);
  });

  protected readonly canTest = computed(() => this.subject() !== null);

  protected readonly testBlocked = computed(() => {
    if (this.arcron.appId() === null) return 'Set a keeper app id before testing.';
    if (this.value().targetApp <= 0) return 'Enter the app id you want called.';
    return 'Fix the method signature before testing.';
  });

  /** The last answer, but only while it is still an answer about this call. */
  protected readonly report = computed(() => {
    const subject = this.subject();
    return subject === null ? null : this.test.resultFor(subject);
  });

  protected readonly claims = computed(() => {
    const { targetApp } = this.value();
    return (
      `App ${targetApp} has this method, it accepts a call arriving from the keeper app's own ` +
      `account, and it stays inside the opcode budget a single call gets. A real execution is ` +
      `handed more budget than this test allowed it, never less.`
    );
  });

  protected gradeLabel(key: string): string {
    switch (key) {
      case 'none':
        return 'resources: none';
      case 'servable':
        return 'resources: servable';
      case 'protocol-only':
        return 'resources: protocol only';
      default:
        return 'resources: never runs';
    }
  }

  protected composition(counts: {
    accounts: number;
    apps: number;
    assets: number;
    boxes: number;
  }): string {
    return (
      `A real execution would carry ${counts.accounts} account, ${counts.apps} app, ` +
      `${counts.assets} asset and ${counts.boxes} box references, two of which ` +
      `(the upkeep box and your app) Arcron always pays for.`
    );
  }

  protected refusalReason(kind: string | null): string {
    switch (kind) {
      case 'no-such-app':
        return 'There is no application with that id on this network. Check the app id.';
      case 'budget':
        return 'The call ran out of opcode budget. An Arcron execution grants more than this test did, so a call only just over the line may still run, but this one is over it.';
      case 'unavailable':
        return 'The call reached for a resource that could not be made available even with everything a keeper could attach.';
      default:
        return 'The target refused this call. Either it has no such method, or its own checks said no. Its words:';
    }
  }

  protected runTest(): void {
    const subject = this.subject();
    const callArgs = this.callArgs();
    if (subject === null || callArgs === null) return;
    void this.test.run(subject, callArgs);
  }

  // ---- What this costs, and whether it can be paid -----------------------

  private readonly mbr = computed(() => {
    const callArgs = this.callArgs();
    return callArgs === null ? null : BigInt(boxMbr(callArgs));
  });

  /**
   * The whole debit, not two thirds of it.
   *
   * The old quote was the box MBR plus the funding, and `register` builds a
   * three transaction group on unmodified suggested params, so it was short by
   * exactly those three fees on every configuration of the form: it said
   * 0.0741 and 0.0771 left the account.
   */
  protected readonly cost = computed(() => {
    const callArgs = this.callArgs();
    if (callArgs === null) return null;
    return registrationCost({
      callArgs,
      funding: BigInt(Math.round(this.value().funding * 1e6)),
      minFee: this.payer.minFee(),
    });
  });

  protected readonly totalLabel = computed(() => {
    const cost = this.cost();
    return cost === null ? '-' : algos(cost.total);
  });

  protected readonly pricedNote = computed(() => {
    if (this.mbr() === null) return 'fix the signature to price the box';
    return "Everything that leaves your account if you sign, at this node's current fee.";
  });

  private readonly afford = computed(() => {
    const cost = this.cost();
    if (cost === null) return null;
    return affordability(cost.total, this.payer.spendable());
  });

  protected readonly balanceLine = computed(() => {
    if (!this.keeper.canSign()) return 'Connect an account to see whether it can cover this.';
    const state = this.afford();
    if (state === null) return 'Fix the signature before this can be priced.';
    if (state.state === 'unknown') {
      const error = this.payer.error();
      return error === null
        ? 'Reading what this account can spend…'
        : `This account's balance could not be read, so nothing will be sent: ${error}`;
    }
    const held = `This account can spend ${algos(state.spendable)}`;
    return state.state === 'enough'
      ? `${held}, leaving ${algos(state.left)} after this.`
      : `${held}, which is ${algos(state.shortfall)} short of this.`;
  });

  protected readonly canSubmit = computed(
    () =>
      this.keeper.canSign() &&
      this.status() === 'VALID' &&
      this.callArgs() !== null &&
      this.keeper.busy() === null &&
      // Nothing on the write path used to ask whether the read path was
      // working. A failed read leaves every warning on the page unrendered
      // and the last-good figures still on screen, which is the moment this
      // button should be least available rather than most.
      this.arcron.canWrite() &&
      // Blocking rather than warning, for the same reason as the line above:
      // an unread balance is not evidence that there is one. There is a
      // re-check control beside the figure so a funded account is never stuck.
      this.afford()?.state === 'enough' &&
      this.value().attested,
  );

  /** The specific reason the form will not submit, when there is one. */
  protected readonly problem = computed(() => {
    this.status();
    const errors = this.form.errors;
    if (errors?.['capBelowFee']) {
      return 'A fee ceiling must be at least the fee per execution, or zero for no escalation.';
    }
    if (errors?.['bonusOfNothing']) {
      return 'A bonus asset needs a bonus per run. Set the asset id to zero to drop it.';
    }
    const built = this.encoded();
    if ('error' in built) return built.error;
    if (errors?.['fundingBelowWorstCase']) {
      const { feePerExecution, feeCap } = this.value();
      const worst = Math.max(feePerExecution, feeCap);
      return `Funding must cover ${worst} ALGO, the price this upkeep can be charged for one execution.`;
    }
    if (this.status() !== 'VALID') {
      return 'Check the highlighted fields. Every value has an on-chain minimum.';
    }
    const afford = this.afford();
    const cost = this.cost();
    if (afford?.state === 'short' && cost !== null) {
      return (
        `This costs ${algos(cost.total)} and the connected account can spend ` +
        `${algos(afford.spendable)}, which is ${algos(afford.shortfall)} short. An account ` +
        `also has to keep its own minimum balance on top of what it spends.`
      );
    }
    if (afford?.state === 'unknown') {
      return (
        "The connected account's balance has not been read, so the console cannot tell " +
        'whether you can afford this. Re-check it beside the cost.'
      );
    }
    if (!this.value().attested) {
      return 'Confirm that you have tested this call against your own app.';
    }
    if (!this.arcron.canWrite()) {
      return 'The console cannot currently read this app, so nothing will be sent until it recovers.';
    }
    return null;
  });

  protected algo(microAlgo: bigint): string {
    return algos(microAlgo);
  }

  protected recheck(): void {
    void this.payer.refresh();
  }

  protected useCadence(seconds: number): void {
    const rounds = Math.max(MIN_INTERVAL_ROUNDS, Math.round(seconds / this.pace()));
    this.form.controls.intervalRounds.setValue(rounds);
  }

  protected async submit(): Promise<void> {
    const callArgs = this.callArgs();
    if (!this.canSubmit() || callArgs === null) return;
    const { targetApp, intervalRounds, feePerExecution, funding, policy, feeCap, feeAsset, assetFee } =
      this.form.getRawValue();
    const upkeepId = await this.keeper.register({
      targetApp,
      callArgs,
      intervalRounds,
      feePerExecution: Math.round(feePerExecution * 1e6),
      funding: Math.round(funding * 1e6),
      policy,
      feeCap: Math.round(feeCap * 1e6),
      feeAsset,
      assetFee,
    });
    // Null means the call did not land, and `KeeperService` has already put
    // the reason on screen. Navigating away from a failure would take the
    // only explanation with it.
    if (upkeepId !== null) this.registered.emit(upkeepId);
  }
}
