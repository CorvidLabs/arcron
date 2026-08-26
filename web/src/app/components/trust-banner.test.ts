/**
 * What the console warns about that is not the app's identity.
 *
 * Identity moved to `quarantine-panel.ts`, which gates rather than warns, and
 * `quarantine-panel.test.ts` covers it. What is left here still must not be
 * gated on a successful read: box contents belong to whoever owns the app, so
 * a hostile deployment that plants one undecodable box pins the status at
 * `error`, and any warning conditioned on `ready` disappears exactly when it
 * is needed.
 */

import { describe, expect, test } from 'bun:test';

import { noticesFor } from './trust-banner';

const state = (overrides: Partial<Parameters<typeof noticesFor>[0]> = {}) =>
    noticesFor({
        appId: 769_891_898,
        standing: 'canonical',
        networkLabel: 'TestNet',
        status: 'ready',
        frozen: true,
        undecodableBoxes: 0,
        ...overrides,
    });

describe('what the banner has to say', () => {
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

    test('the published, frozen, healthy app says nothing', () => {
        expect(state().length).toBe(0);
    });

    test('no app id at all says nothing', () => {
        expect(state({ appId: null, frozen: false, undecodableBoxes: 3 }).length).toBe(0);
    });
});

describe('a network with no published deployment', () => {
    test('says so rather than staying silent', () => {
        // LocalNet has no defaultAppId, and neither will MainNet in the window
        // between adding the network and deploying to it. Nothing can be
        // quarantined there because there is nothing to compare against, so
        // this is the one identity notice that stays here.
        expect(
            state({ standing: 'unverifiable', networkLabel: 'LocalNet' }).some((n) =>
                n.headline.includes('No published app'),
            ),
        ).toBe(true);
    });

    test('still fires when the connection is failing', () => {
        expect(
            state({ standing: 'unverifiable', status: 'error' }).some((n) =>
                n.headline.includes('No published app'),
            ),
        ).toBe(true);
    });

    test('and does not hide the freeze warning behind itself', () => {
        // Ranked, not exclusive. The first version returned at most one
        // notice, so somebody running their own deployment saw the identity
        // warning forever and never learned their own app was unfrozen.
        const notices = state({ standing: 'unverifiable', frozen: false });
        expect(notices.some((n) => n.headline.includes('No published app'))).toBe(true);
        expect(notices.some((n) => n.headline.includes('not frozen'))).toBe(true);
    });

    test('a quarantined app is not warned about twice', () => {
        // The quarantine panel owns that message, and repeating it here as a
        // paragraph is what made it readable-past in the first place.
        const notices = state({ standing: 'foreign' });
        expect(notices.length).toBe(0);
    });

    test('but a quarantined app still gets everything else', () => {
        const notices = state({ standing: 'foreign', frozen: false, undecodableBoxes: 2 });
        expect(notices.some((n) => n.headline.includes('not frozen'))).toBe(true);
        expect(notices.some((n) => n.headline.includes('does not decode'))).toBe(true);
    });
});
