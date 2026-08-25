import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { map } from 'rxjs';

import { ArcronService } from '../core/arcron.service';
import { algos, duration, runwayLabel } from '@corvidlabs/arcron/format';
import { encodeCall, PULSE_TICK_SIGNATURE } from '@corvidlabs/arcron/keeper-abi';
import { KeeperService } from '../core/keeper.service';
import {
  boxMbr,
  CATCH_UP,
  MAX_CALL_ARGS,
  MAX_INTERVAL_ROUNDS,
  MAX_UPKEEP_FEE,
  MIN_INTERVAL_ROUNDS,
  MIN_UPKEEP_FEE,
  SKIP_AHEAD,
  toHex,
} from '@corvidlabs/arcron';

const CADENCES = [
  { label: '30 s', seconds: 30 },
  { label: '5 min', seconds: 300 },
  { label: '1 hour', seconds: 3_600 },
  { label: '1 day', seconds: 86_400 },
] as const;

@Component({
  selector: 'arcron-register-form',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ReactiveFormsModule],
  template: `
    <section class="panel">
      <header>
        <h2>Register an upkeep</h2>
        <p class="subtitle">
          Escrow ALGO to have any keeper call your app on a schedule. The call is a NoOp carrying
          one app arg, a method selector. A fee ceiling makes a neglected upkeep more attractive,
          though only competing keepers hold the price below it. Leave it at zero unless an upkeep
          is actually going unserviced.
        </p>
      </header>

      <form [formGroup]="form" (ngSubmit)="submit()">
        <div class="grid">
          <label>
            <span class="eyebrow">Target app id</span>
            <input type="number" formControlName="targetApp" required />
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
            <small>min {{ minFeeAlgo }} (keepers spend ~0.003 in group fees)</small>
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

          <div class="cost">
            <span class="eyebrow">Up-front cost</span>
            <strong class="mono">{{ upFront() }}</strong>
            <small>{{ mbrNote() }}</small>
          </div>
        </div>

        <div class="cadences">
          <span class="eyebrow">Quick cadence</span>
          @for (cadence of cadences; track cadence.label) {
            <button type="button" class="ghost small" (click)="useCadence(cadence.seconds)">
              every {{ cadence.label }}
            </button>
          }
        </div>

        <div class="submit">
          <button type="submit" class="primary" [disabled]="!canSubmit()">
            {{ keeper.busy() === 'register' ? 'Registering…' : 'Register upkeep' }}
          </button>
          @if (!keeper.canSign()) {
            <p class="hint">Connect an account to register.</p>
          } @else if (status() !== 'VALID') {
            <p class="hint">{{ problem() }}</p>
          }
        </div>
      </form>
    </section>
  `,
  styles: `
    .panel { display: grid; gap: 1.1rem; }
    header h2 { margin: 0; font-size: 1.1rem; }
    .subtitle { margin: 0.2rem 0 0; color: var(--text-faint); font-size: 0.85rem; max-width: 52ch; }
    form { display: grid; gap: 1rem; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(12.5rem, 1fr)); gap: 0.9rem; }
    textarea { font-family: var(--font-mono); font-size: 0.8rem; resize: vertical; }
    label, .cost { display: grid; gap: 0.3rem; align-content: start; }
    small { color: var(--text-faint); font-size: 0.72rem; }
    .cost strong { font-size: 1.1rem; }
    .policy { grid-column: 1 / -1; display: grid; gap: 0.5rem; margin: 0; padding: 0.7rem 0.85rem; border: 1px solid var(--hairline); }
    .policy legend { padding: 0 0.35rem; }
    .choice { display: flex; align-items: start; gap: 0.55rem; }
    .choice input { margin-top: 0.25rem; }
    .choice span { display: grid; gap: 0.1rem; }
    .choice strong { font-size: 0.85rem; font-weight: 600; }
    .cadences { display: flex; flex-wrap: wrap; align-items: center; gap: 0.4rem; }
    .cadences .eyebrow { margin-right: 0.3rem; }
    .submit { display: flex; align-items: center; gap: 0.85rem; flex-wrap: wrap; }
    .hint { margin: 0; color: var(--text-faint); font-size: 0.8rem; }
    input.ng-invalid.ng-touched { border-color: var(--danger); }
  `,
})
export class RegisterForm {
  protected readonly keeper = inject(KeeperService);
  private readonly arcron = inject(ArcronService);
  private readonly builder = inject(FormBuilder);

  protected readonly minInterval = MIN_INTERVAL_ROUNDS;
  protected readonly minFeeAlgo = MIN_UPKEEP_FEE / 1e6;
  protected readonly cadences = CADENCES;
  protected readonly catchUp = Number(CATCH_UP);
  protected readonly skipAhead = Number(SKIP_AHEAD);

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
      MIN_UPKEEP_FEE / 1e6,
      [Validators.required, Validators.min(MIN_UPKEEP_FEE / 1e6), Validators.max(MAX_UPKEEP_FEE / 1e6)],
    ],
    funding: [(MIN_UPKEEP_FEE * 3) / 1e6, [Validators.required, Validators.min(MIN_UPKEEP_FEE / 1e6)]],
    // The encoding keeps CATCH_UP as zero so nothing registered before means
    // something new. The form defaults the other way, because "only the
    // latest run matters" is the commoner shape and the safer mistake.
    policy: [Number(SKIP_AHEAD), Validators.required],
    // Zero is off: the fee never moves. Opt in, rather than surprising a
    // creator with an escrow that drains three times faster than the headline.
    feeCap: [0, [Validators.required, Validators.min(0), Validators.max(MAX_UPKEEP_FEE / 1e6)]],
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
    return 'Check the highlighted fields. Every value has an on-chain minimum.';
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

  private readonly mbr = computed(() => {
    const callArgs = this.callArgs();
    return callArgs === null ? null : BigInt(boxMbr(callArgs));
  });

  protected readonly upFront = computed(() => {
    const mbr = this.mbr();
    if (mbr === null) return '-';
    return algos(mbr + BigInt(Math.round(this.value().funding * 1e6)));
  });

  protected readonly mbrNote = computed(() => {
    const mbr = this.mbr();
    if (mbr === null) return 'fix the signature to price the box';
    return `${algos(mbr)} box MBR, refunded in full on cancel`;
  });

  protected readonly canSubmit = computed(
    () =>
      this.keeper.canSign() &&
      this.status() === 'VALID' &&
      this.callArgs() !== null &&
      this.keeper.busy() === null,
  );

  protected useCadence(seconds: number): void {
    const rounds = Math.max(MIN_INTERVAL_ROUNDS, Math.round(seconds / this.pace()));
    this.form.controls.intervalRounds.setValue(rounds);
  }

  protected submit(): void {
    const callArgs = this.callArgs();
    if (!this.canSubmit() || callArgs === null) return;
    const { targetApp, intervalRounds, feePerExecution, funding, policy, feeCap, feeAsset, assetFee } =
      this.form.getRawValue();
    void this.keeper.register({
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
  }
}
