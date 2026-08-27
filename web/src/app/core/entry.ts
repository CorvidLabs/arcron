/**
 * Where the console points when it opens.
 *
 * **Outside dev mode there is only one answer**: the canonical deployment on
 * the default network. No parameter changes it and nothing is remembered,
 * because the published console serves one deployment and a stranger has
 * nothing to configure. That is what makes a poisoned `?app=` link inert.
 *
 * In dev mode the console remembers the last network and app id you looked
 * at, which is right for a tool you keep open while working. The precedence is
 * **link, then memory, then default**, and following a link updates the memory
 * so a shared link and a bookmark behave the same afterwards.
 *
 * Switching network from the picker is deliberately *not* covered here. The
 * link describes where to open, not where to go next, and carrying a linked
 * app id across a network switch would point the console at an id that does
 * not exist on the other chain.
 */

import { DEFAULT_NETWORK, isNetworkKey, NETWORKS, type NetworkKey } from '@corvidlabs/arcron/networks';

import { isRemembered, type Standing } from './quarantine';

/** Query parameter naming the chain: `?network=testnet`. */
export const NETWORK_PARAM = 'network';
/** Query parameter naming the keeper app: `?app=769891898`. */
export const APP_PARAM = 'app';

/** Where the console opens: a chain, and a registry on it if there is one. */
export interface Entry {
    readonly network: NetworkKey;
    readonly appId: number | null;
}

/** Reads a remembered app id for a network, or null if nothing is remembered. */
export type StoredAppId = (network: NetworkKey) => string | null;

function parseAppId(raw: string): number | null | undefined {
    // `?app=none` and `?app=0` both mean "show me the chain, no registry".
    if (raw === 'none' || raw === '0') return null;
    if (!/^\d+$/.test(raw)) return undefined;
    const parsed = Number(raw);
    return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : undefined;
}

/** The remembered app id for a network, falling back to its canonical one. */
export function rememberedAppId(network: NetworkKey, stored: string | null): number | null {
    if (stored !== null && /^\d+$/.test(stored)) return Number(stored);
    return NETWORKS[network].defaultAppId ?? null;
}

/**
 * Resolve where to open from the entry URL and what the console remembers.
 *
 * - `Parameter search`: `location.search`, including the leading `?`.
 * - `Parameter storedNetwork`: the remembered network, or null.
 * - `Parameter storedAppId`: reads the remembered app id for a given network.
 */
export function entryFrom(
    search: string,
    storedNetwork: string | null,
    storedAppId: StoredAppId,
    devEstablished = false,
): Entry {
    const parameters = new URLSearchParams(search);

    // The console opens on one deployment unless dev mode was *already* on
    // before this navigation. Gating on "established" rather than "enabled" is
    // what stops a single `?dev=1&app=<look-alike>` link from turning dev mode
    // on and redirecting in the same step. See `dev-mode.ts`.
    if (!devEstablished) {
        return {
            network: DEFAULT_NETWORK,
            appId: NETWORKS[DEFAULT_NETWORK].defaultAppId ?? null,
        };
    }

    const linkedNetwork = parameters.get(NETWORK_PARAM);
    const linkedApp = parameters.get(APP_PARAM);

    const network = isNetworkKey(linkedNetwork)
        ? linkedNetwork
        : isNetworkKey(storedNetwork)
          ? storedNetwork
          : DEFAULT_NETWORK;

    // A linked app id belongs to the chain the link opens on, whether the link
    // named that chain or inherited it. It is resolved once, here, and never
    // consulted again.
    if (linkedApp !== null) {
        const parsed = parseAppId(linkedApp);
        if (parsed !== undefined) return { network, appId: parsed };
    }
    return { network, appId: rememberedAppId(network, storedAppId(network)) };
}

/**
 * The query parameters describing exactly this network and app.
 *
 * The router owns the address bar now, and it takes a parameter map rather
 * than a string, so this is the shape everything else is built from.
 */
export function entryParams(
    network: NetworkKey,
    appId: number | null,
): Record<string, string> {
    return { [NETWORK_PARAM]: network, [APP_PARAM]: appId === null ? 'none' : String(appId) };
}

/** The subset of `Storage` this module writes, so a test can supply its own. */
export type AppIdStorage = Pick<Storage, 'setItem' | 'removeItem'>;

/** Where a network's remembered app id is kept. */
export function appIdStorageKey(network: NetworkKey): string {
    return `arcron.appId.${network}`;
}

/**
 * Remember the app id the console is pointed at, unless it must not be.
 *
 * The only writer of app-id memory, so the quarantine rule cannot be missed
 * by a second call site: a foreign app id is never written, which is what
 * stops a poisoned link outliving the visit that followed it. It is not
 * cleared either. Whatever the visitor had before the link arrived is theirs
 * and the attacker does not get to erase it.
 */
export function storeAppId(
    storage: AppIdStorage,
    network: NetworkKey,
    appId: number | null,
    standing: Standing,
): void {
    if (!isRemembered(standing)) return;
    const key = appIdStorageKey(network);
    if (appId === null) storage.removeItem(key);
    else storage.setItem(key, String(appId));
}
