import { ChangeDetectionStrategy, Component, DestroyRef, inject, output, signal } from '@angular/core';

import { RainService, type CreateRainInput } from '../core/rain.service';
import { WalletService } from '../core/wallet.service';
import { CADENCES } from '@corvidlabs/arcron/rain';

/**
 * The holder-facing create form. Lives on `/rain/new`, not under the list:
 * the registry already taught us a form pinned below the table ends on itself.
 */
@Component({
  selector: 'arcron-rain-create-form',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="panel">
      <header>
        <h2>Open a rain</h2>
        <p class="subtitle">
          Anyone can. The box deposit is {{ rain.createCost() }}, paid to the
          app, not to a person. After that, fill the pot and forget it.
        </p>
      </header>

      <form (submit)="create($event)">
          <div class="grid">
            <label>
              <span class="eyebrow">Name</span>
              <input name="label" type="text" maxlength="32" placeholder="Corvid daily" />
              <small>up to 32 characters, stored on the rain box</small>
            </label>

            <label>
              <span class="eyebrow">Each fire</span>
              <input name="drip" type="number" min="0" step="any" value="0.05" />
              <small>ALGO, or base units if the prize is an ASA</small>
            </label>

            <fieldset class="policy">
              <legend class="eyebrow">Who it falls on</legend>
              <label class="choice">
                <input type="radio" name="mode" value="split" [checked]="mode() === 'split'" (change)="mode.set('split')" />
                <span>
                  <strong>Everyone who entered</strong>
                  <small>Fill-and-forget. Equal share of the drip, every fire.</small>
                </span>
              </label>
              <label class="choice">
                <input type="radio" name="mode" value="wave" [checked]="mode() === 'wave'" (change)="mode.set('wave')" />
                <span>
                  <strong>Who shows up</strong>
                  <small>Discord GM rain. First N to check in this drop split it.</small>
                </span>
              </label>
              <label class="choice">
                <input type="radio" name="mode" value="one" [checked]="mode() === 'one'" (change)="mode.set('one')" />
                <span>
                  <strong>One person</strong>
                  <small>One random ticket each fire, from that round’s block seed.</small>
                </span>
              </label>
              @if (mode() === 'wave') {
                <label>
                  <span class="eyebrow">Check-in cap</span>
                  <input name="waveCap" type="number" min="1" step="1" value="10" />
                  <small>How many people this drop, usually 10.</small>
                </label>
              }
            </fieldset>

            <fieldset class="policy">
              <legend class="eyebrow">Who may enter</legend>
              <label class="choice">
                <input type="radio" name="gate" value="corvid" [checked]="gate() === 'corvid'" (change)="gate.set('corvid')" />
                <span>
                  <strong>Corvid NFT holders</strong>
                  <small>TestNet Corvid minter. MainNet Nevermore does not count.</small>
                </span>
              </label>
              <label class="choice">
                <input type="radio" name="gate" value="open" [checked]="gate() === 'open'" (change)="gate.set('open')" />
                <span>
                  <strong>Anyone</strong>
                  <small>No collection gate.</small>
                </span>
              </label>
              <label class="choice">
                <input type="radio" name="gate" value="custom" [checked]="gate() === 'custom'" (change)="gate.set('custom')" />
                <span>
                  <strong>Another collection</strong>
                  <small>The account that minted it, not one asset id.</small>
                </span>
              </label>
              @if (gate() === 'custom') {
                <label>
                  <span class="eyebrow">Minter address</span>
                  <input name="gateCreator" type="text" spellcheck="false" placeholder="Minter address" />
                </label>
              }
            </fieldset>

            <fieldset class="policy">
              <legend class="eyebrow">Prize</legend>
              <label class="choice">
                <input type="radio" name="prize" value="algo" [checked]="prizeKind() === 'algo'" (change)="prizeKind.set('algo')" />
                <span>
                  <strong>ALGO</strong>
                  <small>The pot is TestNet ALGO.</small>
                </span>
              </label>
              <label class="choice">
                <input type="radio" name="prize" value="asa" [checked]="prizeKind() === 'asa'" (change)="prizeKind.set('asa')" />
                <span>
                  <strong>An ASA</strong>
                  <small>The hub opts in if it has not already. Claimers still have to opt in themselves. Entering costs ALGO for the ticket box either way.</small>
                </span>
              </label>
              @if (prizeKind() === 'asa') {
                <label>
                  <span class="eyebrow">Asset id</span>
                  <input name="prizeAsset" type="number" min="0" step="1" placeholder="Asset id" />
                  <small>The ASA id people will opt in to, shown on the rain.</small>
                </label>
              }
            </fieldset>
          </div>

          <div class="cadences">
            <span class="eyebrow">Cadence</span>
            @for (item of cadences; track item.label) {
              <button
                type="button"
                class="ghost small"
                [class.current]="cadence() === item.label"
                (click)="cadence.set(item.label)"
              >
                {{ item.label }}
              </button>
            }
          </div>

          <div class="submit">
            <button type="submit" class="primary" [disabled]="!wallet.connected() || rain.busy() !== null">
              {{ rain.busy() === 'create' ? 'Opening…' : 'Open this rain' }}
            </button>
            @if (!wallet.connected()) {
              <p class="hint">Connect an account above to open one.</p>
            } @else {
              <p class="hint">{{ rain.createCost() }} box deposit, then you fund the pot.</p>
            }
          </div>
        </form>

      @if (rain.writeError(); as message) {
        <p class="banner" role="alert">{{ message }}</p>
      }
    </section>
  `,
  styles: `
    .panel { display: grid; gap: 1.1rem; }
    header h2 { margin: 0; font-size: 1.1rem; }
    .subtitle { margin: 0.2rem 0 0; color: var(--text-faint); font-size: 0.85rem; max-width: 52ch; }
    form { display: grid; gap: 1rem; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(12.5rem, 1fr)); gap: 0.9rem; }
    label { display: grid; gap: 0.3rem; align-content: start; }
    small { color: var(--text-faint); font-size: 0.72rem; }
    .policy { grid-column: 1 / -1; display: grid; gap: 0.5rem; margin: 0; padding: 0.7rem 0.85rem; border: 1px solid var(--hairline); }
    .policy legend { padding: 0 0.35rem; }
    .choice { display: flex; align-items: start; gap: 0.55rem; }
    .choice input { margin-top: 0.25rem; }
    .choice span { display: grid; gap: 0.1rem; }
    .choice strong { font-size: 0.85rem; font-weight: 600; }
    @media (max-width: 480px) {
      input[type='radio'] {
        width: 24px;
        height: 24px;
        margin: 10px;
      }
    }
    .cadences { display: flex; flex-wrap: wrap; align-items: center; gap: 0.4rem; }
    .cadences .eyebrow { margin-right: 0.3rem; }
    .cadences .current { border-color: var(--sheen); color: var(--sheen); font-weight: 500; }
    .submit { display: flex; align-items: center; gap: 0.85rem; flex-wrap: wrap; }
    .hint { margin: 0; color: var(--text-faint); font-size: 0.8rem; max-width: 68ch; }
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
export class RainCreateForm {
  readonly opened = output<bigint>();

  protected readonly rain = inject(RainService);
  protected readonly wallet = inject(WalletService);
  protected readonly cadences = CADENCES;
  protected readonly cadence = signal<(typeof CADENCES)[number]['label']>('daily');
  protected readonly mode = signal<CreateRainInput['mode']>('split');
  protected readonly gate = signal<CreateRainInput['gate']>('corvid');
  protected readonly prizeKind = signal<CreateRainInput['prize']>('algo');

  constructor() {
    const stop = this.rain.watch();
    inject(DestroyRef).onDestroy(stop);
  }

  protected create(event: Event): void {
    event.preventDefault();
    if (!this.wallet.connected()) return;
    const form = event.target as HTMLFormElement;
    const value = (name: string): string => {
      const field = form.elements.namedItem(name);
      if (field instanceof HTMLInputElement || field instanceof HTMLSelectElement) return field.value;
      return '';
    };
    const mode = value('mode') as CreateRainInput['mode'];
    const gate = value('gate') as CreateRainInput['gate'];
    const prize = value('prize') as CreateRainInput['prize'];
    void this.rain
      .create({
        label: value('label'),
        gate,
        gateCreator: value('gateCreator'),
        prize,
        prizeAsset: Number(value('prizeAsset') || 0),
        drip: Number(value('drip') || 0),
        cadence: this.cadence(),
        mode: mode || 'split',
        waveCap: Number(value('waveCap') || 10),
      })
      .then((id) => {
        if (id === null) return;
        this.opened.emit(id);
      });
  }
}
