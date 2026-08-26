/**
 * Three destinations, and no more.
 *
 * The count is the decision, not an implementation detail: an earlier plan
 * copied five destinations from NFDomains onto a registry of five rows, and
 * `docs/console-plan.md` deferred four of them until something needs a fourth
 * reading. A test that only checks the three exist would let a sidebar with
 * two more slip in beside them, so the shape of the whole table is asserted.
 *
 * Bound to the array the application boots with, not to a copy of it.
 */

import { describe, expect, test } from 'bun:test';

import { routerOptions, routes } from './routes';

describe('the console has three destinations', () => {
    test('exactly the registry, one upkeep, and the register form', () => {
        expect(routes.filter((route) => route.path !== '**').map((route) => route.path)).toEqual([
            '',
            'u/:id',
            'register',
        ]);
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
