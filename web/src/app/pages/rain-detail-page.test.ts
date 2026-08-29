/**
 * A rain's own page must not offer the register form, and Back must return
 * to the hub rather than the registry or `/register`.
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { describe, expect, test } from 'bun:test';

const SOURCE = readFileSync(join(import.meta.dirname, 'rain-detail-page.ts'), 'utf8');

describe('a rain detail', () => {
  test('Back to rains is the hub, not the registry or register', () => {
    expect(SOURCE).toContain('routerLink="/rain"');
    expect(SOURCE).toContain('Back to rains');
    expect(SOURCE).not.toContain('routerLink="/register"');
    expect(SOURCE).not.toMatch(/Back to rains[\s\S]{0,80}routerLink="\/"/);
  });

  test('missing rains send people to the hub, not to register', () => {
    expect(SOURCE).toContain('See what is open');
    expect(SOURCE).toContain('No rain {{ id() }} on this hub.');
    expect(SOURCE).not.toContain('Register an upkeep');
  });

  test('WAVE and SPLIT facts both live here', () => {
    expect(SOURCE).toContain('This drop');
    expect(SOURCE).toContain('Enter this rain');
    expect(SOURCE).toContain('I am here');
    expect(SOURCE).toContain('What anyone can do here');
  });

  test('the prize ASA id is on the page so people can opt in', () => {
    expect(SOURCE).toContain('<h3>Prize</h3>');
    expect(SOURCE).toContain('kind="asset"');
    expect(SOURCE).toContain('state.prizeAsset.toString()');
    expect(SOURCE).toContain('Opt in to');
    expect(SOURCE).toContain('Connect to opt in to ASA');
    expect(SOURCE).toContain('ticket box');
  });

  test('a gated rain names the collection token, not the mascot', () => {
    expect(SOURCE).toContain('Who can enter');
    expect(SOURCE).toContain('gateAssetId');
    expect(SOURCE).not.toContain('mascot');
  });
});
