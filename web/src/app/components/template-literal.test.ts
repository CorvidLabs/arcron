/**
 * No component's template or styles literal may be cut short by a stray backtick.
 *
 * Angular components here hold their template and styles in template literals,
 * so a backtick anywhere inside one — including inside a CSS or HTML comment —
 * ends the literal early. The build then fails somewhere unrelated: the last
 * one reported "TS2304: Cannot find name 'scale'" from a sentence explaining
 * why a CSS transform was the wrong fix for a touch target.
 *
 * That happened three times in one afternoon, every time in a comment written
 * to explain a fix, and every time it cost a build cycle to find. This is
 * cheaper than the next one.
 *
 * The check is indirect on purpose. Rather than parse TypeScript, it asks
 * whether every block comment inside a literal is closed: a stray backtick ends
 * the literal mid-comment, so the opener count and closer count stop matching.
 */

import { describe, expect, test } from 'bun:test';
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

const BACKTICK = String.fromCharCode(96);

/** Every non-test .ts file under web/src/app, recursively. */
function sourceFiles(dir: string): string[] {
    const found: string[] = [];
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
        const path = join(dir, entry.name);
        if (entry.isDirectory()) found.push(...sourceFiles(path));
        else if (entry.name.endsWith('.ts') && !entry.name.endsWith('.test.ts')) found.push(path);
    }
    return found;
}

/** The text between `property:` and the next backtick — what the compiler takes. */
function literalBodies(source: string, property: string): string[] {
    const bodies: string[] = [];
    const marker = property + ':';
    let from = 0;
    for (;;) {
        const at = source.indexOf(marker, from);
        if (at === -1) return bodies;
        const open = source.indexOf(BACKTICK, at);
        if (open === -1) return bodies;
        // Only treat it as the literal if nothing but whitespace separates them.
        if (source.slice(at + marker.length, open).trim() !== '') {
            from = at + marker.length;
            continue;
        }
        const close = source.indexOf(BACKTICK, open + 1);
        if (close === -1) return bodies;
        bodies.push(source.slice(open + 1, close));
        from = close + 1;
    }
}

describe('component template and style literals', () => {
    const files = sourceFiles(join(import.meta.dir, '..'));

    test('there are components to check', () => {
        expect(files.length).toBeGreaterThan(5);
    });

    test('no literal ends inside a comment, which is what a stray backtick does', () => {
        const broken: string[] = [];
        for (const file of files) {
            const source = readFileSync(file, 'utf8');
            for (const property of ['template', 'styles']) {
                for (const body of literalBodies(source, property)) {
                    const opens = (body.match(/\/\*/g) ?? []).length;
                    const closes = (body.match(/\*\//g) ?? []).length;
                    if (opens !== closes) {
                        broken.push(`${file} — ${property} literal ends inside a comment`);
                    }
                }
            }
        }
        expect(broken).toEqual([]);
    });
});
