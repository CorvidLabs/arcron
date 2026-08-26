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
        unreadableBoxes: 0,
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

describe('a box the node would not hand over is not an accusation', () => {
    test('an unreadable box raises no notice at all', () => {
        // Cancelling an upkeep deletes its box. The console lists boxes, then
        // reads each one, and a cancel lands between the two. Before fetching
        // was separated from decoding, that produced "this app is holding data
        // shaped like an upkeep and is not one, which usually means it is a
        // different contract wearing these box names" against the visitor's
        // own honest deployment, immediately after they did exactly what the
        // console told them to do.
        const notices = state({ unreadableBoxes: 1 });
        expect(notices.some((n) => n.headline.includes('does not decode'))).toBe(false);
        expect(notices.some((n) => n.detail.includes('different contract'))).toBe(false);
    });

    test('many unreadable boxes are still not an accusation', () => {
        // A rate-limited node answers nothing for anything, so this is the
        // shape of a 403 during a full read, not of a hostile app.
        const notices = state({ unreadableBoxes: 11 });
        expect(notices.some((n) => n.detail.includes('different contract'))).toBe(false);
    });

    test('a box that decodes wrongly IS still an accusation', () => {
        // The distinction has to cut both ways, or the fix has quietly deleted
        // a real security warning. Box contents belong to whoever owns the app.
        const notices = state({ undecodableBoxes: 1 });
        expect(notices.some((n) => n.detail.includes('different contract'))).toBe(true);
    });

    test('an unreadable box does not mask a real one', () => {
        const notices = state({ unreadableBoxes: 5, undecodableBoxes: 1 });
        expect(notices.some((n) => n.headline.includes('1 box here does not decode'))).toBe(true);
    });
});
