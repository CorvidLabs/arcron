/**
 * The control that closes the look-alike-app attack.
 *
 * Its first version returned early unless the connection was `ready`, and
 * `ready` is only reached after every box in the app has been read and
 * decoded. Box contents belong to whoever owns the app, so one malformed box
 * pinned the status at `error` and the warning never rendered once, while the
 * register button stayed live. The identity comparison needs no chain data at
 * all, which is what makes that gate indefensible.
 */

import { describe, expect, test } from 'bun:test';

import { noticesFor } from './trust-banner';

const HOSTILE = 999_999_999;
const CANONICAL = 769_891_898;

const state = (overrides: Partial<Parameters<typeof noticesFor>[0]> = {}) =>
    noticesFor({
        appId: CANONICAL,
        network: 'testnet',
        networkLabel: 'TestNet',
        status: 'ready',
        frozen: true,
        undecodableBoxes: 0,
        ...overrides,
    });

describe('the look-alike warning', () => {
    test('fires for an app that is not the published one', () => {
        const notices = state({ appId: HOSTILE });
        expect(notices[0].headline).toContain('not the published app');
        expect(notices[0].tone).toBe('bad');
    });

    test('still fires when the connection is failing', () => {
        // The attack: a hostile app plants one box that will not decode, the
        // read throws, status pins at error, and every warning disappears.
        const notices = state({ appId: HOSTILE, status: 'error' });
        expect(notices.some((n) => n.headline.includes('not the published app'))).toBe(true);
    });

    test('still fires while the connection is only connecting', () => {
        const notices = state({ appId: HOSTILE, status: 'connecting' });
        expect(notices.some((n) => n.headline.includes('not the published app'))).toBe(true);
    });

    test('offers a way back to the published app', () => {
        // Without this the poisoned id persists and nothing in the UI names
        // the number to return to.
        expect(state({ appId: HOSTILE })[0].canonical).toBe(CANONICAL);
    });

    test('says nothing when the app is the published one', () => {
        expect(state().length).toBe(0);
    });
});

describe('what else the banner has to say', () => {
    test('a stale read is called out, not silently rendered', () => {
        expect(state({ status: 'error' }).some((n) => n.headline.includes('not showing you'))).toBe(
            true,
        );
    });

    test('undecodable boxes are surfaced, since an honest app has none', () => {
        expect(state({ undecodableBoxes: 1 }).some((n) => n.headline.includes('does not decode'))).toBe(
            true,
        );
    });

    test('an unfrozen deployment is warned about', () => {
        expect(state({ frozen: false }).some((n) => n.headline.includes('not frozen'))).toBe(true);
    });

    test('a self-hoster sees the freeze warning too, not only the identity one', () => {
        // Ranked, not exclusive. The first version returned at most one
        // notice, so somebody running their own deployment saw the identity
        // warning forever and never learned their own app was unfrozen.
        const notices = state({ appId: HOSTILE, frozen: false });
        expect(notices.some((n) => n.headline.includes('not the published app'))).toBe(true);
        expect(notices.some((n) => n.headline.includes('not frozen'))).toBe(true);
    });

    test('a network with no published app says so rather than staying silent', () => {
        // LocalNet has no defaultAppId, and neither will MainNet in the
        // window between adding the network and deploying to it.
        expect(
            state({ network: 'localnet' }).some((n) => n.headline.includes('No published app')),
        ).toBe(true);
    });
});
