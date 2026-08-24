import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';

import { ArchonService } from '../core/archon.service';
import { algos, shortAddress } from '../core/format';
import { KmdService } from '../core/kmd.service';

@Component({
  selector: 'archon-signer-bar',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="signer">
      @if (!kmd.supported()) {
        <p class="note">
          <strong>Read-only.</strong> TestNet signing needs a wallet adapter; LocalNet signs
          through KMD. Switch to LocalNet to register, execute or cancel upkeeps.
        </p>
      } @else if (!kmd.connected()) {
        <button type="button" class="primary" [disabled]="kmd.connecting()" (click)="connect()">
          {{ kmd.connecting() ? 'Connecting…' : 'Connect LocalNet account' }}
        </button>
        <p class="note">Keys stay in KMD — transactions are sent there to be signed.</p>
      } @else {
        <label class="account">
          <span class="sr-only">Signing account</span>
          <select (change)="use($event)">
            @for (account of kmd.accounts(); track account.address) {
              <option [value]="account.address" [selected]="account.address === kmd.activeAddress()">
                {{ account.walletName }} · {{ label(account.address) }} · {{ balance(account.amount) }}
              </option>
            }
          </select>
        </label>
        <button type="button" class="ghost" (click)="disconnect()">Disconnect</button>
      }

      @if (kmd.error(); as error) {
        <p class="error" role="alert">{{ error }}</p>
      }
    </div>
  `,
  styles: `
    .signer {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 0.75rem;
      padding: 0.7rem 0.9rem;
      border: 1px solid var(--hairline);
      border-radius: 3px;
      background: var(--ink-06);
    }
    .note { margin: 0; color: var(--text-faint); font-size: 0.82rem; max-width: 60ch; }
    .note strong { color: var(--ink); }
    .account select { min-width: 24rem; }
    .error { margin: 0; color: var(--danger); font-size: 0.82rem; }
  `,
})
export class SignerBar {
  protected readonly kmd = inject(KmdService);
  private readonly archon = inject(ArchonService);

  protected readonly network = computed(() => this.archon.network());

  protected connect(): void {
    void this.kmd.connect();
  }

  protected disconnect(): void {
    this.kmd.disconnect();
  }

  protected use(event: Event): void {
    this.kmd.use((event.target as HTMLSelectElement).value);
  }

  protected label(address: string): string {
    return shortAddress(address);
  }

  protected balance(amount: bigint): string {
    return algos(amount);
  }
}
