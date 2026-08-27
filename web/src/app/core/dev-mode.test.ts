/**
 * Dev mode decides whether the console has controls at all, so it has to be
 * hard to turn on by accident and impossible to turn on from a link somebody
 * else wrote without the visitor also carrying `?dev=1`.
 *
 * It also has to be impossible for one link to turn dev mode on *and* redirect
 * the console in the same navigation. A review pointed out that the module's
 * own comment claimed a poisoned link was "inert for everyone who is not
 * already editing this code", which was false: `?dev=1` is a public query
 * parameter, so `?dev=1&app=<look-alike>` made anybody "already editing this
 * code" and re-armed `?app=` on the same visit. `established` is the fix, and
 * these tests are what stop it being quietly undone.
 */

import { describe, expect, test } from 'bun:test';

import { DEV_STORAGE_KEY, devModeFrom, type DevStorage } from './dev-mode';

/** A storage that records what was written, so persistence can be asserted. */
function memoryStorage(initial: Record<string, string> = {}): DevStorage & {
    readonly data: Record<string, string>;
} {
    const data: Record<string, string> = { ...initial };
    return {
        data,
        getItem: (key) => data[key] ?? null,
        setItem: (key, value) => {
            data[key] = value;
        },
        removeItem: (key) => {
            delete data[key];
        },
    };
}

/** A storage that throws on every access, as a browser blocking site data does. */
const hostileStorage: DevStorage = {
    getItem() {
        throw new Error('site data blocked');
    },
    setItem() {
        throw new Error('site data blocked');
    },
    removeItem() {
        throw new Error('site data blocked');
    },
};

describe('turning developer controls on', () => {
    test('off by default, which is the state a stranger arrives in', () => {
        expect(devModeFrom('', memoryStorage()).enabled).toBe(false);
    });

    test('?dev=1 turns it on', () => {
        expect(devModeFrom('?dev=1', memoryStorage()).enabled).toBe(true);
    });

    test('and is remembered, so navigation does not lose it', () => {
        const storage = memoryStorage();
        devModeFrom('?dev=1', storage);
        expect(storage.data[DEV_STORAGE_KEY]).toBe('1');
        expect(devModeFrom('', storage).enabled).toBe(true);
    });

    test('?dev=0 turns it off and forgets', () => {
        const storage = memoryStorage({ [DEV_STORAGE_KEY]: '1' });
        const state = devModeFrom('?dev=0', storage);
        expect(state.enabled).toBe(false);
        expect(state.established).toBe(false);
        expect(storage.data[DEV_STORAGE_KEY]).toBeUndefined();
    });

    test('an unrelated parameter does not turn it on', () => {
        for (const search of ['?app=999', '?network=localnet', '?debug=1', '?dev=', '?dev=yes']) {
            expect(devModeFrom(search, memoryStorage()).enabled).toBe(false);
        }
    });
});

describe('one link cannot both enable dev mode and redirect', () => {
    test('the navigation that turns dev mode on is not established', () => {
        // This is the whole finding. `?dev=1&app=<look-alike>` enables dev
        // mode, and `established` staying false is what keeps `?app=` inert on
        // that same visit.
        const state = devModeFrom('?dev=1&app=999999', memoryStorage());
        expect(state.enabled).toBe(true);
        expect(state.established).toBe(false);
    });

    test('but the next navigation is, so a developer is not obstructed', () => {
        const storage = memoryStorage();
        devModeFrom('?dev=1', storage);
        const next = devModeFrom('?app=999999', storage);
        expect(next.enabled).toBe(true);
        expect(next.established).toBe(true);
    });

    test('re-sending ?dev=1 when it is already on stays established', () => {
        // A developer who bookmarks a link carrying ?dev=1 should not lose the
        // ability to use ?app= on every visit.
        const storage = memoryStorage({ [DEV_STORAGE_KEY]: '1' });
        expect(devModeFrom('?dev=1&app=123', storage).established).toBe(true);
    });

    test('turning it off and on again is not established again', () => {
        const storage = memoryStorage({ [DEV_STORAGE_KEY]: '1' });
        devModeFrom('?dev=0', storage);
        expect(devModeFrom('?dev=1&app=999999', storage).established).toBe(false);
    });
});

describe('when the browser refuses storage', () => {
    test('a throwing storage does not break the console', () => {
        expect(() => devModeFrom('', hostileStorage)).not.toThrow();
        expect(devModeFrom('', hostileStorage).enabled).toBe(false);
    });

    test('and ?dev=1 still works for the length of the visit', () => {
        expect(() => devModeFrom('?dev=1', hostileStorage)).not.toThrow();
        expect(devModeFrom('?dev=1', hostileStorage).enabled).toBe(true);
    });

    test('but never establishes, so ?app= stays inert without storage', () => {
        // Without storage there is no way to tell a returning developer from a
        // first-time visitor following a link, so the safe answer is the one
        // that keeps `?app=` inert.
        expect(devModeFrom('?dev=1&app=999999', hostileStorage).established).toBe(false);
    });

    test('a missing storage is treated as no storage, not as an error', () => {
        expect(devModeFrom('', null).enabled).toBe(false);
        expect(devModeFrom('?dev=1', null).enabled).toBe(true);
        expect(devModeFrom('?dev=1', null).established).toBe(false);
    });
});
