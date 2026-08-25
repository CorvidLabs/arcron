/**
 * A hosted console has to show the same registry to everyone who follows the
 * same link. That is only true if the link beats whatever the visitor's
 * browser remembers, and if a linked app id never escapes the chain it
 * belongs to.
 */

import { describe, expect, test } from 'bun:test';

import { entryFrom, entryLink, rememberedAppId } from './entry';

/** Nothing remembered for any network. */
const nothingStored = () => null;

describe('opening from a link', () => {
    test('the link beats what the browser remembers', () => {
        const entry = entryFrom('?network=testnet&app=769823086', 'localnet', () => '42');
        expect(entry).toEqual({ network: 'testnet', appId: 769823086 });
    });

    test('memory is used when the link says nothing', () => {
        const entry = entryFrom('', 'testnet', () => '4242');
        expect(entry).toEqual({ network: 'testnet', appId: 4242 });
    });

    test('with neither, it opens on the default network and its canonical app', () => {
        const entry = entryFrom('', null, nothingStored);
        expect(entry.network).toBe('localnet');
    });

    test('a linked app inherits the network the link opens on', () => {
        const entry = entryFrom('?app=769823086', 'testnet', nothingStored);
        expect(entry).toEqual({ network: 'testnet', appId: 769823086 });
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
        expect(rememberedAppId('testnet', null)).toBe(769823086);
        // LocalNet has no canonical deployment: it is whatever you just deployed.
        expect(rememberedAppId('localnet', null)).toBeNull();
    });
});

describe('producing a link', () => {
    test('round-trips through entryFrom', () => {
        const link = entryLink('/arcron/console/', 'testnet', 769823086);
        expect(link).toBe('/arcron/console/?network=testnet&app=769823086');
        const search = link.slice(link.indexOf('?'));
        expect(entryFrom(search, 'localnet', () => '42')).toEqual({
            network: 'testnet',
            appId: 769823086,
        });
    });

    test('an empty registry round-trips as empty, not as the canonical app', () => {
        const link = entryLink('/arcron/console/', 'testnet', null);
        expect(entryFrom(link.slice(link.indexOf('?')), null, () => '42').appId).toBeNull();
    });
});
