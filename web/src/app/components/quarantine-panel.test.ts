/**
 * The visible half of the quarantine.
 *
 * `quarantine.ts` decides that money is locked; this decides whether the
 * visitor is told why in words they can act on. "Not the published app" is
 * useless without both numbers, so both ids are asserted here rather than the
 * shape of the sentence around them.
 */

import { describe, expect, test } from 'bun:test';

import { NETWORKS } from '@corvidlabs/arcron/networks';

import { quarantineNotice } from './quarantine-panel';
import { standingOf } from '../core/quarantine';

const CANONICAL = NETWORKS.testnet.defaultAppId as number;
const HOSTILE = 999_999_999;

const notice = (appId: number | null, network: 'testnet' | 'localnet', accepted = false) =>
    quarantineNotice({
        appId,
        canonicalAppId: NETWORKS[network].defaultAppId ?? null,
        networkLabel: NETWORKS[network].label,
        standing: standingOf({ appId, network }),
        accepted,
    });

describe('what a poisoned link is told', () => {
    test('it names both ids, so there is something to compare', () => {
        const found = notice(HOSTILE, 'testnet');
        expect(found).not.toBeNull();
        expect(found?.linkedAppId).toBe(HOSTILE);
        expect(found?.canonicalAppId).toBe(CANONICAL);
        expect(found?.detail).toContain(String(HOSTILE));
        expect(found?.detail).toContain(String(CANONICAL));
    });

    test('it says this is not the Arcron deployment, not that it might not be', () => {
        expect(notice(HOSTILE, 'testnet')?.headline).toContain('not the Arcron deployment');
    });

    test('it reports that money is locked, and stops saying so once accepted', () => {
        expect(notice(HOSTILE, 'testnet')?.moneyLocked).toBe(true);
        expect(notice(HOSTILE, 'testnet', true)?.moneyLocked).toBe(false);
    });

    test('an accepted app is still shown, because it is still not Arcron', () => {
        // Accepting unlocks the buttons. It does not turn a look-alike into
        // the real deployment, and the page should not start pretending it did.
        expect(notice(HOSTILE, 'testnet', true)).not.toBeNull();
    });

    test('the published app says nothing at all', () => {
        expect(notice(CANONICAL, 'testnet')).toBeNull();
    });

    test('LocalNet says nothing, because there is nothing to compare against', () => {
        // The trust banner carries the "no published app is recorded" warning
        // for this case. Quarantining it would break every developer.
        expect(notice(1_001, 'localnet')).toBeNull();
    });

    test('no app id at all says nothing', () => {
        expect(notice(null, 'testnet')).toBeNull();
    });
});
