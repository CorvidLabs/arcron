/**
 * Network selection, mirroring `scripts/network.py`.
 *
 * LocalNet is the default: the contract is verified there first, and TestNet
 * is a deliberate switch. Both configs are checked against the node's genesis
 * id after connecting, so a misconfigured endpoint fails loudly instead of
 * quietly talking to the wrong chain.
 */

export type NetworkKey = 'localnet' | 'testnet';

export interface NodeConfig {
  readonly server: string;
  readonly port: number | '';
  readonly token: string;
}

export interface NetworkConfig {
  readonly key: NetworkKey;
  readonly label: string;
  readonly algod: NodeConfig;
  /** KMD is LocalNet-only: it is how the browser signs without a wallet extension. */
  readonly kmd?: NodeConfig;
  readonly genesisIds: readonly string[];
  /**
   * Links out to a block explorer, where the network has one.
   *
   * Absent on LocalNet, which nothing outside the machine can see, so every
   * caller has to handle "no link" rather than assuming one exists. That is
   * the whole reason these are optional: a dead link to a chain that is not
   * public is worse than plain text.
   */
  readonly explorerApp?: (appId: number | bigint) => string;
  readonly explorerAccount?: (address: string) => string;
  readonly explorerTx?: (txId: string) => string;
  /** Canonical app id, where one exists. */
  readonly defaultAppId?: number;
  /**
   * Seconds per round to assume before the chain has been watched long
   * enough to measure it.
   *
   * Measured per network rather than taken from Algorand's nominal 2.8,
   * because the networks genuinely differ: over a million rounds, about 31
   * days, TestNet ran at 2.695 and MainNet at 2.752 on 2026-08-28. A single
   * constant is wrong for one of them, and 2.8 is about 4% slow for both,
   * which compounds into hours on a daily cadence.
   *
   * Still only a fallback. Anything showing a schedule to a person should
   * measure the chain it is actually talking to; this is what to use before
   * that measurement exists.
   */
  readonly nominalRoundSeconds: number;
  /**
   * Dev mode: a block is produced per transaction rather than on a timer,
   * so elapsed wall-clock says nothing about how fast rounds pass. Schedules
   * are still shown in human time, using the nominal rate.
   */
  readonly devMode?: boolean;
}

const LOCALNET_TOKEN = 'a'.repeat(64);

export const NETWORKS: Readonly<Record<NetworkKey, NetworkConfig>> = {
  localnet: {
    key: 'localnet',
    label: 'LocalNet',
    algod: { server: 'http://localhost', port: 4001, token: LOCALNET_TOKEN },
    kmd: { server: 'http://localhost', port: 4002, token: LOCALNET_TOKEN },
    genesisIds: ['dockernet-v1', 'sandnet-v1', 'devnet-v1'],
    // Dev mode has no block time at all: a block is produced per transaction.
    // The nominal figure is kept only so a cadence can be shown as a duration.
    nominalRoundSeconds: 2.8,
    devMode: true,
  },
  testnet: {
    key: 'testnet',
    label: 'TestNet',
    algod: { server: 'https://testnet-api.algonode.cloud', port: '', token: '' },
    genesisIds: ['testnet-v1.0'],
    // Measured over 1,000,000 rounds on 2026-08-28.
    nominalRoundSeconds: 2.695,
    explorerApp: (appId) => `https://testnet.explorer.perawallet.app/application/${appId}`,
    explorerAccount: (address) => `https://testnet.explorer.perawallet.app/address/${address}`,
    explorerTx: (txId) => `https://testnet.explorer.perawallet.app/tx/${txId}`,
    defaultAppId: 769891898,
  },
};

// TestNet, matching `scripts/network.py::default_network`. This was 'localnet'
// until 2026-08-26, and nothing pinned it, so docs/console-plan.md could
// record "default to TestNet" as done while the published bundle rewrote its
// own address to ?network=localnet and pointed every stranger at
// http://localhost:4001, which HTTPS blocks as mixed content.
export const DEFAULT_NETWORK: NetworkKey = 'testnet';

export function isNetworkKey(value: string | null): value is NetworkKey {
  return value === 'localnet' || value === 'testnet';
}
