/**
 * The wallets the console can sign with.
 *
 * Follows the CorvidLabs house pattern (see algorune's client/wallet.ts): list
 * every wallet that needs no configuration, and only add the generic
 * WalletConnect entry when a project id happens to be configured — Pera,
 * Defly, Lute, Exodus and Kibisis each bring their own connection and need no
 * project id at all.
 *
 * KMD is included for LocalNet, where it is how a browser signs without any
 * wallet installed.
 */

import type { WalletAdapterConfig } from '@txnlab/use-wallet';
import { defly } from '@txnlab/use-wallet-defly';
import { exodus } from '@txnlab/use-wallet-exodus';
import { kibisis } from '@txnlab/use-wallet-kibisis';
import { kmd } from '@txnlab/use-wallet-kmd';
import { lute } from '@txnlab/use-wallet-lute';
import { pera } from '@txnlab/use-wallet-pera';
import { walletConnect } from '@txnlab/use-wallet-walletconnect';

import { NETWORKS, type NetworkKey } from '@corvidlabs/arcron/networks';

/** Set at runtime (window config or a build define) to enable WalletConnect. */
declare global {
  interface Window {
    __ARCRON__?: { walletConnectProjectId?: string };
  }
}

const LOCALNET_TOKEN = 'a'.repeat(64);

function walletConnectProjectId(): string {
  return window.__ARCRON__?.walletConnectProjectId?.trim() ?? '';
}

/** Wallets that hold real accounts — the ones that matter off LocalNet. */
export function publicWallets(): WalletAdapterConfig[] {
  const wallets: WalletAdapterConfig[] = [pera(), defly(), lute(), exodus(), kibisis()];
  const projectId = walletConnectProjectId();
  if (projectId !== '') {
    wallets.push(walletConnect({ projectId }));
  }
  return wallets;
}

/**
 * LocalNet's key manager. The sandbox's default wallet has no password, so
 * nothing is ever prompted for or typed into the page; keys stay in KMD and
 * transactions are sent there to be signed.
 */
export function localnetWallet(): WalletAdapterConfig {
  const config = NETWORKS.localnet.kmd;
  return kmd({
    token: config?.token ?? LOCALNET_TOKEN,
    baseServer: config?.server ?? 'http://localhost',
    port: config?.port ?? 4002,
    promptForPassword: async () => '',
  });
}

export function walletsFor(network: NetworkKey): WalletAdapterConfig[] {
  return network === 'localnet' ? [localnetWallet(), ...publicWallets()] : publicWallets();
}

/** Network configuration in the shape use-wallet's manager expects. */
export function managerNetworks() {
  return {
    localnet: {
      algod: {
        token: NETWORKS.localnet.algod.token,
        baseServer: NETWORKS.localnet.algod.server,
        port: NETWORKS.localnet.algod.port,
      },
      isTestnet: true,
    },
    testnet: {
      algod: {
        token: NETWORKS.testnet.algod.token,
        baseServer: NETWORKS.testnet.algod.server,
        port: NETWORKS.testnet.algod.port,
      },
      genesisId: 'testnet-v1.0',
      isTestnet: true,
    },
  };
}
