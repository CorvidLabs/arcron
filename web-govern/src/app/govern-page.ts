import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import algosdk from 'algosdk';

import { GovernWallet } from './wallet';
import {
  FREEZE_BENEFIT,
  FREEZE_CONSEQUENCE,
  confirmationMatches,
  describeState,
  whyCannotFreeze,
  type FreezeState,
  type GovernState,
} from './core/govern-state';

/** Public nodes, so this app needs no configuration to be useful. */
const NODES = {
  testnet: 'https://testnet-api.4160.nodely.dev',
  mainnet: 'https://mainnet-api.4160.nodely.dev',
} as const;

/**
 * Freezing a deployment, from the creator's own machine.
 *
 * This is not part of the console and must never be. The console is published
 * at one canonical address, and that address is a security property: the
 * contract is permissionless, so the address is the only thing separating our
 * front end from a copy. A page whose whole purpose is to authorize permanent
 * changes to a contract holding other people's money does not belong on the
 * public internet, where a convincing clone costs an afternoon.
 *
 * So it runs locally, and it is the one page here that can reach MainNet.
 *
 * Only `freeze` is offered. `update` replaces the programs, a browser cannot
 * compile Algorand Python, and a page handed program bytes it cannot verify is
 * worse than no page. That stays on the command line where `verify_build`
 * rebuilds from source.
 */
@Component({
  selector: 'govern-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormsModule],
  template: `
    <header>
      <h1>Arcron governance</h1>
      <p class="lede">
        Local only. This page is never served from corvidlabs.xyz, because a page that
        authorizes permanent changes to a live contract has no business on the public
        internet.
      </p>
    </header>

    <section class="target">
      <h2>Deployment</h2>
      <div class="row">
        <label>
          <span>Network</span>
          <select [value]="network()" (change)="pick($any($event.target).value)">
            <option value="testnet">TestNet</option>
            <option value="mainnet">MainNet</option>
          </select>
        </label>
        <label>
          <span>App id</span>
          <input type="text" inputmode="numeric" [(ngModel)]="appIdText" spellcheck="false" />
        </label>
        <button type="button" (click)="read()" [disabled]="loading()">
          {{ loading() ? 'Reading…' : 'Read it' }}
        </button>
      </div>
      @if (network() === 'mainnet') {
        <p class="warn">MainNet. Everything on this page is permanent there.</p>
      }
    </section>

    @if (state(); as current) {
      <section class="state">
        <h2>What is deployed</h2>
        <dl>
          <div><dt>Creator</dt><dd class="mono break">{{ current.creator }}</dd></div>
          <div><dt>Programs</dt><dd>{{ current.approvalBytes }} + {{ current.clearBytes }} bytes</dd></div>
          <div><dt>Digest</dt><dd class="mono break">{{ current.digest }}</dd></div>
          <div><dt>State</dt><dd [class.warn]="current.freeze === 'upgradeable'">{{ describe() }}</dd></div>
        </dl>
        <p class="note">
          Check that digest against <code>poetry run python -m scripts.verify_build</code>
          on the commit you expect, before doing anything else.
        </p>
      </section>

      <section class="freeze">
        <h2>Freeze</h2>
        <div class="columns">
          <div>
            <h3>What it costs</h3>
            <ul>@for (line of consequence; track line) {<li>{{ line }}</li>}</ul>
          </div>
          <div>
            <h3>What it is worth</h3>
            <ul>@for (line of benefit; track line) {<li>{{ line }}</li>}</ul>
          </div>
        </div>

        @if (blocked(); as reason) {
          <p class="blocked">{{ reason }}</p>
          @if (wallet.address() === null) {
            <button type="button" (click)="wallet.connect()" [disabled]="wallet.connecting()">
              {{ wallet.connecting() ? 'Waiting for Pera…' : 'Connect Pera' }}
            </button>
          }
        } @else {
          <label class="confirm">
            <span>Type the app id, {{ current.appId }}, to confirm</span>
            <input type="text" inputmode="numeric" [(ngModel)]="typed" [disabled]="busy()" />
          </label>
          <button type="button" class="danger" [disabled]="!confirmed() || busy()" (click)="freeze()">
            {{ busy() ? 'Approve it in Pera…' : 'Freeze permanently' }}
          </button>
        }

        @if (wallet.address(); as connected) {
          <p class="who">Signing as <span class="mono">{{ connected }}</span></p>
        }
        @if (wallet.error(); as failure) {<p class="blocked">{{ failure }}</p>}
        @if (outcome(); as message) {<p class="outcome" role="status">{{ message }}</p>}
      </section>
    }

    <section class="not-here">
      <h2>Not here</h2>
      <p>
        <strong>Updating the programs.</strong> A browser cannot compile Algorand Python,
        so this page would be handed bytes it cannot check. Use
        <code>poetry run python -m scripts.govern update</code>.
      </p>
    </section>
  `,
  styles: `
    :host { display: block; max-width: 48rem; margin: 0 auto; padding: 1.5rem 1rem 4rem; display: grid; gap: 1.5rem; }
    h1 { font-size: 1.4rem; margin: 0; }
    h2 { font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.08em; margin: 0 0 0.7rem; opacity: 0.75; }
    h3 { font-size: 0.85rem; margin: 0 0 0.4rem; }
    .lede { margin: 0.35rem 0 0; line-height: 1.55; }
    section { border: 1px solid currentColor; border-radius: 0.4rem; padding: 1rem; }
    .row { display: flex; flex-wrap: wrap; gap: 0.75rem; align-items: end; }
    label { display: grid; gap: 0.25rem; font-size: 0.78rem; }
    input, select, button { font: inherit; padding: 0.5rem 0.7rem; min-height: 44px; }
    button { cursor: pointer; }
    button:disabled { cursor: not-allowed; opacity: 0.55; }
    dl { display: grid; gap: 0.45rem; margin: 0; }
    dl > div { display: grid; grid-template-columns: 6rem 1fr; gap: 0.75rem; }
    dt { font-size: 0.73rem; text-transform: uppercase; letter-spacing: 0.06em; opacity: 0.75; }
    dd { margin: 0; }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.85rem; }
    .break { overflow-wrap: anywhere; }
    .warn { font-weight: 600; }
    .note, .who { font-size: 0.85rem; opacity: 0.85; margin: 0.75rem 0 0; line-height: 1.5; }
    .columns { display: grid; gap: 1rem; grid-template-columns: 1fr; margin-bottom: 1rem; }
    @media (min-width: 40rem) { .columns { grid-template-columns: 1fr 1fr; } }
    ul { margin: 0; padding-left: 1.1rem; display: grid; gap: 0.35rem; font-size: 0.9rem; line-height: 1.45; }
    .confirm { margin-bottom: 0.75rem; max-width: 18rem; }
    .danger { font-weight: 600; }
    .blocked { margin: 0.5rem 0 0; font-size: 0.9rem; line-height: 1.5; }
    .outcome { margin: 1rem 0 0; font-weight: 600; line-height: 1.5; }
  `,
})
export class GovernPage {
  protected readonly wallet = inject(GovernWallet);
  protected readonly consequence = FREEZE_CONSEQUENCE;
  protected readonly benefit = FREEZE_BENEFIT;

  protected readonly network = signal<'testnet' | 'mainnet'>('testnet');
  protected readonly appIdText = signal('769891898');
  protected readonly state = signal<GovernState | null>(null);
  protected readonly typed = signal('');
  protected readonly loading = signal(false);
  protected readonly busy = signal(false);
  protected readonly outcome = signal<string | null>(null);

  protected readonly describe = computed(() => describeState(this.state()));
  protected readonly blocked = computed(() =>
    whyCannotFreeze(this.state(), this.wallet.address()),
  );
  protected readonly confirmed = computed(() => {
    const current = this.state();
    return current !== null && confirmationMatches(this.typed(), current.appId);
  });

  private algod(): algosdk.Algodv2 {
    return new algosdk.Algodv2('', NODES[this.network()], '');
  }

  protected async pick(network: string): Promise<void> {
    this.network.set(network === 'mainnet' ? 'mainnet' : 'testnet');
    this.state.set(null);
    this.outcome.set(null);
    await this.wallet.useNetwork(this.network());
  }

  protected async read(): Promise<void> {
    const appId = Number(this.appIdText().trim());
    if (!Number.isFinite(appId) || appId <= 0) {
      this.outcome.set('That is not an app id.');
      return;
    }
    this.loading.set(true);
    this.outcome.set(null);
    try {
      const info = await this.algod().getApplicationByID(appId).do();
      const params = info.params;
      if (params === undefined) throw new Error('No parameters on this network.');
      const approval = params.approvalProgram ?? new Uint8Array();
      const clear = params.clearStateProgram ?? new Uint8Array();
      const flag = (params.globalState ?? []).find(
        (entry) => new TextDecoder().decode(entry.key) === 'frozen',
      );
      const freeze: FreezeState =
        flag === undefined ? 'absent' : Number(flag.value.uint) === 1 ? 'frozen' : 'upgradeable';

      this.state.set({
        appId: BigInt(appId),
        creator: params.creator.toString(),
        freeze,
        digest: await combined(approval, clear),
        approvalBytes: approval.length,
        clearBytes: clear.length,
      });
    } catch (cause) {
      this.state.set(null);
      this.outcome.set(
        `Could not read it: ${cause instanceof Error ? cause.message : String(cause)}`,
      );
    } finally {
      this.loading.set(false);
    }
  }

  protected async freeze(): Promise<void> {
    const current = this.state();
    const signing = this.wallet.signing();
    if (current === null || signing === null || !this.confirmed()) return;

    this.busy.set(true);
    this.outcome.set(null);
    try {
      const algod = this.algod();
      const composer = new algosdk.AtomicTransactionComposer();
      composer.addMethodCall({
        appID: Number(current.appId),
        method: algosdk.ABIMethod.fromSignature('freeze()void'),
        sender: signing.sender,
        signer: signing.signer,
        suggestedParams: await algod.getTransactionParams().do(),
      });
      const result = await composer.execute(algod, 4);
      this.outcome.set(
        `Frozen in round ${result.confirmedRound}. The programs can never be replaced ` +
          `again. Transaction ${result.txIDs[0]}.`,
      );
      this.typed.set('');
      await this.read();
    } catch (cause) {
      this.outcome.set(`Not frozen: ${cause instanceof Error ? cause.message : String(cause)}`);
    } finally {
      this.busy.set(false);
    }
  }
}

/** sha256 over approval, a zero byte, then clear. Same as `verify_build`. */
async function combined(approval: Uint8Array, clear: Uint8Array): Promise<string> {
  const joined = new Uint8Array(approval.length + 1 + clear.length);
  joined.set(approval, 0);
  joined[approval.length] = 0;
  joined.set(clear, approval.length + 1);
  const hash = await crypto.subtle.digest('SHA-256', joined as unknown as BufferSource);
  return Array.from(new Uint8Array(hash))
    .map((byte) => byte.toString(16).padStart(2, '0'))
    .join('');
}
