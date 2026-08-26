/**
 * A hosted console has to show the same registry to everyone who follows the
 * same link. That is only true if the link beats whatever the visitor's
 * browser remembers, and if a linked app id never escapes the chain it
 * belongs to.
 */

import { describe, expect, test } from 'bun:test';

import { DEFAULT_NETWORK, NETWORKS } from '@corvidlabs/arcron/networks';

import { appIdStorageKey, entryFrom, entryParams, rememberedAppId, storeAppId } from './entry';

/** Nothing remembered for any network. */
const nothingStored = () => null;

/**
 * Not a real deployment. A link carries whatever app id it carries, so these
 * cases only need a number, and using a real one invites the reader to think
 * the canonical app is asserted here rather than derived below.
 */
const LINKED_APP = 1234567;

describe('opening from a link', () => {
    test('the link beats what the browser remembers', () => {
        const entry = entryFrom(`?network=testnet&app=${LINKED_APP}`, 'localnet', () => '42');
        expect(entry).toEqual({ network: 'testnet', appId: LINKED_APP });
    });

    test('memory is used when the link says nothing', () => {
        const entry = entryFrom('', 'testnet', () => '4242');
        expect(entry).toEqual({ network: 'testnet', appId: 4242 });
    });

    test('with neither, it opens on the default network and its canonical app', () => {
        // Named the canonical app in its title and asserted only the network,
        // and asserted the wrong one: 'localnet' agreed with the code rather
        // than with the intent, so it held the bug in place instead of
        // catching it. A stranger opening a published console would have been
        // pointed at http://localhost:4001.
        const entry = entryFrom('', null, nothingStored);
        expect(entry.network).toBe(DEFAULT_NETWORK);
        expect(entry.network).toBe('testnet');
        expect(entry.appId).toBe(NETWORKS.testnet.defaultAppId);
    });

    test('a linked app inherits the network the link opens on', () => {
        const entry = entryFrom(`?app=${LINKED_APP}`, 'testnet', nothingStored);
        expect(entry).toEqual({ network: 'testnet', appId: LINKED_APP });
    });

    test('?app=none opens the chain with no registry', () => {
        expect(entryFrom('?network=testnet&app=none', null, () => '42').appId).toBeNull();
        expect(entryFrom('?network=testnet&app=0', null, () => '42').appId).toBeNull();
    });

    test('a nonsense app id falls back rather than opening on NaN', () => {
        for (const bad of ['abc', '-1', '1.5', '99999999999999999999', '']) {
            const entry = entryFrom(`?network=testnet&app=${bad}`, null, () => '4242');
            expect(entry.appId).toBe(4242);
        }
    });

    test('a nonsense network falls back rather than opening on an unknown chain', () => {
        expect(entryFrom('?network=mainnet', 'testnet', nothingStored).network).toBe('testnet');
    });
});

describe('switching network in the picker', () => {
    test('uses memory, then the network canonical app, never the link', () => {
        expect(rememberedAppId('testnet', '4242')).toBe(4242);
        expect(rememberedAppId('testnet', null)).toBe(NETWORKS.testnet.defaultAppId);
        // LocalNet has no canonical deployment: it is whatever you just deployed.
        expect(rememberedAppId('localnet', null)).toBeNull();
    });
});

describe('what a link is allowed to leave behind', () => {
    test('the app id it opened on is remembered, so a reload and a bookmark agree', () => {
        const written = new Map<string, string>();
        storeAppId(
            { setItem: (k, v) => void written.set(k, v), removeItem: () => undefined },
            'testnet',
            NETWORKS.testnet.defaultAppId ?? 0,
            'canonical',
        );
        expect(written.get(appIdStorageKey('testnet'))).toBe(String(NETWORKS.testnet.defaultAppId));
    });

    test('unless the console cannot vouch for it, in which case nothing is written', () => {
        // The precedence at the top of this file makes a link beat memory,
        // which is right. It also used to make a link *become* memory, and a
        // poisoned app id then survived every visit after the one that
        // carried it. `quarantine.test.ts` covers the rule; this pins that
        // the writer is the one enforcing it.
        const written = new Map<string, string>();
        const removed: string[] = [];
        storeAppId(
            { setItem: (k, v) => void written.set(k, v), removeItem: (k) => void removed.push(k) },
            'testnet',
            LINKED_APP,
            'foreign',
        );
        expect(written.size).toBe(0);
        expect(removed).toEqual([]);
    });
});

describe('the parameters the address bar carries', () => {
    // `app.ts` merges exactly these into the URL after every change, so what
    // the console writes and what it reads back have to be the same shape.
    const search = (network: 'testnet' | 'localnet', appId: number | null) =>
        `?${new URLSearchParams(entryParams(network, appId)).toString()}`;

    test('round-trip through entryFrom', () => {
        expect(search('testnet', LINKED_APP)).toBe(`?network=testnet&app=${LINKED_APP}`);
        expect(entryFrom(search('testnet', LINKED_APP), 'localnet', () => '42')).toEqual({
            network: 'testnet',
            appId: LINKED_APP,
        });
    });

    test('an empty registry round-trips as empty, not as the canonical app', () => {
        expect(entryFrom(search('testnet', null), null, () => '42').appId).toBeNull();
    });
});
