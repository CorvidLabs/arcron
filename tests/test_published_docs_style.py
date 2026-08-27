"""The documents this repository publishes have to pass the site's own audit.

`CorvidLabs/site` renders seven of these files and runs `audit-site.mjs` over
the result, which rejects an em dash. That audit is the site's gate, and it fired
on a sentence written here: the fix landed, the docs synced, and the site build
failed on prose this repository had already accepted.

The rule belongs at the source. A style gate that only runs downstream turns a
one-character edit into a cross-repository round trip, and the person who wrote
the sentence is no longer looking at it by the time it fails.

Only the published set is checked. A design note or a review record is not
rendered by the site and is not held to the site's typography.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: The documents scripts/sync_site_docs.py renders, and only those.
PUBLISHED = [
    "docs/status.md",
    "docs/integrating.md",
    "docs/arcron.md",
    "docs/hosting.md",
    "docs/deploying.md",
    "docs/releases.md",
    "docs/security.md",
]

#: What the site's audit rejects.
FORBIDDEN = {"—": "em dash"}


def test_no_published_document_contains_a_character_the_site_rejects() -> None:
    found: list[str] = []
    for name in PUBLISHED:
        document = ROOT / name
        if not document.exists():
            continue
        for line_number, line in enumerate(document.read_text().splitlines(), start=1):
            for character, description in FORBIDDEN.items():
                if character in line:
                    found.append(f"{name}:{line_number} {description}: {line.strip()[:70]}")
    assert not found, "the site's audit rejects these:\n  " + "\n  ".join(found)


def test_the_published_list_matches_what_the_sync_actually_renders() -> None:
    # A list that drifts from the sync checks the wrong files and passes anyway.
    sync = (ROOT / "scripts/sync_site_docs.py").read_text()
    for name in PUBLISHED:
        assert name.removeprefix("docs/") in sync, f"{name} is not rendered by sync_site_docs.py"
