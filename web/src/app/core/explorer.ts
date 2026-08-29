/**
 * Links out to a block explorer.
 *
 * `NETWORKS.testnet.explorerApp` has existed since the console was written and
 * nothing ever called it, so nothing in the console linked to an explorer at
 * all: app ids, target apps, app accounts and transaction ids were plain text.
 * That matters most exactly where money is committed, because checking an app
 * id somewhere we do not control is the one verification a visitor can make
 * that this page cannot fake.
 *
 * LocalNet has no explorer, so every one of these returns null there and the
 * caller renders text instead. A dead link to a chain nobody outside the
 * machine can reach would be worse than no link.
 */

import { NETWORKS, type NetworkKey } from '@corvidlabs/arcron/networks';

export type ExplorerKind = 'app' | 'account' | 'transaction' | 'asset';

export function explorerUrl(
  network: NetworkKey,
  kind: ExplorerKind,
  value: string,
): string | null {
  const config = NETWORKS[network];
  switch (kind) {
    case 'app':
      return config.explorerApp?.(Number(value)) ?? null;
    case 'account':
      return config.explorerAccount?.(value) ?? null;
    case 'transaction':
      return config.explorerTx?.(value) ?? null;
    case 'asset':
      return config.explorerAsset?.(Number(value)) ?? null;
  }
}
