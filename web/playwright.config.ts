/**
 * The rendering suite: build the console, serve it, and look at it.
 *
 * Self-contained on purpose. `webServer` builds the production bundle and
 * serves it with the single-page fallback, so the suite runs from a cold
 * checkout with nothing else started - no `ng serve`, no LocalNet, no TestNet,
 * no network at all. The chain it reads is stubbed at the HTTP boundary in
 * `e2e/chain.ts`, which is what makes a round number stop being a source of
 * failure.
 *
 * Chromium only, deliberately. This suite is not asking whether the console
 * works in three engines; it is asking what the CSS resolves to and where the
 * boxes land, and one engine answers that in a third of the time. Cross-engine
 * behaviour is a different suite with a different reason to exist.
 */

import { defineConfig, devices } from '@playwright/test';

// Playwright transpiles this config to CommonJS, because web/package.json has
// no `"type": "module"`, so `__dirname` is the portable spelling here and
// `import.meta.url` is a syntax error.
const HERE = __dirname;
// Deliberately not 4200 or 4300. `reuseExistingServer` on a popular port is a
// trap: this suite spent a while auditing an unrelated server that happened to
// be listening, and reported a page whose CSS had nothing to do with this tree.
const PORT = Number(process.env['ARCRON_E2E_PORT'] ?? 4319);
const BASE_URL = `http://127.0.0.1:${PORT}`;

export default defineConfig({
  testDir: './e2e',
  // `.pw.ts`, not `.spec.ts`: `bun test` owns `*.test.ts` and `*.spec.ts` in
  // this package and would otherwise try to run these in its own runner.
  testMatch: '**/*.pw.ts',
  outputDir: './test-results',
  globalSetup: './e2e/global-setup.ts',
  globalTeardown: './e2e/global-teardown.ts',
  fullyParallel: true,
  forbidOnly: Boolean(process.env['CI']),
  // No retries. Every input is pinned, so a test that passes on the second
  // attempt is reporting a bug in the suite and should say so.
  retries: 0,
  reporter: process.env['CI'] ? [['list'], ['html', { open: 'never' }]] : [['list']],
  timeout: 60_000,
  expect: { timeout: 10_000 },
  use: {
    baseURL: BASE_URL,
    // The viewport is set per test; this is only the size the context opens at.
    viewport: { width: 1280, height: 800 },
    // A device pixel ratio of 1 keeps every measurement in CSS pixels.
    deviceScaleFactor: 1,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command:
      'bun run ng build --output-path dist/e2e && ' +
      `bun run scripts/serve-static.ts dist/e2e/browser ${PORT}`,
    url: BASE_URL,
    cwd: HERE,
    // Never reuse. A server already on this port is not necessarily serving
    // this tree, and a rendering audit of somebody else's bundle is worse than
    // no audit at all: it is confidently wrong. The build is a few seconds.
    reuseExistingServer: false,
    timeout: 240_000,
    stdout: 'ignore',
    stderr: 'pipe',
  },
});
