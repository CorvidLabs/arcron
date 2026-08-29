/**
 * Opening a rain is a rain page, not Register an upkeep.
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { describe, expect, test } from 'bun:test';

const PAGE = readFileSync(join(import.meta.dirname, 'rain-create-page.ts'), 'utf8');
const FORM = readFileSync(join(import.meta.dirname, '../components/rain-create-form.ts'), 'utf8');

describe('open a rain', () => {
  test('Back to rains is the hub, not the registry or register', () => {
    expect(PAGE).toContain('routerLink="/rain"');
    expect(PAGE).toContain('Back to rains');
    expect(PAGE).not.toContain('routerLink="/register"');
    expect(PAGE).not.toMatch(/Back to rains[\s\S]{0,80}routerLink="\/"/);
  });

  test('the form is a rain, not an upkeep', () => {
    expect(FORM).toContain('Who it falls on');
    expect(FORM).toContain('Open this rain');
    expect(FORM).not.toContain('Register an upkeep');
    expect(PAGE).not.toContain('Register an upkeep');
  });
});
