/**
 * Serve a built console over HTTP, with the single-page fallback.
 *
 * The Playwright suite has to work from a cold checkout with nothing else
 * running, so it builds the console and serves it itself rather than assuming
 * somebody has `ng serve` up. The fallback is not optional: the console routes
 * on the path, so a request for `/u/7` names no file on disk, and without
 * handing it `index.html` every deep-linked upkeep in the suite would 404.
 * That is the same rule `fledge run web-verify-hosted` checks for the hosted
 * bundle, enforced here for the same reason.
 *
 *   bun run scripts/serve-static.ts <directory> [port]
 */

import { existsSync, statSync } from 'node:fs';
import { join, normalize } from 'node:path';

const [directory, portArgument] = process.argv.slice(2);
if (directory === undefined) {
  console.error('usage: serve-static.ts <directory> [port]');
  process.exit(1);
}

const port = Number(portArgument ?? 4300);
const root = normalize(directory);
const index = join(root, 'index.html');

if (!existsSync(index)) {
  console.error(`serve-static: ${index} does not exist. Build first.`);
  process.exit(1);
}

function resolve(pathname: string): string | null {
  // `..` in a request must never escape the served directory, even in a test
  // server: this thing runs on a developer's machine with their home directory
  // one level up.
  const candidate = normalize(join(root, decodeURIComponent(pathname)));
  if (!candidate.startsWith(root)) return null;
  if (!existsSync(candidate)) return null;
  const stats = statSync(candidate);
  if (stats.isDirectory()) {
    const nested = join(candidate, 'index.html');
    return existsSync(nested) ? nested : null;
  }
  return candidate;
}

const server = Bun.serve({
  port,
  hostname: '127.0.0.1',
  async fetch(request) {
    const { pathname } = new URL(request.url);
    const file = resolve(pathname) ?? index;
    return new Response(Bun.file(file), {
      headers: { 'cache-control': 'no-store' },
    });
  },
});

console.log(`serve-static: ${root} on http://127.0.0.1:${server.port}`);
