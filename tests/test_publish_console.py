"""The console's address has to mean the same thing in every place it is set.

Four files decide where the console lives: `fledge.toml` compiles a base href
into the bundle, `scripts/publish_console.py` decides where in the site
repository it is written, and the documents tell people where to go. If those
drift, nothing fails: the build succeeds, the publish succeeds, and the page
is served at an address its own markup does not believe in.

That matters more here than in most places, because `docs/console-plan.md`
makes the address a security property. The contract is permissionless, anyone
can host a front end for it, and the defence against a hostile one is telling
people the canonical URL. A URL that is wrong in the docs is not a typo, it is
the defence pointing at nothing.

The other half of this file tests the checker itself. `web-build-hosted` sat in
`fledge.toml` for weeks without ever being run, and a check nobody runs and a
check that always passes fail identically.
"""

import re
import tomllib
from pathlib import Path

from scripts.publish_console import (
    BASE_HREF,
    CONSOLE_URL,
    SITE_PATH,
    _ASSET_ATTRIBUTES,
    _check_no_root_paths,
    _referenced,
)

ROOT = Path(__file__).resolve().parent.parent

# Every document that tells a human where the console is. A new one naming a
# different address is the failure this catches.
DOCUMENTS = [
    "README.md",
    "AGENTS.md",
    "CLAUDE.md",
    "web/README.md",
    "docs/status.md",
    "docs/releases.md",
    "docs/console-plan.md",
]


def _tasks() -> dict[str, str]:
    with (ROOT / "fledge.toml").open("rb") as handle:
        return tomllib.load(handle)["tasks"]


def test_the_build_compiles_in_the_base_href_the_publisher_expects():
    build = _tasks()["web-build-hosted"]
    assert f"--base-href {BASE_HREF}" in build, (
        f"fledge.toml builds a different base href from the {BASE_HREF} "
        "scripts/publish_console.py publishes to"
    )


def test_the_publish_path_and_the_url_agree():
    assert CONSOLE_URL.endswith(f"/{SITE_PATH}/")
    assert BASE_HREF == f"/{SITE_PATH}/"


def test_the_hosted_build_is_in_the_ci_lane_and_so_is_the_check():
    # The whole reason the check exists is that a hosted build which 404s its
    # own JavaScript compiles cleanly. It is only worth anything in a lane.
    with (ROOT / "fledge.toml").open("rb") as handle:
        lane = tomllib.load(handle)["lanes"]["ci"]["steps"]
    assert "web-build-hosted" in lane
    assert "web-verify-hosted" in lane
    assert lane.index("web-build-hosted") < lane.index("web-verify-hosted")


def test_every_document_naming_the_console_names_the_same_address():
    pattern = re.compile(r"https://corvidlabs\.xyz/arcron/[A-Za-z0-9_./-]*")
    for name in DOCUMENTS:
        text = (ROOT / name).read_text()
        for found in pattern.findall(text):
            # Trailing punctuation from prose is not part of the address.
            found = found.rstrip(".,)")
            if "/console" not in found:
                continue  # /arcron/docs/... is the documentation sync's business.
            assert found == CONSOLE_URL.rstrip("/") or found == CONSOLE_URL, (
                f"{name} names {found}, and the console is at {CONSOLE_URL}"
            )


def test_a_root_absolute_reference_is_refused():
    # The bug the base href exists to prevent, written out: a script tag
    # pinned to the domain root, which resolves nowhere under a subpath.
    index = f'<base href="{BASE_HREF}"><script src="/main-abc.js"></script>'
    assert _check_no_root_paths(index) == ["/main-abc.js"]


def test_the_base_href_itself_is_not_treated_as_an_offender():
    assert _check_no_root_paths(f'<base href="{BASE_HREF}">') == []


def test_relative_references_resolve_under_the_subpath():
    document = b'<link href="brand/tokens.css"><script src="main-abc.js"></script>'
    found = _referenced(document, BASE_HREF, _ASSET_ATTRIBUTES)
    assert found == [f"{BASE_HREF}brand/tokens.css", f"{BASE_HREF}main-abc.js"]


def test_somebody_elses_server_is_not_ours_to_check():
    # The console loads its fonts from Google. Fetching those from a throwaway
    # local server would fail for a reason that says nothing about the bundle.
    document = b'<link href="https://fonts.googleapis.com/css2?family=X">'
    assert _referenced(document, BASE_HREF, _ASSET_ATTRIBUTES) == []
