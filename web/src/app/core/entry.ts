/**
 * Where the console points when it opens.
 *
 * The console remembers the last network and app id you looked at, which is
 * right for a tool you keep open while developing. It is wrong for a link: a
 * hosted console has to show the same registry to everyone who follows the
 * same URL, whatever they happened to look at last.
 *
 * So on open the precedence is **link, then memory, then default**, and
 * following a link updates the memory — a shared link and a bookmark behave
 * the same afterwards.
 *
 * Switching network from the picker is deliberately *not* covered here. The
 * link describes where to open, not where to go next, and carrying a linked
 * app id across a network switch would point the console at an id that does
 * not exist on the other chain.
 */

import { DEFAULT_NETWORK, isNetworkKey, NETWORKS, type NetworkKey } from '@corvidlabs/arcron/networks';

/** Query parameter naming the chain: `?network=testnet`. */
export const NETWORK_PARAM = 'network';
/** Query parameter naming the keeper app: `?app=769823086`. */
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
): Entry {
    const parameters = new URLSearchParams(search);
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

/** The link that opens the console on exactly this network and app. */
export function entryLink(base: string, network: NetworkKey, appId: number | null): string {
    const query = new URLSearchParams({ [NETWORK_PARAM]: network });
    query.set(APP_PARAM, appId === null ? 'none' : String(appId));
    return `${base}?${query.toString()}`;
}
