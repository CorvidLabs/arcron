/**
 * Links out of the console.
 *
 * `explorerApp` was defined in `js/src/networks.ts` and called by nothing, so
 * every app id, target, account and transaction in the console was plain text.
 * The property worth pinning is the one that is easy to get wrong when adding
 * a network: a chain with no public explorer must return nothing rather than a
 * link that goes nowhere.
 */

import { describe, expect, test } from 'bun:test';

import { explorerUrl } from './explorer';

describe('explorerUrl', () => {
    test('links an app id on a network that has an explorer', () => {
        expect(explorerUrl('testnet', 'app', '769891898')).toContain('/application/769891898');
    });

    test('links an account', () => {
        const address = 'M4YFP33L5VIFRF53X53WUMQWBOWSLYQNBSSAJV2SORGF43L36XBY7OREUA';
        expect(explorerUrl('testnet', 'account', address)).toContain(`/address/${address}`);
    });

    test('links a transaction', () => {
        expect(explorerUrl('testnet', 'transaction', 'ABC123')).toContain('/tx/ABC123');
    });

    test('links an asset', () => {
        expect(explorerUrl('testnet', 'asset', '770131837')).toContain('/asset/770131837');
    });

    test('every link is to the TestNet explorer, not MainNet', () => {
        // A MainNet explorer showing "no such application" for a TestNet id is
        // the kind of link that makes a visitor doubt the app rather than the
        // link.
        for (const url of [
            explorerUrl('testnet', 'app', '1'),
            explorerUrl('testnet', 'account', 'A'),
            explorerUrl('testnet', 'transaction', 'B'),
            explorerUrl('testnet', 'asset', '1'),
        ]) {
            expect(url).toContain('testnet.');
        }
    });

    test('LocalNet has no explorer, and says so by returning nothing', () => {
        // Nothing outside the machine can reach a LocalNet chain, so a link
        // there is worse than plain text. Every caller renders text on null.
        expect(explorerUrl('localnet', 'app', '1002')).toBeNull();
        expect(explorerUrl('localnet', 'account', 'A')).toBeNull();
        expect(explorerUrl('localnet', 'transaction', 'B')).toBeNull();
        expect(explorerUrl('localnet', 'asset', '1')).toBeNull();
    });
});
