/**
 * Two behaviours, and the difference between them is the point.
 *
 * **Outside dev mode** the console opens on one deployment and no parameter
 * changes it. That is what makes a link carrying a look-alike `?app=` inert
 * for a stranger, rather than merely warned about.
 *
 * **In dev mode** the link beats what the browser remembers, because someone
 * working on the console needs to point it somewhere, and a linked app id must
 * never escape the chain it belongs to. Every case below that passes `true` is
 * asserting dev behaviour.
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
        const entry = entryFrom(`?network=testnet&app=${LINKED_APP}`, 'localnet', () => '42', true);
        expect(entry).toEqual({ network: 'testnet', appId: LINKED_APP });
    });

    test('memory is used when the link says nothing', () => {
        const entry = entryFrom('', 'testnet', () => '4242', true);
        expect(entry).toEqual({ network: 'testnet', appId: 4242 });
    });

    test('with neither, it opens on the default network and its canonical app', () => {
        // Named the canonical app in its title and asserted only the network,
        // and asserted the wrong one: 'localnet' agreed with the code rather
        // than with the intent, so it held the bug in place instead of
        // catching it. A stranger opening a published console would have been
        // pointed at http://localhost:4001.
        const entry = entryFrom('', null, nothingStored, true);
        expect(entry.network).toBe(DEFAULT_NETWORK);
        expect(entry.network).toBe('testnet');
        expect(entry.appId).toBe(NETWORKS.testnet.defaultAppId);
    });

    test('a linked app inherits the network the link opens on', () => {
        const entry = entryFrom(`?app=${LINKED_APP}`, 'testnet', nothingStored, true);
        expect(entry).toEqual({ network: 'testnet', appId: LINKED_APP });
    });

    test('?app=none opens the chain with no registry', () => {
        expect(entryFrom('?network=testnet&app=none', null, () => '42', true).appId).toBeNull();
        expect(entryFrom('?network=testnet&app=0', null, () => '42', true).appId).toBeNull();
    });

    test('a nonsense app id falls back rather than opening on NaN', () => {
        for (const bad of ['abc', '-1', '1.5', '99999999999999999999', '']) {
            const entry = entryFrom(`?network=testnet&app=${bad}`, null, () => '4242', true);
            expect(entry.appId).toBe(4242);
        }
    });

    test('a nonsense network falls back rather than opening on an unknown chain', () => {
        expect(entryFrom('?network=mainnet', 'testnet', nothingStored, true).network).toBe('testnet');
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
        expect(entryFrom(search('testnet', LINKED_APP), 'localnet', () => '42', true)).toEqual({
            network: 'testnet',
            appId: LINKED_APP,
        });
    });

    test('an empty registry round-trips as empty, not as the canonical app', () => {
        expect(entryFrom(search('testnet', null), null, () => '42', true).appId).toBeNull();
    });
});

describe('the published console shows one deployment and nothing else', () => {
    test('a link naming another app is ignored, not merely warned about', () => {
        // The only attack this project has: anyone can deploy a contract with
        // this ABI and box layout, and a look-alike shows the same registry and
        // accepts the same register form. quarantine.ts warns about it. Not
        // reading the parameter removes it.
        const entry = entryFrom(`?app=${LINKED_APP}`, null, nothingStored);
        expect(entry.appId).toBe(NETWORKS[DEFAULT_NETWORK].defaultAppId ?? null);
        expect(entry.appId).not.toBe(LINKED_APP);
    });

    test('a link naming another network is ignored', () => {
        expect(entryFrom('?network=localnet', null, nothingStored).network).toBe(DEFAULT_NETWORK);
    });

    test('?app=none cannot empty the registry either', () => {
        // Otherwise a link still has a way to make the console look dead.
        expect(entryFrom('?app=none', null, nothingStored).appId).not.toBeNull();
    });

    test('nothing remembered can override it', () => {
        // A visitor who once opened a dev link must not carry that deployment
        // into the published console afterwards.
        const entry = entryFrom('', 'localnet', () => String(LINKED_APP));
        expect(entry.network).toBe(DEFAULT_NETWORK);
        expect(entry.appId).toBe(NETWORKS[DEFAULT_NETWORK].defaultAppId ?? null);
    });

    test('the same URL opens the same registry for everyone', () => {
        const strangers = [
            entryFrom('', null, nothingStored),
            entryFrom('?app=999', 'localnet', () => '42'),
            entryFrom('?network=localnet&app=none', 'testnet', () => '7'),
        ];
        for (const entry of strangers) {
            expect(entry).toEqual(strangers[0]);
        }
    });
});
