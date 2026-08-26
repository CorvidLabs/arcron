/**
 * Dev mode decides whether the console has controls at all, so it has to be
 * hard to turn on by accident and impossible to turn on from a link somebody
 * else wrote without the visitor also carrying `?dev=1`.
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
        expect(devModeFrom('', memoryStorage())).toBe(false);
    });

    test('?dev=1 turns it on', () => {
        expect(devModeFrom('?dev=1', memoryStorage())).toBe(true);
    });

    test('and is remembered, so navigation does not lose it', () => {
        const storage = memoryStorage();
        devModeFrom('?dev=1', storage);
        expect(storage.data[DEV_STORAGE_KEY]).toBe('1');
        expect(devModeFrom('', storage)).toBe(true);
    });

    test('?dev=0 turns it off and forgets', () => {
        const storage = memoryStorage({ [DEV_STORAGE_KEY]: '1' });
        expect(devModeFrom('?dev=0', storage)).toBe(false);
        expect(storage.data[DEV_STORAGE_KEY]).toBeUndefined();
    });

    test('an unrelated parameter does not turn it on', () => {
        // The failure that matters: a link written by somebody else must not
        // be able to reveal the app id field on a stranger's console, because
        // that is the control the whole look-alike attack needs.
        for (const search of ['?app=999', '?network=localnet', '?debug=1', '?dev=', '?dev=yes']) {
            expect(devModeFrom(search, memoryStorage())).toBe(false);
        }
    });
});

describe('when the browser refuses storage', () => {
    test('a throwing storage does not break the console', () => {
        // A private window, or a browser set to block site data, throws on
        // access rather than returning null. A console that cannot open is
        // worse than one without dev mode.
        expect(() => devModeFrom('', hostileStorage)).not.toThrow();
        expect(devModeFrom('', hostileStorage)).toBe(false);
    });

    test('and ?dev=1 still works for the length of the visit', () => {
        expect(() => devModeFrom('?dev=1', hostileStorage)).not.toThrow();
        expect(devModeFrom('?dev=1', hostileStorage)).toBe(true);
    });

    test('a missing storage is treated as no storage, not as an error', () => {
        expect(devModeFrom('', null)).toBe(false);
        expect(devModeFrom('?dev=1', null)).toBe(true);
    });
});
