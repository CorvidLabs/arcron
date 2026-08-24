/**
 * Signing on LocalNet, via KMD.
 *
 * LocalNet accounts live in the sandbox's key manager, which the browser can
 * reach directly — so the console is fully usable with no wallet extension
 * and no mnemonics pasted into a page. Keys never leave KMD: transactions are
 * sent there to be signed.
 *
 * TestNet needs a real wallet instead. `@txnlab/use-wallet` is installed for
 * that; wiring an adapter (Pera, Defly) is the next step once the LocalNet
 * flow is confirmed.
 */

import { computed, effect, Injectable, inject, signal, untracked } from '@angular/core';
import algosdk from 'algosdk';

import { ArchonService, describe } from './archon.service';
import type { Signing } from './keeper-txns';

export interface KmdAccount {
  readonly address: string;
  readonly walletId: string;
  readonly walletName: string;
  readonly amount: bigint;
}

@Injectable({ providedIn: 'root' })
export class KmdService {
  private readonly archon = inject(ArchonService);

  readonly accounts = signal<readonly KmdAccount[]>([]);
  readonly activeAddress = signal<string | null>(null);
  readonly connecting = signal(false);
  readonly error = signal<string | null>(null);

  /** KMD is a LocalNet facility; there is deliberately no TestNet equivalent. */
  readonly supported = computed(() => this.archon.config().kmd !== undefined);
  readonly activeAccount = computed(
    () => this.accounts().find((account) => account.address === this.activeAddress()) ?? null,
  );
  readonly connected = computed(() => this.activeAccount() !== null);

  constructor() {
    // A KMD account exists on exactly one chain. Switching networks must drop
    // it, or the console would offer to sign TestNet transactions with keys
    // only LocalNet knows about.
    effect(() => {
      this.archon.network();
      untracked(() => this.disconnect());
    });
  }

  async connect(): Promise<void> {
    const config = this.archon.config().kmd;
    if (config === undefined) {
      this.error.set('KMD is only available on LocalNet');
      return;
    }
    this.connecting.set(true);
    this.error.set(null);
    try {
      const kmd = new algosdk.Kmd(config.token, config.server, config.port);
      const algod = this.archon.algod();
      const { wallets } = await kmd.listWallets();
      const accounts: KmdAccount[] = [];
      for (const wallet of wallets) {
        const handle = await this.handleFor(kmd, wallet.id);
        const { addresses } = await kmd.listKeys(handle);
        for (const address of addresses as string[]) {
          const info = await algod.accountInformation(address).do();
          accounts.push({
            address,
            walletId: wallet.id,
            walletName: wallet.name,
            amount: info.amount,
          });
        }
      }
      accounts.sort((left, right) => (right.amount > left.amount ? 1 : -1));
      this.accounts.set(accounts);
      if (this.activeAccount() === null) {
        this.activeAddress.set(accounts.at(0)?.address ?? null);
      }
    } catch (cause) {
      this.error.set(describe(cause));
    } finally {
      this.connecting.set(false);
    }
  }

  disconnect(): void {
    this.accounts.set([]);
    this.activeAddress.set(null);
    this.error.set(null);
  }

  use(address: string): void {
    this.activeAddress.set(address);
  }

  /** The sender/signer pair the keeper calls need, or null if not connected. */
  signing(): Signing | null {
    const account = this.activeAccount();
    const config = this.archon.config().kmd;
    if (account === null || config === undefined) return null;
    const kmd = new algosdk.Kmd(config.token, config.server, config.port);
    return {
      sender: account.address,
      signer: async (group, indexes) => {
        // Handles expire, so take a fresh one per signing round-trip.
        const handle = await this.handleFor(kmd, account.walletId);
        return Promise.all(
          indexes.map(async (index) =>
            new Uint8Array(await kmd.signTransaction(handle, '', group[index])),
          ),
        );
      },
    };
  }

  private async handleFor(kmd: algosdk.Kmd, walletId: string): Promise<string> {
    const response = await kmd.initWalletHandle(walletId, '');
    return response.wallet_handle_token;
  }
}
