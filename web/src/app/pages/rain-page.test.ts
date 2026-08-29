/**
 * The Open-a-rain CTA must stay a rain route.
 *
 * `href="#create"` resolves against `<base href="/">` as `/#create`, which
 * is the registry. The registry's primary action is "Register an upkeep".
 * A fragment on `/rain` kept people on rain but pinned the form under the
 * table. `/rain/new` is the rain equivalent of `/register`: a page, not a
 * hash. Lock the markup here, and the click in `e2e/rain.pw.ts`.
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { describe, expect, test } from 'bun:test';

const SOURCE = readFileSync(join(import.meta.dirname, 'rain-page.ts'), 'utf8');

describe('Open a rain', () => {
  test('is a rain route, not a hash-only href or a fragment on the list', () => {
    expect(SOURCE).not.toContain('href="#create"');
    expect(SOURCE).not.toContain('fragment="create"');
    expect(SOURCE).toContain('routerLink="/rain/new"');
  });

  test('does not point at register or the registry root', () => {
    expect(SOURCE).not.toMatch(/Open a rain[\s\S]{0,120}routerLink="\/register"/);
    expect(SOURCE).not.toMatch(/Open a rain[\s\S]{0,120}routerLink="\/"/);
    expect(SOURCE).not.toContain('routerLink="/register"');
  });

  test('the empty-state Open one uses the same rain route', () => {
    expect(SOURCE).toMatch(/Open one[\s\S]{0,80}to start a drip/);
    const empty = SOURCE.slice(SOURCE.indexOf('No rains on this hub yet'));
    expect(empty).toContain('routerLink="/rain/new"');
    expect(empty.slice(0, 400)).not.toContain('href="#create"');
    expect(empty.slice(0, 400)).not.toContain('fragment="create"');
  });

  test('the list does not carry the create form', () => {
    expect(SOURCE).not.toContain('Who it falls on');
    expect(SOURCE).not.toContain('Open this rain');
    expect(SOURCE).not.toContain('Register an upkeep');
  });

  test('the whole row is one rain link, not the id and the name separately', () => {
    expect(SOURCE).toContain('class="row-link"');
    expect(SOURCE).toContain("['/rain', row.id]");
    expect(SOURCE).not.toContain('(click)="open(row.id, $event)"');
    expect(SOURCE).not.toContain('<a [routerLink]="[\'/rain\', row.id]">{{ row.id }}</a>');
    expect(SOURCE).toContain('class="identity"');
    expect(SOURCE).toContain('row.gate');
  });

  test('the state chip lives on Next, not jammed against the id', () => {
    expect(SOURCE).not.toContain('class="id-cell"');
    const next = SOURCE.slice(SOURCE.indexOf('data-label="Next"'));
    expect(next).toContain('chip');
    const identity = SOURCE.slice(SOURCE.indexOf('class="identity"'), SOURCE.indexOf('data-label="Pays"'));
    expect(identity).not.toContain('class="chip"');
  });

  test('an ASA rain shows the asset id on the row', () => {
    expect(SOURCE).toContain('row.prizeId');
    expect(SOURCE).toContain('row.gateId');
    expect(SOURCE).toContain('opt in');
  });

  test('the collection picture is an NFT image, not the mascot', () => {
    expect(SOURCE).toContain('class="thumb"');
    expect(SOURCE).not.toContain('mascot');
    expect(SOURCE).not.toContain('brand/');
  });
});
