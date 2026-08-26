/**
 * The console is meant to spread by people sending each other links, and a
 * link is the only attack this product has: the ABI and box layout are
 * public, so a look-alike keeper shows the same registry, accepts the same
 * register form, and keeps whatever is escrowed in it.
 *
 * So a link naming an app that is not the published deployment is not warned
 * about. It is quarantined, and these are the three things that has to mean.
 *
 * Everything here imports the code the console runs. `canCommitMoney` is the
 * one gate every money button and `KeeperService.send` key on, and `storeAppId`
 * is the only writer of app-id memory, so deleting either guard fails a test
 * below rather than passing a paraphrase of it.
 */

import { describe, expect, test } from 'bun:test';

import { NETWORKS } from '@corvidlabs/arcron/networks';

import { canCommitMoney } from './arcron.service';
import { appIdStorageKey, storeAppId } from './entry';
import { canonicalAppId, isQuarantined, isRemembered, standingOf } from './quarantine';

/** Whatever the console ships pointing at, rather than a number retyped here. */
const CANONICAL = NETWORKS.testnet.defaultAppId as number;
/** A look-alike. Any id that is not the canonical one behaves the same way. */
const HOSTILE = 999_999_999;

/** A read that has succeeded, on the right chain: everything but the app id is fine. */
const healthy = { status: 'ready', genesisMatches: true };

function fakeStorage() {
    const written = new Map<string, string>();
    const removed: string[] = [];
    return {
        written,
        removed,
        setItem: (key: string, value: string) => void written.set(key, value),
        removeItem: (key: string) => void removed.push(key),
    };
}

describe('what the console can vouch for', () => {
    test('the app it ships pointing at on TestNet is canonical', () => {
        expect(canonicalAppId('testnet')).toBe(CANONICAL);
        expect(standingOf({ appId: CANONICAL, network: 'testnet' })).toBe('canonical');
    });

    test('any other app id on TestNet is foreign', () => {
        expect(standingOf({ appId: HOSTILE, network: 'testnet' })).toBe('foreign');
        expect(standingOf({ appId: CANONICAL + 1, network: 'testnet' })).toBe('foreign');
    });

    test('LocalNet records no published app, so nothing there is foreign', () => {
        // The app on LocalNet is whatever you just deployed, and the node is
        // on your own machine, so a link cannot aim it at anything an
        // attacker controls. Quarantining it would cost the one workflow that
        // has to stay cheap and close no attack.
        expect(canonicalAppId('localnet')).toBeNull();
        expect(standingOf({ appId: HOSTILE, network: 'localnet' })).toBe('unverifiable');
    });

    test('no app id at all is neither vouched for nor quarantined', () => {
        expect(standingOf({ appId: null, network: 'testnet' })).toBe('unset');
    });
});

describe('a link naming a foreign app leaves every money button dead', () => {
    test('the canonical app is not quarantined, so money works normally', () => {
        const standing = standingOf({ appId: CANONICAL, network: 'testnet' });
        const quarantined = isQuarantined({ standing, accepted: false });
        expect(quarantined).toBe(false);
        expect(canCommitMoney({ ...healthy, appId: CANONICAL, quarantined })).toBe(true);
    });

    test('a foreign app blocks writes, however healthy the read', () => {
        // The whole point. Delete `!state.quarantined` from `canCommitMoney`
        // and this is the test that goes red: a poisoned link would otherwise
        // reach a page whose register button is live under a warning.
        const standing = standingOf({ appId: HOSTILE, network: 'testnet' });
        const quarantined = isQuarantined({ standing, accepted: false });
        expect(quarantined).toBe(true);
        expect(canCommitMoney({ ...healthy, appId: HOSTILE, quarantined })).toBe(false);
    });

    test('accepting it, and only accepting it, unlocks them again', () => {
        const standing = standingOf({ appId: HOSTILE, network: 'testnet' });
        const quarantined = isQuarantined({ standing, accepted: true });
        expect(quarantined).toBe(false);
        expect(canCommitMoney({ ...healthy, appId: HOSTILE, quarantined })).toBe(true);
    });

    test('accepting does not repeal the other guards', () => {
        // Continuing to a foreign app is a decision about identity. It is not
        // a decision to send transactions against a failed read or a node
        // answering for the wrong chain.
        expect(canCommitMoney({ ...healthy, status: 'error', appId: HOSTILE, quarantined: false })).toBe(
            false,
        );
        expect(
            canCommitMoney({ ...healthy, genesisMatches: false, appId: HOSTILE, quarantined: false }),
        ).toBe(false);
    });

    test('a LocalNet app is not quarantined, so developers are not gated', () => {
        const standing = standingOf({ appId: HOSTILE, network: 'localnet' });
        expect(isQuarantined({ standing, accepted: false })).toBe(false);
        expect(
            canCommitMoney({ ...healthy, appId: HOSTILE, quarantined: false }),
        ).toBe(true);
    });
});

describe('a foreign app id is never written down', () => {
    test('the canonical app is remembered, so a bookmark keeps working', () => {
        const storage = fakeStorage();
        storeAppId(storage, 'testnet', CANONICAL, standingOf({ appId: CANONICAL, network: 'testnet' }));
        expect(storage.written.get(appIdStorageKey('testnet'))).toBe(String(CANONICAL));
    });

    test('a foreign app id is not written, which is what stops it outliving the visit', () => {
        // Delete the `isRemembered` guard from `storeAppId` and this goes red.
        // Without it the poisoned id is in localStorage for good: the victim
        // closes the tab, opens the console fresh with no link at all, and
        // `entryFrom` hands them the attacker's app back.
        const storage = fakeStorage();
        storeAppId(storage, 'testnet', HOSTILE, standingOf({ appId: HOSTILE, network: 'testnet' }));
        expect(storage.written.size).toBe(0);
        expect(isRemembered('foreign')).toBe(false);
    });

    test('nor is it cleared: what the visitor had before the link is theirs', () => {
        const storage = fakeStorage();
        storeAppId(storage, 'testnet', HOSTILE, 'foreign');
        expect(storage.removed).toEqual([]);
    });

    test('accepting a foreign app still does not write it down', () => {
        // Accepting says "show me this app now", not "open here next time".
        // Persisting the acceptance would rebuild the thing this closes.
        const storage = fakeStorage();
        storeAppId(storage, 'testnet', HOSTILE, standingOf({ appId: HOSTILE, network: 'testnet' }));
        expect(storage.written.size).toBe(0);
    });

    test('a LocalNet app id is remembered, because a developer redeploys constantly', () => {
        const storage = fakeStorage();
        storeAppId(storage, 'localnet', 1_001, standingOf({ appId: 1_001, network: 'localnet' }));
        expect(storage.written.get(appIdStorageKey('localnet'))).toBe('1001');
    });

    test('choosing no registry clears the memory rather than leaving a stale one', () => {
        const storage = fakeStorage();
        storeAppId(storage, 'testnet', null, standingOf({ appId: null, network: 'testnet' }));
        expect(storage.removed).toEqual([appIdStorageKey('testnet')]);
    });
});
