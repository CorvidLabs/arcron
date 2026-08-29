"""The keeper dashboard must never become publishable.

It is a different product from the console. The console is for somebody
registering an upkeep: it connects a wallet, it is served from one canonical
address, and that address is a security property, because the contract is
permissionless and the address is the only thing separating our front end from
a copy.

The dashboard is for somebody running a keeper. It connects no wallet, it holds
no key, and it is meant to run on the operator's own machine. Publishing it
would put a second Arcron-branded page on the internet whose whole purpose is
to be pointed at arbitrary app ids, which is precisely the look-alike problem
`web/src/app/core/quarantine.ts` exists to mitigate.

Nothing enforces that except these tests, because "we did not mean to publish
it" is not a control.
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
KEEPER_UI = ROOT / "web-keeper"
GOVERN_UI = ROOT / "web-govern"

#: Every app that must never reach the public site.
LOCAL_ONLY = ("web-keeper", "web-govern")


def test_the_keeper_ui_exists_to_be_checked() -> None:
    # A guard over a directory that has been renamed passes for ever.
    assert (KEEPER_UI / "angular.json").is_file()
    assert (KEEPER_UI / "src" / "main.ts").is_file()


def test_no_publish_step_names_the_keeper_ui() -> None:
    """The console has a publish path. This must not acquire one.

    `scripts/publish_console.py` stages a build into a checkout of the site
    repository. If it ever learns the word `web-keeper`, the separation has
    been lost by editing rather than by decision.
    """
    publish = (ROOT / "scripts" / "publish_console.py").read_text()
    sync = (ROOT / "scripts" / "sync_site_docs.py").read_text()
    for name in LOCAL_ONLY:
        assert name not in publish, f"{name} is named in the publish script"
        assert name not in sync, f"{name} is named in the docs sync"


def test_the_keeper_ui_has_no_base_href_for_the_site() -> None:
    # A base href is what makes a bundle servable from a subdirectory of the
    # public site. The console's build carries one on purpose; this must not.
    fledge = (ROOT / "fledge.toml").read_text()
    for line in fledge.splitlines():
        for name in LOCAL_ONLY:
            if name in line:
                assert "--base-href" not in line, line
                assert "/arcron/" not in line, line


def test_the_keeper_ui_asks_not_to_be_indexed() -> None:
    # Belt and braces: if it is ever served by accident, it should not be
    # findable.
    index = (KEEPER_UI / "src" / "index.html").read_text()
    assert re.search(r'name="robots"[^>]*noindex', index)


def test_the_keeper_ui_carries_no_wallet_dependency() -> None:
    """It holds no key, so it must not be able to hold one.

    The bot already has the key. A dashboard that could connect a wallet would
    be inviting an operator to expose one to a page that has no use for it.
    """
    package = (KEEPER_UI / "package.json").read_text()
    assert "use-wallet" not in package


def test_the_keeper_ui_is_in_the_workspace_so_ci_sees_it() -> None:
    # An app nothing builds is an app that rots.
    root_package = (ROOT / "package.json").read_text()
    assert "web-keeper" in root_package
    fledge = (ROOT / "fledge.toml").read_text()
    assert "keeper-ui-test" in fledge and "keeper-ui-build" in fledge


def test_the_governance_app_is_local_only_too() -> None:
    """The one page that can reach MainNet, so the one that must not be served.

    The console's address is a security property: the contract is
    permissionless, so that address is the only thing separating our front end
    from a copy. A page whose purpose is authorizing permanent changes to a live
    contract raises the stakes of a convincing clone from "somebody loses their
    own escrow" to "the programs are replaced for everyone". It runs locally.
    """
    assert (GOVERN_UI / "angular.json").is_file()
    index = (GOVERN_UI / "src" / "index.html").read_text()
    assert re.search(r'name="robots"[^>]*noindex', index)


def test_the_governance_app_says_so_on_the_page() -> None:
    # Not only in a test and a comment. Whoever opens it should be able to tell
    # they are somewhere that is not the published console.
    page = (GOVERN_UI / "src" / "app" / "govern-page.ts").read_text()
    assert "Local only" in page
    assert "corvidlabs.xyz" in page


def test_the_governance_app_offers_no_update() -> None:
    """A browser cannot compile Algorand Python.

    An update whose payload the page cannot verify would be worse than no
    update, so that stays where `verify_build` rebuilds from source.
    """
    page = (GOVERN_UI / "src" / "app" / "govern-page.ts").read_text()
    assert "update()void" not in page
    assert "freeze()void" in page
