/**
 * A production build must not depend on which dev commands you have run.
 *
 * `public/axe.min.js` is a gitignored local copy that the accessibility
 * workflow asks you to drop in so `ng serve` can offer it. Because it lives in
 * `public/`, a production build picked it up and shipped 567 KB of test
 * harness to every visitor — on a developer machine. CI never reproduced it,
 * because CI never has the file, so the bundle was clean there and dirty
 * wherever it was actually built for hosting.
 *
 * That asymmetry is why this is asserted against the configuration rather than
 * against a built bundle: a test that greps `dist/` would pass in CI for the
 * wrong reason and protect nothing.
 */

import { describe, expect, test } from 'bun:test';

import angular from '../angular.json';

const build = angular.projects.web.architect.build;

describe('production assets', () => {
    test('the accessibility harness is excluded', () => {
        const assets = build.configurations.production.assets;
        expect(assets).toBeDefined();
        const fromPublic = assets.find((asset) => asset.input === 'public');
        expect(fromPublic?.ignore).toContain('axe.min.js');
    });

    test('development still serves it, so the documented a11y workflow works', () => {
        // `ng serve` defaults to the development configuration, which inherits
        // the base assets rather than the production override.
        expect(build.configurations.development).not.toHaveProperty('assets');
        const base = build.options.assets.find((asset) => asset.input === 'public');
        expect(base?.ignore).toBeUndefined();
    });
});
