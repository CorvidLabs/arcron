/**
 * The console's default network is a published address, not a preference.
 *
 * `DEFAULT_NETWORK` was `'localnet'` while `docs/console-plan.md` recorded
 * "default to TestNet" as a completed build-order item. Nothing pinned it, so
 * the document and the code disagreed for as long as nobody built a hosted
 * bundle: it rewrote its own address to `?network=localnet` and pointed every
 * visitor at `http://localhost:4001`, which HTTPS blocks as mixed content. A
 * stranger following a published link would have seen an empty page.
 *
 * These tests exist so that claim is falsifiable. `scripts/network.py` is the
 * other half of the pair and defaults to TestNet; the two must agree.
 */

import { describe, expect, test } from 'bun:test';
import { DEFAULT_NETWORK, isNetworkKey, NETWORKS } from '../src/networks';

describe('where the console opens', () => {
    test('the default network is TestNet, matching scripts/network.py', () => {
        expect(DEFAULT_NETWORK).toBe('testnet');
    });

    test('the default is a network that actually exists', () => {
        expect(isNetworkKey(DEFAULT_NETWORK)).toBe(true);
        expect(NETWORKS[DEFAULT_NETWORK]).toBeDefined();
    });

    test('the default network reaches a public node over HTTPS', () => {
        // The failure this pins is not "wrong network", it is a page that
        // cannot load anything: a published bundle served over HTTPS cannot
        // reach a plaintext localhost node, and the browser blocks it before
        // any request is made.
        const algod = NETWORKS[DEFAULT_NETWORK].algod.server;
        expect(algod.startsWith('https://')).toBe(true);
        expect(algod).not.toContain('localhost');
        expect(algod).not.toContain('127.0.0.1');
    });

    test('the default network names the deployment to open', () => {
        // Without this a visitor arrives at a console with no registry and
        // concludes the network is dead, which is the failure
        // tests/test_app_id_consistency.py was written about.
        expect(NETWORKS[DEFAULT_NETWORK].defaultAppId).toBeGreaterThan(0);
    });

    test('every network can be told apart from the others by its genesis id', () => {
        const seen = new Set<string>();
        for (const key of Object.keys(NETWORKS) as Array<keyof typeof NETWORKS>) {
            for (const genesis of NETWORKS[key].genesisIds) {
                expect(seen.has(genesis)).toBe(false);
                seen.add(genesis);
            }
        }
    });
});
