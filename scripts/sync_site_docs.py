"""Publish the integrator documentation to the public site.

The repository is private, so the site is the only documentation anybody
outside can read. Writing it twice would mean maintaining it twice, and this
project has already spent a day repairing prose that drifted from the code.
So there is one source, here, and this copies it.

    poetry run python -m scripts.sync_site_docs --site ../site
    poetry run python -m scripts.sync_site_docs --site ../site --check

`--check` reports drift without writing, which is what CI would run if the
two repositories ever share one.

Only the documents an integrator needs are published. Design notes, specs and
internal task lists stay here: they are about how decisions were reached, and
publishing them would ask a reader to care about arguments that are settled.
"""

import argparse
import logging
import pathlib
import re
import sys

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

REPO = pathlib.Path(__file__).resolve().parent.parent

# source, slug, title, one-line description, section
PAGES = [
    ("docs/integrating.md", "integrating", "Integrating",
     "Hooking your contract into Arcron: the hook shape, authorization, and the failure modes that stop an upkeep being serviced.",
     "Building on it"),
    ("docs/arcron.md", "how-it-works", "How it works",
     "What the keeper network does, what an execution costs, and what an Arcron-triggered call can reach.",
     "Building on it"),
    ("docs/hosting.md", "running-a-keeper", "Running a keeper",
     "Where to run a keeper, what each option costs, and what the account needs.",
     "Running it"),
    ("docs/deploying.md", "deploying", "Deploying",
     "From a checkout to a running deployment on any network, plus updating, freezing and multisig control.",
     "Running it"),
    ("docs/releases.md", "release-stages", "Release stages",
     "alpha, beta, rc and MainNet, and the gate that ends each one.",
     "Reference"),
    ("docs/security.md", "security", "Security",
     "The threat model, the accepted risks, and how to check a deployment yourself.",
     "Reference"),
]

# Links between published documents rewrite to site paths. Anything pointing at
# a file that is not published becomes plain text, because a link into a
# private repository is worse than no link.
PUBLISHED = {source.split("/")[-1]: slug for source, slug, *_ in PAGES}


def _rewrite_links(body: str, base: str) -> str:
    def replace(match: re.Match) -> str:
        label, target = match.group(1), match.group(2)
        anchor = ""
        if "#" in target:
            target, anchor = target.split("#", 1)
            anchor = "#" + anchor
        # A link to an anchor on this same page, which every table of contents
        # is made of. Splitting the anchor off leaves an empty target, and an
        # earlier version of this then treated it as an unpublished file and
        # stripped the link, turning every contents list into dead text.
        if not target:
            return f"[{label}]({anchor})"
        name = target.split("/")[-1]
        if name in PUBLISHED:
            return f"[{label}]({base}{PUBLISHED[name]}/{anchor})"
        if target.startswith("http"):
            return match.group(0)
        # A path into the private repository. Keep the words, drop the link.
        return label

    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", replace, body)


def _page(source: pathlib.Path, slug: str, title: str, description: str) -> str:
    body = source.read_text()
    # The site renders the title from frontmatter, so drop the leading H1.
    body = re.sub(r"\A#\s+[^\n]*\n+", "", body)
    body = _rewrite_links(body, "/arcron/docs/")
    front = (
        "---\n"
        "layout: ../../../layouts/arcron-docs.astro\n"
        f"title: {title}\n"
        f"slug: {slug}\n"
        f"description: >-\n  {description}\n"
        "---\n\n"
        f"<!-- Generated from {source.relative_to(REPO)} by scripts/sync_site_docs.py.\n"
        "     Edit that file, not this one. -->\n\n"
    )
    return front + body


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--site", type=pathlib.Path, required=True, help="path to the site checkout")
    parser.add_argument("--check", action="store_true", help="report drift without writing")
    args = parser.parse_args(argv)

    out_dir = args.site / "src" / "pages" / "arcron" / "docs"
    if not args.check:
        out_dir.mkdir(parents=True, exist_ok=True)

    drifted = []
    for source_name, slug, title, description, _section in PAGES:
        source = REPO / source_name
        if not source.exists():
            logger.error(f"{source_name} does not exist")
            return 1
        rendered = _page(source, slug, title, description)
        target = out_dir / f"{slug}.md"
        if args.check:
            if not target.exists() or target.read_text() != rendered:
                drifted.append(f"{target.relative_to(args.site)} differs from {source_name}")
        else:
            target.write_text(rendered)
            logger.info(f"  {source_name} -> {target.relative_to(args.site)}")

    if args.check:
        if drifted:
            logger.error("The published docs are out of date:")
            for line in drifted:
                logger.error(f"  {line}")
            logger.error("Run scripts/sync_site_docs.py without --check.")
            return 1
        logger.info(f"All {len(PAGES)} published documents match their source.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
