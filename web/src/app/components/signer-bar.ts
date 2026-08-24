import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';

import { ArcronService } from '../core/arcron.service';
import { shortAddress } from '../core/format';
import { WalletService } from '../core/wallet.service';

@Component({
  selector: 'arcron-signer-bar',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="signer">
      @if (wallet.connected()) {
        <div class="active">
          @if (wallet.activeWallet(); as active) {
            @if (active.icon; as icon) {
              <img class="icon" [src]="icon" alt="" width="20" height="20" />
            }
            <span class="name">{{ active.name }}</span>
          }

          @if (wallet.addresses().length > 1) {
            <label>
              <span class="sr-only">Signing account</span>
              <select (change)="use($event)">
                @for (address of wallet.addresses(); track address) {
                  <option [value]="address" [selected]="address === wallet.activeAddress()">
                    {{ label(address) }}
                  </option>
                }
              </select>
            </label>
          } @else {
            <span class="address mono">{{ label(wallet.activeAddress() ?? '') }}</span>
          }

          <button type="button" class="ghost small" (click)="disconnect()">Disconnect</button>
        </div>
      } @else {
        <p class="prompt">
          <span class="eyebrow">Connect</span>
          {{ prompt() }}
        </p>
        <div class="choices">
          @for (option of wallet.wallets(); track option.id) {
            <button
              type="button"
              class="ghost small wallet"
              [class.pending]="wallet.connecting() === option.id"
              (click)="connect(option.id)"
            >
              @if (option.icon; as icon) {
                <img class="icon" [src]="icon" alt="" width="18" height="18" />
              }
              {{ wallet.connecting() === option.id ? 'Waiting…' : option.name }}
            </button>
          }
          @if (wallet.connecting() !== null) {
            <button type="button" class="ghost small" (click)="cancel()">Cancel</button>
          }
        </div>
      }

      @if (wallet.error(); as error) {
        <p class="error" role="alert">{{ error }}</p>
      }
    </div>
  `,
  styles: `
    .signer {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 0.6rem 0.9rem;
      padding: 0.7rem 0.9rem;
      border: 1px solid var(--hairline);
      border-radius: 3px;
      background: var(--ink-06);
    }
    .active { display: flex; flex-wrap: wrap; align-items: center; gap: 0.6rem; }
    .name { font-weight: 500; }
    .address { color: var(--text-faint); font-size: 0.85rem; }
    .prompt { margin: 0; color: var(--text-faint); font-size: 0.82rem; max-width: 46ch; }
    .prompt .eyebrow { margin-right: 0.4rem; }
    .choices { display: flex; flex-wrap: wrap; gap: 0.4rem; }
    .wallet { display: inline-flex; align-items: center; gap: 0.4rem; }
    .wallet.pending { border-color: var(--sheen); color: var(--sheen); }
    .icon { border-radius: 3px; }
    .error { margin: 0; flex-basis: 100%; color: var(--danger); font-size: 0.82rem; }
  `,
})
export class SignerBar {
  protected readonly wallet = inject(WalletService);
  private readonly arcron = inject(ArcronService);

  protected readonly prompt = computed(() =>
    this.arcron.network() === 'localnet'
      ? 'LocalNet accounts come from KMD — no extension, no mnemonic. Any wallet below works too.'
      : 'Reads are permissionless; connect a wallet to register, execute or cancel upkeeps.',
  );

  protected connect(walletId: string): void {
    void this.wallet.connect(walletId);
  }

  protected cancel(): void {
    this.wallet.cancelConnecting();
  }

  protected disconnect(): void {
    void this.wallet.disconnect();
  }

  protected use(event: Event): void {
    this.wallet.use((event.target as HTMLSelectElement).value);
  }

  protected label(address: string): string {
    return address === '' ? '' : shortAddress(address);
  }
}
