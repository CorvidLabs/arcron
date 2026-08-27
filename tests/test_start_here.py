"""START-HERE.md is the front door, so its links have to actually open.

This file is the one a stranger, an outside agent, or a teammate is pointed at
first. Every other document in this repository can afford a stale link for a
week. This one cannot: a dead link here is the first thing somebody sees, and
the cost is not a broken build, it is a person deciding the project is
abandoned and closing the tab.

It also pins the two claims on that page that are load-bearing and easy to let
drift: the app id it names, and the fact that it does not oversell the state.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
START_HERE = ROOT / "START-HERE.md"

#: The deployment START-HERE sends people to. Same id tests/test_app_id_consistency.py guards.
CURRENT_APP_ID = "769891898"

#: Links that leave the repository; this test is about paths, not the network.
EXTERNAL = re.compile(r"^(https?:|mailto:|#)")


def _repo_links(text: str) -> list[str]:
    """Every markdown link target that should resolve to a file in the tree."""
    targets = re.findall(r"\[[^\]]+\]\(([^)]+)\)", text)
    return [t.split("#", 1)[0] for t in targets if not EXTERNAL.match(t) and t.strip()]


def test_every_link_resolves_to_something_that_exists() -> None:
    missing = [link for link in _repo_links(START_HERE.read_text()) if not (ROOT / link).exists()]
    assert not missing, f"START-HERE.md points at paths that do not exist: {missing}"


def test_it_names_the_current_deployment() -> None:
    # A front door naming a superseded app sends every newcomer to an empty
    # registry, which reads as "this is dead" rather than "this link is stale".
    text = START_HERE.read_text()
    assert CURRENT_APP_ID in text, f"START-HERE.md does not name app {CURRENT_APP_ID}"


def test_it_still_says_what_is_unproven() -> None:
    # The page's value is that it does not oversell. These are the admissions
    # most likely to be quietly dropped once there is an audience, and dropping
    # them would make this the marketing page it was written not to be.
    text = START_HERE.read_text().lower()
    for admission in ("unaudited", "upgradeable", "alpha"):
        assert admission in text, f"START-HERE.md no longer says it is {admission}"


def test_it_tells_agents_not_to_trust_its_own_numbers() -> None:
    # Added because several figures on the page it replaced were wrong, and were
    # caught by a review that recomputed them instead of quoting them.
    text = START_HERE.read_text().lower()
    assert "recompute" in text or "do not trust" in text
