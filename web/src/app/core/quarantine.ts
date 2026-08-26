/**
 * Whether the console can vouch for the app it is pointed at.
 *
 * The console is shipped pointing at one deployment per network, and the app
 * id is a query parameter, so a link can point it anywhere. The ABI and the
 * box layout are public: a look-alike keeper accepts the same register form,
 * shows the same registry, and keeps the escrow. Shareable links are the
 * growth mechanic on a product whose only known attack is a shareable link.
 *
 * So a link naming an app that is not the published one is **quarantined**
 * rather than merely flagged. Three consequences, all derived from the
 * standing below and none of them optional:
 *
 * 1. `canCommitMoney` refuses, so every money button is dead.
 * 2. `isRemembered` is false, so the id is never written to localStorage and
 *    the poison cannot outlive the visit.
 * 3. The console says so, names both ids, and offers one click back.
 *
 * LocalNet is deliberately `unverifiable` rather than `foreign`: there is no
 * published deployment there, the app is whatever you just deployed, and the
 * node is on your own machine, so a link cannot aim it at anything an
 * attacker controls. Quarantining it would break the one workflow the console
 * has to keep cheap without closing an attack that exists.
 */

import { NETWORKS, type NetworkKey } from '@corvidlabs/arcron/networks';

/** What the console can say about the app id it is currently pointed at. */
export type Standing =
  /** No app id at all. Nothing to vouch for, and nothing to warn about. */
  | 'unset'
  /** The deployment this console ships pointing at. */
  | 'canonical'
  /** This network records no published deployment, so nothing here can verify one. */
  | 'unverifiable'
  /** A different app id from the published one, on a network that has one. */
  | 'foreign';

/** The app id this console ships pointing at on a network, where there is one. */
export function canonicalAppId(network: NetworkKey): number | null {
  return NETWORKS[network].defaultAppId ?? null;
}

export function standingOf(state: { appId: number | null; network: NetworkKey }): Standing {
  if (state.appId === null) return 'unset';
  const canonical = canonicalAppId(state.network);
  if (canonical === null) return 'unverifiable';
  return state.appId === canonical ? 'canonical' : 'foreign';
}

/**
 * Whether money must stay locked: a foreign app the visitor has not accepted.
 *
 * Acceptance is per visit and per app id, and is deliberately not persisted.
 * Persisting it would rebuild the thing this closes, which is a poisoned
 * choice outliving the page that made it.
 */
export function isQuarantined(state: { standing: Standing; accepted: boolean }): boolean {
  return state.standing === 'foreign' && !state.accepted;
}

/**
 * Whether this app id may be written to browser memory.
 *
 * A foreign id never is, accepted or not. Accepting says "show me this app
 * now", not "open here next time", and the difference is the whole point: the
 * link that carried the id is the only thing that can bring it back.
 */
export function isRemembered(standing: Standing): boolean {
  return standing !== 'foreign';
}
