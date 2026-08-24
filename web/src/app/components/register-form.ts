import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { map } from 'rxjs';

import { ArchonService } from '../core/archon.service';
import { algos, duration, microAlgos, runwayLabel } from '../core/format';
import { methodSelector, PULSE_TICK_SIGNATURE } from '../core/keeper-abi';
import { KeeperService } from '../core/keeper.service';
import { boxMbr, CATCH_UP, MIN_INTERVAL_ROUNDS, MIN_UPKEEP_FEE, SKIP_AHEAD, toHex } from '../core/upkeep';

const CADENCES = [
  { label: '30 s', seconds: 30 },
  { label: '5 min', seconds: 300 },
  { label: '1 hour', seconds: 3_600 },
  { label: '1 day', seconds: 86_400 },
] as const;

@Component({
  selector: 'archon-register-form',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ReactiveFormsModule],
  template: `
    <section class="panel">
      <header>
        <h2>Register an upkeep</h2>
        <p class="subtitle">
          Escrow ALGO to have any keeper call your app on a schedule. The call is a NoOp with one
          app arg — a method selector.
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
            <small class="mono">selector {{ selector() }}</small>
          </label>

          <label>
            <span class="eyebrow">Interval (rounds)</span>
            <input type="number" formControlName="intervalRounds" [min]="minInterval" />
            <small>{{ cadenceHint() }}</small>
          </label>

          <label>
            <span class="eyebrow">Fee per execution (ALGO)</span>
            <input type="number" step="0.001" formControlName="feePerExecution" />
            <small>min {{ minFeeAlgo }} — keepers spend ~0.003 in group fees</small>
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
            <p class="hint">Check the highlighted fields — every value has an on-chain minimum.</p>
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
  private readonly archon = inject(ArchonService);
  private readonly builder = inject(FormBuilder);

  protected readonly minInterval = MIN_INTERVAL_ROUNDS;
  protected readonly minFeeAlgo = MIN_UPKEEP_FEE / 1e6;
  protected readonly cadences = CADENCES;
  protected readonly catchUp = Number(CATCH_UP);
  protected readonly skipAhead = Number(SKIP_AHEAD);

  protected readonly form = this.builder.nonNullable.group({
    targetApp: [0, [Validators.required, Validators.min(1)]],
    signature: [PULSE_TICK_SIGNATURE, Validators.required],
    intervalRounds: [MIN_INTERVAL_ROUNDS, [Validators.required, Validators.min(MIN_INTERVAL_ROUNDS)]],
    feePerExecution: [MIN_UPKEEP_FEE / 1e6, [Validators.required, Validators.min(MIN_UPKEEP_FEE / 1e6)]],
    funding: [(MIN_UPKEEP_FEE * 3) / 1e6, [Validators.required, Validators.min(MIN_UPKEEP_FEE / 1e6)]],
    // The encoding keeps CATCH_UP as zero so nothing registered before means
    // something new. The form defaults the other way, because "only the
    // latest run matters" is the commoner shape and the safer mistake.
    policy: [Number(SKIP_AHEAD), Validators.required],
    // Zero is off: the fee never moves. Opt in, rather than surprising a
    // creator with an escrow that drains three times faster than the headline.
    feeCap: [0, [Validators.required, Validators.min(0)]],
  });

  /**
   * Reactive-forms state is not signal-based, so validity has to be pulled
   * into a signal — a computed() reading `form.valid` directly would cache
   * the first answer and the submit button would never enable.
   */
  protected readonly status = toSignal(this.form.statusChanges, { initialValue: this.form.status });

  private readonly value = toSignal(
    this.form.valueChanges.pipe(map(() => this.form.getRawValue())),
    { initialValue: this.form.getRawValue() },
  );

  /** Measured pace where we have it; Algorand's nominal block time otherwise. */
  private readonly pace = computed(() => this.archon.secondsPerRound());

  private readonly callData = computed(() => {
    try {
      return methodSelector(this.value().signature);
    } catch {
      return null;
    }
  });

  protected readonly selector = computed(() => {
    const callData = this.callData();
    return callData === null ? 'invalid signature' : `0x${toHex(callData)}`;
  });

  protected readonly cadenceHint = computed(() => {
    const { intervalRounds } = this.value();
    if (intervalRounds < MIN_INTERVAL_ROUNDS) return `minimum ${MIN_INTERVAL_ROUNDS} rounds`;
    const basis = this.archon.paceSource() === 'measured' ? 'measured' : 'nominal';
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
    if (feeCap === 0) return 'off — the fee never changes';
    if (feeCap < feePerExecution) return 'must be at least the fee, or zero';
    const multiple = (feeCap / feePerExecution).toFixed(1);
    return `rises to ${multiple}× over one missed interval, then holds`;
  });

  private readonly mbr = computed(() => {
    const callData = this.callData();
    return callData === null ? null : BigInt(boxMbr(callData.length));
  });

  protected readonly upFront = computed(() => {
    const mbr = this.mbr();
    if (mbr === null) return '—';
    return algos(mbr + BigInt(Math.round(this.value().funding * 1e6)));
  });

  protected readonly mbrNote = computed(() => {
    const mbr = this.mbr();
    if (mbr === null) return 'fix the signature to price the box';
    return `${algos(mbr)} box MBR (${microAlgos(mbr)}) — refunded in full on cancel`;
  });

  protected readonly canSubmit = computed(
    () => this.keeper.canSign() && this.status() === 'VALID' && this.keeper.busy() === null,
  );

  protected useCadence(seconds: number): void {
    const rounds = Math.max(MIN_INTERVAL_ROUNDS, Math.round(seconds / this.pace()));
    this.form.controls.intervalRounds.setValue(rounds);
  }

  protected submit(): void {
    const callData = this.callData();
    if (!this.canSubmit() || callData === null) return;
    const { targetApp, intervalRounds, feePerExecution, funding, policy, feeCap } =
      this.form.getRawValue();
    void this.keeper.register({
      targetApp,
      callData,
      intervalRounds,
      feePerExecution: Math.round(feePerExecution * 1e6),
      funding: Math.round(funding * 1e6),
      policy,
      feeCap: Math.round(feeCap * 1e6),
    });
  }
}
