"""Verify and stage the console for hosting at corvidlabs.xyz/arcron/console/.

The console has never had an address. Everything that needs somebody outside
this repository to try Arcron is blocked on that: a stranger cannot install
Bun, Poetry and Docker before they are allowed to look. `docs/console-plan.md`
also names a canonical URL as the only real defence against a poisoned link,
so the URL is not a convenience, it is part of the security story.

`scripts/sync_site_docs.py` already publishes the prose to the same site. This
does the same job for the bundle: it verifies the build, then mirrors it into
a checkout of `CorvidLabs/site`, which deploys it. It does not push, commit or
deploy anything. Publishing stays a deliberate act by a human in the other
repository.

    poetry run python -m scripts.publish_console --verify
    poetry run python -m scripts.publish_console --site ../../site --check
    poetry run python -m scripts.publish_console --site ../../site

`--verify` needs no site checkout, which is why it is the part CI runs. It
serves the built bundle from a throwaway directory at the subpath it will be
hosted under and fetches every file in it, because the failure this guards
against does not show up in a build log: `ng build` is perfectly happy to emit
a page whose script tags resolve to the domain root and 404 the moment the app
is served from anywhere but `/`.

Run `fledge run web-build-hosted` first. A bundle built without the base href
is refused rather than published, since the two are indistinguishable on disk
apart from one attribute in `index.html`.
"""

import argparse
import filecmp
import functools
import http.server
import logging
import pathlib
import re
import shutil
import socketserver
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

REPO = pathlib.Path(__file__).resolve().parent.parent

# Where the console lives on the public site. These three have to agree: the
# base href is compiled into the bundle by `fledge run web-build-hosted`, the
# site path is where the site repository serves it from, and the URL is what
# the docs tell people to visit. Changing the address means changing all three
# and every document that names it.
SITE_PATH = "arcron/console"
BASE_HREF = f"/{SITE_PATH}/"
CONSOLE_URL = f"https://corvidlabs.xyz{BASE_HREF}"

# What `fledge run web-build-hosted` writes. Angular's application builder puts
# the browser bundle in a `browser/` subdirectory of the output path; only that
# subdirectory is served, and the licence manifest beside it is not.
BUNDLE = REPO / "web" / "dist" / "hosted" / "browser"

# Attributes in index.html that name a file the browser will fetch.
_ASSET_ATTRIBUTES = re.compile(r"""(?:href|src)\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
# A url() inside a stylesheet we fetched, which is the other way an asset gets
# referenced without appearing in index.html at all.
_CSS_URL = re.compile(r"""url\(\s*["']?([^"')]+)["']?\s*\)""", re.IGNORECASE)


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    """A static file server that does not narrate every request."""

    def log_message(self, format: str, *args: object) -> None:
        return


def _relative_files(root: pathlib.Path) -> list[str]:
    return sorted(str(path.relative_to(root)) for path in root.rglob("*") if path.is_file())


def _fetch(base_url: str, path: str) -> tuple[int, bytes]:
    """GET one path from the throwaway server, returning its status and body."""
    url = urllib.parse.urljoin(base_url, path)
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, b""


def _referenced(document: bytes, from_path: str, pattern: re.Pattern) -> list[str]:
    """Every same-origin asset a document asks for, as absolute server paths.

    Relative references resolve against the document, exactly as a browser
    resolves them, which for index.html means against its `<base href>`.
    """
    found = []
    for raw in pattern.findall(document.decode("utf-8", "replace")):
        target = raw.strip()
        if not target or target.startswith(("#", "data:", "mailto:")):
            continue
        if urllib.parse.urlparse(target).scheme or target.startswith("//"):
            continue  # A fully qualified URL: somebody else's server, not ours.
        found.append(urllib.parse.urljoin(from_path, target))
    return found


def _check_no_root_paths(index: str) -> list[str]:
    """Root-absolute references in index.html, which a subpath cannot serve.

    The base href is the one legitimate `/...` in the document. Anything else
    escapes the subpath and would only work if the console owned the domain.
    """
    offenders = []
    for raw in _ASSET_ATTRIBUTES.findall(index):
        if raw.startswith("/") and raw != BASE_HREF:
            offenders.append(raw)
    return offenders


def verify(bundle: pathlib.Path) -> int:
    """Serve the bundle at its hosted subpath and fetch everything in it."""
    if not bundle.is_dir():
        logger.error(f"{bundle.relative_to(REPO)} does not exist.")
        logger.error("Build it first: fledge run web-build-hosted")
        return 1

    index_file = bundle / "index.html"
    if not index_file.exists():
        logger.error(f"{bundle.relative_to(REPO)} has no index.html, so it is not a browser bundle.")
        return 1

    index_source = index_file.read_text()
    declared = re.search(r"""<base\s+href\s*=\s*["']([^"']+)["']""", index_source)
    if declared is None or declared.group(1) != BASE_HREF:
        found = "no <base> tag at all" if declared is None else f'"{declared.group(1)}"'
        logger.error(f'index.html declares {found}, and hosting needs "{BASE_HREF}".')
        logger.error("This is a bundle built for the domain root. Rebuild: fledge run web-build-hosted")
        return 1

    offenders = _check_no_root_paths(index_source)
    if offenders:
        logger.error("index.html references files at the domain root, which will 404 under a subpath:")
        for offender in offenders:
            logger.error(f"  {offender}")
        return 1

    # A throwaway tree that puts the bundle exactly where the site will: the
    # server root stands in for corvidlabs.xyz, and nothing else lives in it,
    # so any reference that reaches outside the subpath fails the way it would
    # in production instead of quietly resolving.
    staging = REPO / "web" / "dist" / ".hosted-check"
    shutil.rmtree(staging, ignore_errors=True)
    served = staging / SITE_PATH
    served.parent.mkdir(parents=True)
    served.symlink_to(bundle)

    handler = functools.partial(_QuietHandler, directory=str(staging))
    with socketserver.TCPServer(("127.0.0.1", 0), handler) as server:
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            failures = _crawl(base_url, bundle)
        finally:
            server.shutdown()
            thread.join(timeout=5)
    shutil.rmtree(staging, ignore_errors=True)

    if failures:
        logger.error(f"The hosted bundle is broken at {BASE_HREF}:")
        for failure in failures:
            logger.error(f"  {failure}")
        return 1

    logger.info(f"Served {len(_relative_files(bundle))} files at {BASE_HREF} and every one of them loaded.")
    logger.info(f"Ready to publish at {CONSOLE_URL}")
    return 0


def _crawl(base_url: str, bundle: pathlib.Path) -> list[str]:
    """Fetch the entry page, everything it references, and everything shipped."""
    failures = []

    status, index = _fetch(base_url, BASE_HREF)
    if status != 200:
        return [f"GET {BASE_HREF} returned {status}"]

    # Everything index.html asks for, then everything those stylesheets ask
    # for. Two levels is enough: nothing here references a third.
    queue = _referenced(index, BASE_HREF, _ASSET_ATTRIBUTES)
    seen = set()
    while queue:
        path = queue.pop(0)
        if path in seen:
            continue
        seen.add(path)
        if not path.startswith(BASE_HREF):
            failures.append(f"{path} is referenced but sits outside {BASE_HREF}")
            continue
        status, body = _fetch(base_url, path)
        if status != 200:
            failures.append(f"GET {path} returned {status}")
            continue
        if not body:
            failures.append(f"GET {path} returned an empty body")
            continue
        if path.endswith(".css"):
            queue.extend(_referenced(body, path, _CSS_URL))

    # And every file the build actually shipped, whether or not index.html
    # names it. `web/src/app/components/network-bar.ts` injects brand/theme.js
    # from a string at runtime, so a crawl of the markup alone would miss the
    # design system's own script and call the bundle healthy.
    for relative in _relative_files(bundle):
        path = BASE_HREF + relative
        status, body = _fetch(base_url, path)
        if status != 200:
            failures.append(f"GET {path} returned {status} (it is in the bundle)")
        elif len(body) != (bundle / relative).stat().st_size:
            failures.append(f"GET {path} returned {len(body)} bytes, not the {(bundle / relative).stat().st_size} on disk")

    return failures


def _report_spa_fallback() -> None:
    """State the one thing serving the bundle cannot prove on its own."""
    logger.info("")
    logger.info("Note: the console has no client-side routes yet, so a plain static")
    logger.info("server is enough. The moment routing lands, nginx needs a fallback or")
    logger.info("every deep link 404s on reload. In CorvidLabs/site deploy/vps/nginx.conf:")
    logger.info("")
    logger.info(f"    location ^~ {BASE_HREF} {{")
    logger.info(f"        try_files $uri $uri/index.html {BASE_HREF}index.html;")
    logger.info("    }")


def _publish(bundle: pathlib.Path, site: pathlib.Path, check: bool) -> int:
    """Mirror the verified bundle into a checkout of CorvidLabs/site."""
    if not (site / "astro.config.mjs").exists():
        logger.error(f"{site} does not look like the site repository (no astro.config.mjs).")
        return 1

    # public/ is copied verbatim into the Astro build, and the deploy workflow
    # rsyncs that build into the web root with --delete. So a file here is a
    # file on corvidlabs.xyz, and a file removed here is removed there.
    target = site / "public" / SITE_PATH
    shipped = _relative_files(bundle)

    if check:
        existing = _relative_files(target) if target.is_dir() else []
        drifted = [f"missing: {name}" for name in shipped if name not in existing]
        drifted += [f"stale: {name}" for name in existing if name not in shipped]
        drifted += [
            f"differs: {name}"
            for name in shipped
            if name in existing and not filecmp.cmp(bundle / name, target / name, shallow=False)
        ]
        if drifted:
            logger.error(f"{target.relative_to(site)} is out of date:")
            for line in drifted[:20]:
                logger.error(f"  {line}")
            if len(drifted) > 20:
                logger.error(f"  ... and {len(drifted) - 20} more")
            logger.error("Run scripts/publish_console.py without --check.")
            return 1
        logger.info(f"All {len(shipped)} files match {target.relative_to(site)}.")
        return 0

    # Replace rather than overlay. Angular fingerprints its chunk names, so an
    # overlay leaves every previous build's chunks behind for ever, and they
    # would be deployed alongside the current one.
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(bundle, target)
    logger.info(f"  {bundle.relative_to(REPO)} -> {target.relative_to(site)} ({len(shipped)} files)")
    _report_spa_fallback()
    logger.info("")
    logger.info("Nothing is published yet. Review the staged bundle, then in the site checkout:")
    logger.info(f"    git -C {site} add public/{SITE_PATH}")
    logger.info(f"    git -C {site} commit -m 'Add: the Arcron console at /arcron/console/'")
    logger.info(f"    git -C {site} push")
    logger.info("The site's deploy workflow builds and ships main, so the push is what publishes.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--site", type=pathlib.Path, help="path to the CorvidLabs/site checkout")
    parser.add_argument("--check", action="store_true", help="report drift without writing")
    parser.add_argument("--verify", action="store_true", help="only prove the bundle works at its subpath")
    args = parser.parse_args(argv)

    if args.site is None and not args.verify:
        parser.error("give --site to publish, or --verify to only check the bundle")

    failed = verify(BUNDLE)
    if failed:
        return failed
    if args.verify:
        _report_spa_fallback()
        return 0
    return _publish(BUNDLE, args.site, args.check)


if __name__ == "__main__":
    sys.exit(main())
