/**
 * One wallet, one account, one signature.
 *
 * The console's `wallet.service.ts` does far more: several wallets, account
 * switching, network switching, reconnection across navigations. None of that
 * is wanted here. This app exists so the creator can sign one irreversible
 * transaction from their own machine, and the smaller surface is the point
 * rather than a shortcut.
 *
 * Pera only, because that is the wallet the creator uses. Offering the others
 * would be speculative breadth in an app that runs a handful of times a year,
 * and each one is another connection path to get wrong.
 */

import { Injectable, signal } from '@angular/core';
import {
  NetworkConfigBuilder,
  WalletManager,
  type WalletAdapterConfig,
} from '@txnlab/use-wallet';
import { pera } from '@txnlab/use-wallet-pera';
import type algosdk from 'algosdk';

export type GovernNetwork = 'testnet' | 'mainnet';

export interface Signing {
  readonly sender: string;
  readonly signer: algosdk.TransactionSigner;
}

/**
 * Both networks, unlike the console, which deliberately has no MainNet entry.
 *
 * This is the one page with any business touching MainNet: it is where a
 * MainNet deployment gets frozen. It is also why this app is run locally and
 * never served, since a page that authorizes permanent changes to a live
 * contract should not be somewhere a stranger can load a convincing copy of.
 */
function networks() {
  return new NetworkConfigBuilder()
    .testnet({ algod: { baseServer: 'https://testnet-api.4160.nodely.dev', token: '', port: '' } })
    .mainnet({ algod: { baseServer: 'https://mainnet-api.4160.nodely.dev', token: '', port: '' } })
    .build();
}

@Injectable({ providedIn: 'root' })
export class GovernWallet {
  readonly address = signal<string | null>(null);
  readonly connecting = signal(false);
  readonly error = signal<string | null>(null);

  private manager: WalletManager | null = null;

  private get client(): WalletManager {
    this.manager ??= new WalletManager({
      wallets: [pera() as WalletAdapterConfig],
      networks: networks(),
      defaultNetwork: 'testnet',
      options: { persistNetwork: false },
    });
    return this.manager;
  }

  async connect(): Promise<void> {
    this.connecting.set(true);
    this.error.set(null);
    try {
      const wallet = this.client.wallets[0];
      if (wallet === undefined) throw new Error('Pera is not available.');
      await wallet.connect();
      wallet.setActive();
      this.address.set(this.client.activeAddress ?? null);
    } catch (cause) {
      // Closing the modal is a decision, not a failure, and reporting it as an
      // error trains an operator to ignore the error line.
      const message = cause instanceof Error ? cause.message : String(cause);
      const dismissed = /closed|cancel|reject|declin/i.test(message);
      this.error.set(dismissed ? null : message);
    } finally {
      this.connecting.set(false);
    }
  }

  async disconnect(): Promise<void> {
    try {
      await this.client.activeWallet?.disconnect();
    } finally {
      this.address.set(null);
    }
  }

  async useNetwork(network: GovernNetwork): Promise<void> {
    await this.client.setActiveNetwork(network);
    this.address.set(this.client.activeAddress ?? null);
  }

  signing(): Signing | null {
    const sender = this.client.activeAddress;
    if (!sender) return null;
    return { sender, signer: this.client.transactionSigner };
  }
}
