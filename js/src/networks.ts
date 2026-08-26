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
  readonly explorerApp?: (appId: number | bigint) => string;
  /** Canonical app id, where one exists. */
  readonly defaultAppId?: number;
  /**
   * Seconds per round to assume before the chain has been watched long
   * enough to measure it, taken from Algorand's nominal block time.
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
    nominalRoundSeconds: 2.8,
    devMode: true,
  },
  testnet: {
    key: 'testnet',
    label: 'TestNet',
    algod: { server: 'https://testnet-api.algonode.cloud', port: '', token: '' },
    genesisIds: ['testnet-v1.0'],
    nominalRoundSeconds: 2.8,
    explorerApp: (appId) => `https://testnet.explorer.perawallet.app/application/${appId}`,
    defaultAppId: 769891898,
  },
};

export const DEFAULT_NETWORK: NetworkKey = 'localnet';

export function isNetworkKey(value: string | null): value is NetworkKey {
  return value === 'localnet' || value === 'testnet';
}
