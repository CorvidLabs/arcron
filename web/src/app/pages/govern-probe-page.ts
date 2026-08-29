import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import algosdk from 'algosdk';

import { WalletService } from '../core/wallet.service';
import { ArcronService } from '../core/arcron.service';
import { classify, describeResult, summariseProbe, type ProbeResult } from '../core/govern-probe';
import { extractSignature } from '../core/govern-sign';
import { signMultisigWithPera } from '../core/pera-multisig';

/** The live 2 of 3 that owns MainNet deployments, from scripts/network.py. */
const MEMBERS = [
  'X2OF75PUW34XMTY2QW7ZTXH2XHDREVH4ZRDDHFXJNJHXJEEPSWWB4T73AQ',
  'WGSHC4TYKYBS6EX5V5E377BQDLKWIIPBCFOLZQZIXCKHFIEKRPBFOMW25A',
  'DEXWEZGRX3Q6B2S3GVO74MUN54XA3JI5GQFVGNK64JYPD4NCFRK4G5ACVY',
];
const THRESHOLD = 2;

/**
 * Does any wallet sign a transaction whose sender is a multisig?
 *
 * Everything else in the governance work is done and tested. This is the one
 * question that cannot be answered from a desk, and reading type definitions is
 * how it was got wrong once already: `@txnlab/use-wallet` exports
 * `MultisigMetadata` for ARC-1's `msig` field and no adapter implements it, so
 * the wallet is never told this is a multisig. It is asked to sign a
 * transaction whose sender is an address it does not hold, and wallets
 * generally check exactly that.
 *
 * **The transaction is a zero ALGO self-payment from the multisig.** It answers
 * the same question as a real governance transaction and cannot do anything if
 * it somehow escaped this page. A probe that risks a live contract to learn
 * something about a wallet would be the wrong trade.
 *
 * Nothing here is submitted. The signature, if one comes back, is measured and
 * discarded.
 */
@Component({
  selector: 'arcron-govern-probe-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <h1>Will a wallet sign for a multisig?</h1>

    <p class="lede">
      This page answers one question, and the answer decides how governance works
      for the rest of this project. It is a diagnostic, not a feature: nothing here
      is submitted and nothing changes.
    </p>

    <section class="why">
      <h2>Why this question matters</h2>
      <p>
        MainNet deployments are owned by a <strong>2 of 3 multisig</strong>. Replacing
        the contract's programs, or freezing them, needs two of the three holders to
        sign the same transaction.
      </p>
      <p>
        Today the only way to do that is <code>govern sign</code> on the command line,
        which reads a mnemonic from an environment variable. That is acceptable for a
        paper account and <strong>wrong for a Ledger</strong>, whose entire purpose is
        that the key never leaves the device. Nobody should be typing a hardware
        wallet's recovery phrase into a shell.
      </p>
      <p>
        A wallet could sign instead. ARC-1 even defines a <code>msig</code> field for
        exactly this. <strong>No wallet adapter implements it</strong>, so the wallet
        cannot be told it is signing for a multisig. It can only be handed the
        transaction and asked, and the transaction's sender is an address the holder
        does not hold. Most wallets refuse that on principle.
      </p>
      <p class="note">
        Whether <em>any</em> wallet does it anyway cannot be looked up. It has to be
        tried, which is what this page is.
      </p>
    </section>

    <section class="flow">
      <h2>Where this sits</h2>
      <ol>
        <li>
          Someone runs <code>govern update</code> or <code>govern freeze</code>, which
          writes an unsigned transaction to a file. No key needed.
        </li>
        <li>
          The file goes to each holder. <strong>They read it</strong>, compare its
          digest against their own <code>verify_build</code>, and sign.
          <em>This is the step in question.</em>
        </li>
        <li>Once two signatures are on it, anyone submits it. No key needed.</li>
      </ol>
      <p class="note">
        Step 2 is where a mnemonic is required today. If a wallet will sign, that
        stops being true and the Ledger holder is served properly. If no wallet will,
        the command line stays and the Ledger needs a different answer entirely.
      </p>
    </section>

    <section class="connect">
      <h2>Connect a member</h2>
      @if (wallet.connected()) {
        <p>
          Connected as <span class="mono">{{ short(wallet.activeAddress()) }}</span>
          @if (isMember()) {
            <span class="ok">a member of this multisig</span>
          } @else {
            <span class="warn">not a member, so nothing here would ever count</span>
          }
        </p>
        <button type="button" (click)="wallet.disconnect()">Disconnect</button>
      } @else {
        <div class="wallets">
          @for (option of wallet.wallets(); track option.id) {
            <button type="button" (click)="connect(option.id)">{{ option.name }}</button>
          }
        </div>
      }
    </section>

    <section class="run">
      <h2>Ask it to sign</h2>
      <button
        type="button"
        class="primary"
        [disabled]="!isMember() || busy()"
        (click)="probe()"
      >
        {{ busy() ? 'Waiting for the wallet…' : 'Sign the probe transaction' }}
      </button>
      @if (!isMember()) {
        <p class="note">Connect one of the three member accounts first.</p>
      }
    </section>

    @if (results().length) {
      <section class="results">
        <h2>What each wallet said</h2>
        <ul>
          @for (result of results(); track result.wallet + result.address) {
            <li [class]="result.outcome">
              <strong>{{ result.outcome }}</strong> {{ line(result) }}
            </li>
          }
        </ul>
        <p class="verdict">{{ verdict() }}</p>
        <button type="button" (click)="copy()">Copy this as a report</button>
      </section>
    }
  `,
  styles: `
    :host { display: grid; gap: 1.75rem; align-content: start; max-width: 46rem; }
    h1 { font-size: 1.4rem; margin: 0; }
    h2 { font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.08em; margin: 0 0 0.6rem; opacity: 0.75; }
    .lede { margin: 0; line-height: 1.55; }
    section { border: 1px solid currentColor; border-radius: 0.4rem; padding: 1rem; }
    section p { margin: 0 0 0.7rem; line-height: 1.55; }
    section p:last-of-type { margin-bottom: 0; }
    ol { margin: 0; padding-left: 1.2rem; display: grid; gap: 0.5rem; line-height: 1.5; }
    ol code { font-size: 0.9em; }
    dl { display: grid; gap: 0.4rem; margin: 0; }
    dl > div { display: grid; grid-template-columns: 7rem 1fr; gap: 0.75rem; }
    dt { font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.06em; opacity: 0.75; }
    dd { margin: 0; overflow-wrap: anywhere; }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.85rem; }
    .note { font-size: 0.85rem; opacity: 0.8; margin: 0.75rem 0 0; line-height: 1.5; }
    .wallets { display: flex; flex-wrap: wrap; gap: 0.5rem; }
    button { font: inherit; padding: 0.5rem 0.9rem; min-height: 44px; cursor: pointer; }
    button:disabled { cursor: not-allowed; opacity: 0.55; }
    .primary { font-weight: 600; }
    .ok::before { content: ' — '; }
    .warn::before { content: ' — '; }
    .warn { font-weight: 600; }
    ul { list-style: none; padding: 0; margin: 0; display: grid; gap: 0.5rem; }
    li { font-size: 0.9rem; line-height: 1.5; }
    li strong { text-transform: uppercase; font-size: 0.7rem; letter-spacing: 0.08em; }
    .verdict { margin: 1rem 0 0.75rem; font-weight: 600; line-height: 1.5; }
  `,
})
export class GovernProbePage {
  protected readonly wallet = inject(WalletService);
  private readonly arcron = inject(ArcronService);

  protected readonly MEMBERS = MEMBERS;
  protected readonly THRESHOLD = THRESHOLD;

  protected readonly busy = signal(false);
  protected readonly results = signal<ProbeResult[]>([]);

  protected readonly multisig = computed(() =>
    algosdk.multisigAddress({ version: 1, threshold: THRESHOLD, addrs: MEMBERS }).toString(),
  );

  protected readonly isMember = computed(() => {
    const address = this.wallet.activeAddress();
    return address !== null && MEMBERS.includes(address);
  });

  protected readonly verdict = computed(() => summariseProbe(this.results()).verdict);

  protected short(address: string | null): string {
    return address ? `${address.slice(0, 8)}…${address.slice(-4)}` : '';
  }

  protected line(result: ProbeResult): string {
    return describeResult(result);
  }

  protected async connect(id: string): Promise<void> {
    await this.wallet.connect(id);
  }

  /**
   * Build the probe transaction and ask the connected wallet to sign it.
   *
   * The sender is the multisig, which is the entire point: a member does not
   * hold that address, and whether a wallet signs anyway is what this measures.
   */
  protected async probe(): Promise<void> {
    const address = this.wallet.activeAddress();
    if (address === null) return;
    this.busy.set(true);

    const name = this.wallet.activeWallet()?.name ?? 'unknown wallet';
    try {
      const params = await this.arcron.algod().getTransactionParams().do();
      const txn = algosdk.makePaymentTxnWithSuggestedParamsFromObject({
        sender: this.multisig(),
        receiver: this.multisig(),
        amount: 0,
        suggestedParams: params,
        note: new TextEncoder().encode('arcron multisig signing probe, never submitted'),
      });

      // `signing()` hands back the standard TransactionSigner, which is what
      // the console uses everywhere else. It signs as the active account; the
      // transaction's sender is the multisig, and whether the wallet minds is
      // the thing being measured.
      // Straight to Pera, not through use-wallet. Its adapter tags any
      // transaction whose sender it does not hold with `signers: []`, which
      // Pera documents as "skip this one", so the wallet is never asked. Pera's
      // own SDK takes `msig` metadata and will try.
      if ((this.wallet.activeWallet()?.id ?? '').toLowerCase().includes('pera')) {
        const attempt = await signMultisigWithPera(txn, address, {
          version: 1,
          threshold: THRESHOLD,
          addrs: MEMBERS,
        });
        this.record({
          wallet: name,
          address,
          outcome: attempt.outcome,
          detail: attempt.detail,
          signature: attempt.signature,
        });
        return;
      }

      const signing = this.wallet.signing();
      if (signing === null) throw new Error('No wallet is connected.');
      const signed = await signing.signer([txn], [0]);
      const first = signed[0];
      if (!first) {
        // Not a refusal. The adapter checks `addresses.includes(txn.sender)`
        // and tags anything else `signers: []`, which tells the wallet not to
        // sign it. Our sender is the multisig, which no member holds, so the
        // wallet was never asked and cannot have refused.
        this.record({
          wallet: name,
          address,
          outcome: 'not-asked',
          detail:
            'The adapter filtered this out before the wallet saw it, because the ' +
            'sender is not an address this wallet holds.',
          signature: null,
        });
        return;
      }
      const signature = extractSignature(first);
      this.record({ wallet: name, address, outcome: 'signed', detail: null, signature });
    } catch (cause) {
      const { outcome, detail } = classify(cause);
      this.record({ wallet: name, address, outcome, detail, signature: null });
    } finally {
      this.busy.set(false);
    }
  }

  private record(result: ProbeResult): void {
    this.results.update((all) => [...all.filter((r) => r.wallet !== result.wallet), result]);
  }

  protected copy(): void {
    const found = summariseProbe(this.results());
    const text = [
      'Arcron multisig signing probe',
      `multisig ${this.multisig()}`,
      '',
      ...this.results().map((r) => `- ${describeResult(r)}`),
      '',
      found.verdict,
    ].join('\n');
    void navigator.clipboard?.writeText(text);
  }
}
