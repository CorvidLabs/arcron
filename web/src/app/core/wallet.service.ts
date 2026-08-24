/**
 * Connecting and signing, for every wallet the console supports.
 *
 * use-wallet keeps its own store; this mirrors it into signals so the rest of
 * the app can stay signal-based, and exposes the same `{ sender, signer }`
 * pair the keeper calls take — so the transaction layer never learns which
 * wallet it is talking to.
 *
 * One active wallet at a time: connecting a second disconnects the first,
 * which is the same single-active behaviour our other Algorand front ends use.
 */

import { computed, effect, Injectable, inject, signal, untracked } from '@angular/core';
import { WalletManager } from '@txnlab/use-wallet';

import { ArcronService, describe } from './arcron.service';
import type { Signing } from './keeper-txns';
import { managerNetworks, walletsFor } from './wallets';

/** Closing a wallet's modal is a decision, not a failure. */
function isDismissal(cause: unknown): boolean {
  const message = (cause instanceof Error ? cause.message : String(cause)).toLowerCase();
  return (
    message.includes('closed') ||
    message.includes('cancel') ||
    message.includes('rejected') ||
    message.includes('declined')
  );
}

export interface WalletOption {
  readonly id: string;
  readonly name: string;
  readonly icon: string | null;
  readonly connected: boolean;
  readonly active: boolean;
  readonly addresses: readonly string[];
}

@Injectable({ providedIn: 'root' })
export class WalletService {
  private readonly arcron = inject(ArcronService);
  private managers = new Map<string, WalletManager>();
  private unsubscribe: (() => void) | null = null;

  readonly wallets = signal<readonly WalletOption[]>([]);
  readonly activeAddress = signal<string | null>(null);
  readonly activeWalletId = signal<string | null>(null);
  readonly addresses = signal<readonly string[]>([]);
  readonly connecting = signal<string | null>(null);
  readonly error = signal<string | null>(null);

  readonly connected = computed(() => this.activeAddress() !== null);
  readonly activeWallet = computed(
    () => this.wallets().find((wallet) => wallet.id === this.activeWalletId()) ?? null,
  );

  constructor() {
    effect(() => {
      const network = this.arcron.network();
      untracked(() => this.useNetwork(network));
    });
  }

  async connect(walletId: string): Promise<void> {
    const manager = this.manager();
    // Starting a second wallet abandons the first attempt rather than queueing
    // behind it — a QR waiting to be scanned must never lock the picker.
    this.connecting.set(walletId);
    this.error.set(null);
    try {
      const wallet = manager.wallets.find((candidate) => String(candidate.id) === walletId);
      if (wallet === undefined) throw new Error(`Unknown wallet: ${walletId}`);
      // Keep exactly one wallet active, so "the connected account" is never
      // ambiguous when a transaction is signed.
      for (const other of manager.wallets) {
        if (other.isConnected && other.id !== wallet.id) await other.disconnect();
      }
      if (wallet.isConnected) wallet.setActive();
      else await wallet.connect();
      this.sync();
    } catch (cause) {
      // A wallet the user closed or declined is not an error worth shouting
      // about; anything else is.
      if (!isDismissal(cause)) this.error.set(describe(cause));
    } finally {
      // Only clear the flag if this attempt is still the current one.
      if (untracked(this.connecting) === walletId) this.connecting.set(null);
    }
  }

  /**
   * Give up waiting on a wallet. If it connects later anyway, the manager's
   * own subscription still picks it up.
   */
  cancelConnecting(): void {
    this.connecting.set(null);
    this.error.set(null);
  }

  async disconnect(): Promise<void> {
    try {
      await this.manager().disconnect();
    } catch (cause) {
      this.error.set(describe(cause));
    }
    this.sync();
  }

  /** Switch between the accounts a connected wallet exposes. */
  use(address: string): void {
    const wallet = this.manager().activeWallet;
    wallet?.setActiveAccount(address);
    this.sync();
  }

  /** The sender/signer pair the keeper calls need, or null if not connected. */
  signing(): Signing | null {
    const manager = this.manager();
    const sender = manager.activeAddress;
    if (sender === null || sender === undefined) return null;
    return { sender, signer: manager.transactionSigner };
  }

  private manager(): WalletManager {
    const network = this.arcron.network();
    const existing = this.managers.get(network);
    if (existing !== undefined) return existing;
    const manager = new WalletManager({
      wallets: walletsFor(network),
      networks: managerNetworks(),
      defaultNetwork: network,
      options: { persistNetwork: false },
    });
    this.managers.set(network, manager);
    return manager;
  }

  private useNetwork(network: string): void {
    this.unsubscribe?.();
    this.error.set(null);
    const manager = this.manager();
    this.unsubscribe = manager.subscribe(() => this.sync());
    // A wallet may already be connected from a previous visit.
    void manager
      .resumeSessions()
      .catch(() => undefined)
      .finally(() => this.sync());
    this.sync();
  }

  private sync(): void {
    const manager = this.manager();
    this.wallets.set(
      manager.wallets.map((wallet) => ({
        id: String(wallet.id),
        name: wallet.metadata?.name ?? String(wallet.id),
        icon: wallet.metadata?.icon ?? null,
        connected: wallet.isConnected,
        active: wallet.isActive,
        addresses: (wallet.accounts ?? []).map((account) => account.address),
      })),
    );
    this.activeAddress.set(manager.activeAddress ?? null);
    this.activeWalletId.set(
      manager.activeWallet === null ? null : String(manager.activeWallet.id),
    );
    this.addresses.set(manager.activeWalletAddresses ?? []);
  }
}
