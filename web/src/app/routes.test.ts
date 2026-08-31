/**
 * Three destinations — registry, one upkeep, register — and three stubs where
 * Rain used to be.
 *
 * Rain moved to its own repository and its own address. The three paths are
 * kept, in the same order, because shared links are how this console spreads
 * and a deleted path renders the registry rather than 404ing under the
 * index.html fallback. Bound to the array the application boots with.
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { describe, expect, test } from 'bun:test';

import { routerOptions, routes } from './routes';

const STUBS = readFileSync(join(import.meta.dirname, 'pages', 'rain-moved.ts'), 'utf8');

describe('the console has three destinations and three forwarding stubs', () => {
    test('exactly the registry, one upkeep, the register form, and the three old rain paths', () => {
        expect(routes.filter((route) => route.path !== '**').map((route) => route.path)).toEqual([
            '',
            'u/:id',
            'register',
            'rain',
            'rain/new',
            'rain/:id',
        ]);
    });

    test('rain/new is declared before rain/:id, so it still forwards as "new" and not as rain 0', () => {
        const paths = routes.map((route) => route.path);
        expect(paths.indexOf('rain/new')).toBeLessThan(paths.indexOf('rain/:id'));
    });

    test('the three rain paths forward rather than render, so an old link is never quietly wrong', () => {
        // The whole point of keeping them. Deleting the paths would let the
        // index.html fallback answer `/rain/3` with the registry, showing a
        // developer console to somebody who was sent one rain.
        for (const path of ['rain', 'rain/new', 'rain/:id']) {
            const route = routes.find((candidate) => candidate.path === path);
            expect(route?.title).toBe('Rain has moved');
            expect(String(route?.loadComponent)).toContain('rain-moved');
        }
    });

    test('the stubs leave for the new address, and none of them forwards an id', () => {
        // Read as source rather than imported: importing an Angular component
        // needs the compiler this runner does not carry, and the only thing
        // worth pinning is the destination a shared link ends up at.
        expect(STUBS).toContain("const RAIN = 'https://corvidlabs.xyz/rain/'");
        expect(STUBS).toContain('window.location.replace(destination)');
        expect(STUBS).toContain("RAIN + 'new'");
        // `/rain/:id` deliberately lands on the rain list. It forwarded
        // `RAIN + 'r/' + id` until 2026-08-31, which was right while one hub
        // sat behind both addresses; rain then redeployed from 770130162 onto
        // 770746178, and a rain's id is a box id on one hub, so the same
        // number over there is a different draw or none. Pinned as an absence
        // because the failure it guards is silent: a forwarded id renders a
        // plausible wrong rain rather than an error anybody would notice.
        expect(STUBS).not.toContain("RAIN + 'r/'");
        expect(STUBS).toContain("protected readonly id = this.route.snapshot.paramMap.get('id') ?? ''");
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
