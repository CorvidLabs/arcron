/**
 * Six destinations: registry, one upkeep, register, Rain, open a rain, one rain.
 *
 * Rain is a separate contract and a holder-facing surface, so it is a route
 * rather than a tab. Opening a rain is its own page, declared before
 * `rain/:id` so "new" is not an id. Bound to the array the application boots with.
 */

import { describe, expect, test } from 'bun:test';

import { routerOptions, routes } from './routes';

describe('the console has six destinations', () => {
    test('exactly the registry, one upkeep, the register form, rain, open a rain, and one rain', () => {
        expect(routes.filter((route) => route.path !== '**').map((route) => route.path)).toEqual([
            '',
            'u/:id',
            'register',
            'rain',
            'rain/new',
            'rain/:id',
        ]);
    });

    test('rain/new is declared before rain/:id, so opening one is not rain 0', () => {
        const paths = routes.map((route) => route.path);
        expect(paths.indexOf('rain/new')).toBeLessThan(paths.indexOf('rain/:id'));
    });

    test('every one of them loads a component', () => {
        for (const route of routes) {
            if (route.path === '**') continue;
            expect(typeof route.loadComponent).toBe('function');
        }
    });

    test('the root matches only the empty path, so `/register` is not swallowed', () => {
        expect(routes.find((route) => route.path === '')?.pathMatch).toBe('full');
    });

    test('an unknown path falls back to the registry rather than a blank page', () => {
        const wildcard = routes.at(-1);
        expect(wildcard?.path).toBe('**');
        expect(wildcard?.redirectTo).toBe('');
    });

    test('each destination sets a title, so a tab and a back button are readable', () => {
        for (const route of routes) {
            if (route.path === '**') continue;
            expect(typeof route.title).toBe('string');
        }
    });
});

describe('the two query parameters survive navigation', () => {
    test('the router preserves them by default, so no link has to remember', () => {
        // Without this every routerLink drops `?network=` and `?app=`, and the
        // console silently falls back to whatever the browser last
        // remembered, which is exactly the failure `entry.ts` exists to
        // prevent: two people following the same link see different
        // registries.
        //
        // Asserted against the object `app.config.ts` hands to
        // `withRouterConfig`. Booting the router itself would need the JIT
        // compiler, which this test runner does not carry.
        expect(routerOptions.defaultQueryParamsHandling).toBe('preserve');
    });
});
