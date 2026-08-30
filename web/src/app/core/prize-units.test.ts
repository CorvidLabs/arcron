/**
 * An ASA pot must never be shown or spent in base units.
 *
 * The hub is permissionless, so anyone can open a rain around a scam-named
 * 6-decimal asset. Unscaled, the console at the canonical address printed
 * that pot a millionfold too large — the trusted front end serving as the
 * lure — and the deposit form did the inverse: typing 10,000 moved 0.01
 * tokens. This pins both directions of the scaling, and pins that unknown
 * decimals read as labelled base units rather than as whole tokens, because
 * a wrong number is worse than a qualified one.
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { describe, expect, test } from 'bun:test';

import { toBaseUnits } from '@corvidlabs/arcron/format';
import { prizeLabel } from '@corvidlabs/arcron/rain';

const SERVICE = readFileSync(join(import.meta.dirname, 'rain.service.ts'), 'utf8');
const DETAIL_PAGE = readFileSync(
    join(import.meta.dirname, '..', 'pages', 'rain-detail-page.ts'),
    'utf8',
);

describe('what an ASA pot reads as', () => {
    test('a 6-decimal pot reads as whole tokens', () => {
        expect(prizeLabel(1_000_000n, 770_131_837n, 'DROP', 6)).toBe('1 DROP');
        expect(prizeLabel(50_000n, 770_131_837n, 'DROP', 6)).toBe('0.05 DROP');
    });

    test('a 0-decimal pot is unchanged', () => {
        expect(prizeLabel(1_000n, 770_131_837n, 'DROP', 0)).toBe('1,000 DROP');
    });

    test('ALGO pots are untouched by any of this', () => {
        expect(prizeLabel(1_000_000n, 0n)).toBe('1 ALGO');
        expect(prizeLabel(50_000n, 0n)).toBe('0.05 ALGO');
    });

    test('decimals the console has not read yet are named, not guessed', () => {
        expect(prizeLabel(1_000_000n, 770_131_837n, 'DROP')).toBe(
            '1,000,000 base units of DROP',
        );
    });

    test('the service hands prizeLabel the decimals it fetched', () => {
        // `decimals` was fetched and then read nowhere; this is the thread.
        expect(SERVICE).toContain("prizeLabel(amount, rain.prizeAsset, info?.unitName ?? '', info?.decimals ?? null)");
    });
});

describe('what a typed deposit sends', () => {
    test('whole tokens convert to base units by the asset decimals', () => {
        expect(toBaseUnits(10_000, 6)).toBe(10_000_000_000n);
        expect(toBaseUnits(1.5, 2)).toBe(150n);
        expect(toBaseUnits(250, 0)).toBe(250n);
    });

    test('the service converts before building the transfer', () => {
        expect(SERVICE).toContain('const baseUnits = toBaseUnits(amount, info.decimals)');
        expect(SERVICE).toContain('Number(rain.prizeAsset), baseUnits)');
    });

    test('the service refuses to deposit while decimals are unknown', () => {
        expect(SERVICE).toContain('Still reading the prize asset.');
    });

    test('the deposit field asks for whole tokens, not base units', () => {
        expect(DETAIL_PAGE).toContain('[step]="prizeStep(state)"');
        expect(DETAIL_PAGE).not.toContain('value="10000"');
    });
});
