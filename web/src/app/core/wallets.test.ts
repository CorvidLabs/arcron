/**
 * Every wallet the console offers must construct with no configuration.
 *
 * This is the property that matters in practice: Pera, Defly, Lute, Exodus
 * and Kibisis each bring their own connection, so the console works out of the
 * box. Only the generic WalletConnect entry needs a project id, and it is only
 * offered when one is configured. A regression there would silently drop
 * every wallet from the picker at runtime.
 */

import { beforeAll, describe, expect, test } from 'bun:test';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body></body></html>', {
  url: 'http://localhost:4200/',
});
const globals = globalThis as Record<string, unknown>;
globals['window'] = dom.window;
globals['document'] = dom.window.document;
globals['navigator'] = dom.window.navigator;
globals['localStorage'] = dom.window.localStorage;

let walletsFor: typeof import('./wallets').walletsFor;
let managerNetworks: typeof import('./wallets').managerNetworks;
let publicWallets: typeof import('./wallets').publicWallets;

beforeAll(async () => {
  ({ walletsFor, managerNetworks, publicWallets } = await import('./wallets'));
});

describe('wallet catalogue', () => {
  test('offers the five self-contained wallets, with no project id anywhere', () => {
    const ids = publicWallets().map((wallet) => String(wallet.id));
    expect(ids).toEqual(['pera', 'defly', 'lute', 'exodus', 'kibisis']);
  });

  test('adds KMD on LocalNet, where a browser can sign with no wallet installed', () => {
    expect(walletsFor('localnet').map((wallet) => String(wallet.id))).toEqual([
      'kmd',
      'pera',
      'defly',
      'lute',
      'exodus',
      'kibisis',
    ]);
  });

  test('leaves KMD out anywhere it cannot work', () => {
    expect(walletsFor('testnet').map((wallet) => String(wallet.id))).not.toContain('kmd');
  });

  test('includes WalletConnect only when a project id is configured', () => {
    expect(publicWallets().map((wallet) => String(wallet.id))).not.toContain('walletconnect');
    dom.window.__ARCRON__ = { walletConnectProjectId: 'test-project-id' };
    expect(publicWallets().map((wallet) => String(wallet.id))).toContain('walletconnect');
    dom.window.__ARCRON__ = undefined;
  });

  test('use-wallet accepts the whole set and offers it as a picker', async () => {
    const { WalletManager } = await import('@txnlab/use-wallet');
    const manager = new WalletManager({
      wallets: walletsFor('localnet'),
      networks: managerNetworks(),
      defaultNetwork: 'localnet',
      options: { persistNetwork: false },
    });
    // Names are what the picker shows, so a broken adapter is visible here.
    expect(manager.wallets.map((wallet) => wallet.metadata?.name ?? String(wallet.id))).toEqual([
      'KMD',
      'Pera',
      'Defly',
      'Lute',
      'Exodus',
      'Kibisis',
    ]);
    expect(manager.activeAddress).toBeNull();
    expect(manager.activeNetwork).toBe('localnet');
  });

  test('points each network at the endpoint the console uses', () => {
    const networks = managerNetworks();
    expect(networks.localnet.algod.baseServer).toBe('http://localhost');
    expect(networks.localnet.algod.port).toBe(4001);
    expect(networks.testnet.algod.baseServer).toBe('https://testnet-api.algonode.cloud');
    expect(networks.testnet.genesisId).toBe('testnet-v1.0');
  });
});
