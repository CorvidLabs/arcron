"""`docs/book/` restates the docs, so something has to notice when it stops.

The Working Guide is a fourth copy of every figure in this repository: the
contract holds one, `docs/` holds another, the console holds a third, and a
16,000-word book holds the fourth. Drift between copies is the single most
recurring defect in this project's history — `docs/why.md` alone has had three
rounds of corrections to its cost argument, and `docs/first-upkeep.md` has got
its own arithmetic wrong twice.

The book ran that risk immediately. It was drafted on 2026-08-24; by 2026-08-27,
before it had even merged, seven of its load-bearing figures were superseded by
corrections landing on `main` — a cost multiple, the creator crossover, the
register-cost total, the fee the console suggests, and a claim about ALGO's
trading history that was false. A reviewer caught those. A reviewer will not be
there next time.

So this file makes the duplication *checked* rather than *hoped for*. Every
figure below is extracted from the file that owns it and then required to appear
in the guide. Change the owner and this test fails, naming the figure and the
file it came from.

Two rules for anyone maintaining this:

* **The owning file wins.** If this test fails, the guide is wrong. Fix the
  guide, do not loosen the pattern.
* **A pattern that stops matching its owner is also a failure**, deliberately.
  It means the sentence the figure lived in was rewritten, and somebody should
  look at whether the book still says the same thing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BOOK_DIR = ROOT / "docs" / "book"
GUIDE = BOOK_DIR / "arcron-working-guide.md"

#: Links that leave the repository; this test is about paths, not the network.
EXTERNAL = re.compile(r"^(https?:|mailto:|#)")


def guide_text() -> str:
    return GUIDE.read_text()


def _flowed(text: str) -> str:
    """Every run of whitespace as one space.

    Both the guide and `docs/` are hard-wrapped at about 80 columns, so a
    sentence carrying a figure breaks across lines wherever the previous edit
    left it, and reflows on the next one. Patterns that depend on where the line
    broke fail for reasons that have nothing to do with the number being wrong,
    which is the fastest way to get a test like this deleted.
    """
    return re.sub(r"\s+", " ", text)


def _owned_figure(relative_path: str, pattern: str, group: int = 1) -> str:
    """Pull one figure out of the file that owns it."""
    text = _flowed((ROOT / relative_path).read_text())
    found = re.search(pattern, text)
    assert found, (
        f"{relative_path}: pattern {pattern!r} matched nothing. The sentence that "
        f"held this figure was rewritten — check whether docs/book/ still agrees "
        f"with it, then update this pattern."
    )
    return found.group(group)


def _thousands(raw: str) -> str:
    """`4_000` in Python or `4000` in TypeScript, as the guide writes it: `4,000`."""
    return f"{int(raw.replace('_', '').replace(',', '')):,}"


def _guide_figure(pattern: str, label: str) -> str:
    """Pull the same figure back out of the guide, from a named place.

    Anchored patterns, not substring searches. A 16,000-word document contains
    "758" and "10" somewhere no matter what it says, so `"758" in text` is not a
    check — it is a check-shaped thing that always passes. Every assertion here
    reads the figure out of the row or sentence that is supposed to carry it.
    """
    found = re.search(pattern, _flowed(guide_text()))
    assert found, (
        f"docs/book/ no longer states the {label} where this test looks for it "
        f"({pattern!r}). Either the figure was dropped or its sentence was "
        f"rewritten; check the guide still agrees with the doc that owns it."
    )
    return found.group(1)


# --- links ----------------------------------------------------------------


def test_every_link_in_the_guide_resolves() -> None:
    """A book that cites `docs/` and cannot open it is worse than no citation.

    The guide's whole safety story is "read the source, it is canonical". A
    broken relative link breaks that promise silently, and the guide is nested
    two directories deep, so every one of its links is a `../` that is easy to
    get wrong.
    """
    targets = re.findall(r"\[[^\]]+\]\(([^)]+)\)", guide_text())
    missing = [
        target
        for target in targets
        if not EXTERNAL.match(target)
        and target.strip()
        and not (BOOK_DIR / target.split("#", 1)[0]).resolve().exists()
    ]
    assert not missing, f"docs/book/ points at paths that do not exist: {missing}"


def test_the_book_directory_stays_text() -> None:
    """No committed PDF or EPUB.

    The tree is otherwise entirely text. A rendered book is a build product of
    a Markdown file that is itself derived from `docs/`, so committing one adds
    a copy of every figure that nothing can date and nothing can check. Build it
    with `./build.sh`, or attach it to a release.
    """
    committed = sorted(
        path.name
        for path in BOOK_DIR.iterdir()
        if path.is_file() and path.suffix.lower() in {".pdf", ".epub", ".mobi", ".docx"}
    )
    assert not committed, (
        f"docs/book/ has committed build products: {committed}. They are gitignored; "
        "build them with ./build.sh instead."
    )


# --- the deployment it points at ------------------------------------------


def test_it_names_the_current_deployment() -> None:
    """Same guarantee `tests/test_start_here.py` gives the front door.

    A guide naming a superseded app walks a newcomer into an empty registry,
    which reads as "this project is dead" rather than "this link is stale".
    """
    from tests.test_app_id_consistency import latest_release_app_id

    expected = latest_release_app_id()
    assert expected in guide_text(), f"the guide does not name app {expected}"


# --- contract constants ---------------------------------------------------

CONTRACT = "smart_contracts/keeper/contract.py"

#: (label, owning file, pattern in the owner, pattern in Appendix G's table).
#: Appendix G is the guide's one table of constants, so it is where they are
#: pinned; the prose elsewhere quotes the same numbers back.
CONSTANTS = [
    ("minimum fee", CONTRACT, r"MIN_UPKEEP_FEE = ([\d_]+)", r"\| Minimum fee \| ([\d,]+) µALGO"),
    (
        "maximum fee",
        CONTRACT,
        r"MAX_UPKEEP_FEE = ([\d_]+)",
        r"\| Maximum fee / cap \| ([\d,]+) µALGO",
    ),
    ("minimum interval", CONTRACT, r"MIN_INTERVAL_ROUNDS = ([\d_]+)", r"\| Interval \| ([\d,]+) –"),
    (
        "maximum interval",
        CONTRACT,
        r"MAX_INTERVAL_ROUNDS = ([\d_]+)",
        r"\| Interval \| [\d,]+ – ([\d,]+) rounds",
    ),
    ("max app args", CONTRACT, r"MAX_CALL_ARGS = ([\d_]+)", r"\| Max app args \| ([\d,]+) "),
    ("max call data", CONTRACT, r"MAX_CALL_DATA = ([\d_]+)", r"\| Max call data \| ([\d,]+) bytes"),
    (
        "asset opt-in MBR",
        CONTRACT,
        r"ASSET_OPT_IN_MBR = ([\d_]+)",
        r"\| ASA opt-in deposit \| ([\d,]+) µALGO",
    ),
    # The console's suggested fee is new. The guide quoted the 4,000 floor as if
    # it were what anyone should register at for as long as it did not exist.
    (
        "suggested fee",
        "js/src/upkeep.ts",
        r"SUGGESTED_UPKEEP_FEE = ([\d_]+)",
        r"\| Console-suggested fee \| ([\d,]+) µALGO",
    ),
]


@pytest.mark.parametrize(
    ("label", "relative_path", "owner_pattern", "guide_pattern"), CONSTANTS
)
def test_guide_quotes_the_real_constant(
    label: str, relative_path: str, owner_pattern: str, guide_pattern: str
) -> None:
    expected = _thousands(_owned_figure(relative_path, owner_pattern))
    actual = _thousands(_guide_figure(guide_pattern, label))
    assert actual == expected, (
        f"docs/book/ gives the {label} as {actual}; {relative_path} defines it as "
        f"{expected}. The source wins: fix the guide."
    )


def test_guide_quotes_the_real_abi_signatures() -> None:
    """A selector is `sha512_256(signature)[:4]`, so a signature is not prose.

    Appendix A prints all eight. One wrong character there produces
    `logic eval error: err opcode executed` with no mention of the method, which
    is among the least debuggable failures in the system.
    """
    spec = json.loads((ROOT / "smart_contracts/artifacts/keeper/Keeper.arc56.json").read_text())
    text = guide_text()
    missing = []
    for method in spec["methods"]:
        args = ",".join(argument["type"] for argument in method["args"])
        signature = f"{method['name']}({args}){method['returns']['type']}"
        if signature not in text:
            missing.append(signature)
    assert not missing, f"Appendix A is missing or misquotes ABI signatures: {missing}"


# --- the economics --------------------------------------------------------

WHY = "docs/why.md"
FIRST_UPKEEP = "docs/first-upkeep.md"
ARCRON = "docs/arcron.md"

#: The figures that have actually gone wrong, each pinned to the file that owns
#: it *and* to the place in the guide that must carry it. Every one of these was
#: either corrected on main after the book was drafted, or is a direct
#: neighbour of one that was.
#:
#: Where a figure appears in the guide's prose as well as in Appendix G, both
#: are listed: Appendix G going stale is bad, but Chapter 10's argument going
#: stale while Appendix G stays right is worse, because that is the copy a
#: reader is actually persuaded by.
ECONOMICS = [
    # The single basis. Two earlier drafts of docs/why.md mixed two bases and
    # produced a multiple that did not reproduce from their own tables, so the
    # basis is now the first thing the guide states and the first thing pinned.
    (
        "round time",
        WHY,
        r"basis: ([\d.]+) s/round measured",
        [r"\| Round time \(TestNet measured\) \| ([\d.]+) s \|", r"\*\*([\d.]+) s/round measured"],
    ),
    (
        "nominal-hour rounds",
        WHY,
        r"upkeep of ([\d,]+) rounds fires",
        [r"\| Nominal-hour upkeep \| ([\d,]+) rounds"],
    ),
    (
        "real cadence",
        WHY,
        r"fires every ([\d.]+) minutes",
        [r"\| Nominal-hour upkeep \| [\d,]+ rounds = ([\d.]+) minutes"],
    ),
    (
        "executions per month",
        WHY,
        r"\*\*(\d+) times a month\*\*",
        [
            r"\| Executions per month \| \*\*(\d+)\*\*",
            r"\*\*(\d+) times a month\*\*",
            r"\*\*(\d+) executions, not 720\*\*",
            r"it fires \*\*(\d+) times, not 720\*\*",
        ],
    ),
    (
        "ALGO price",
        WHY,
        r"at ALGO \$(\d+\.\d+)",
        [r"\| ALGO price used \| \*\*\$(\d+\.\d+)\*\*", r"priced at \*\*ALGO \$(\d+\.\d+)\*\*"],
    ),
    # The multiple. An earlier draft printed one number for both fees and so
    # overstated the suggested fee by two and a half times.
    (
        "floor multiple",
        WHY,
        r"About ([\d.]+)x cheaper than the cheapest paid host",
        [
            r"paid host by \| \*\*([\d.]+)x\*\* at the floor",
            r"\*\*([\d.]+)x cheaper than the cheapest \*paid\* host at the floor fee",
            r"at the 4,000 µALGO floor\*\* is \*\*([\d.]+)x\*\* cheaper",
        ],
    ),
    (
        "suggested-fee multiple",
        WHY,
        r"at the floor, and \*\*([\d.]+)x\*\*",
        [
            r"at the floor, \*\*([\d.]+)x\*\* suggested",
            r"and ([\d.]+)x cheaper at the fee the console actually suggests",
            r"gap narrows to \*\*([\d.]+)x\*\*",
            r"still ([\d.]+)x cheaper than the cheapest paid host",
        ],
    ),
    # Monthly cost at each fee.
    (
        "floor monthly cost",
        WHY,
        r"at the 4,000 µALGO floor \| ~([\d.]+) ALGO",
        [
            r"\| Monthly cost, hourly upkeep at the floor \| ([\d.]+) ALGO",
            r"\*\*Arcron at the 4,000 µALGO floor\*\* \| ~([\d.]+) ALGO",
        ],
    ),
    (
        "suggested monthly cost",
        WHY,
        r"at the suggested 10,000 \| ~([\d.]+) ALGO",
        [
            r"\| Monthly cost, hourly upkeep at the suggested fee \| ([\d.]+) ALGO",
            r"\*\*Arcron at the suggested 10,000 µALGO\*\* \| ~([\d.]+) ALGO",
        ],
    ),
    # The crossover. An earlier draft quoted 26, the figure for a $5 host, on a
    # page whose own table quotes $2.02.
    (
        "creator crossover",
        WHY,
        r"Above about (\d+) hourly upkeeps",
        [
            r"\| Creator crossover to self-hosting \(vs fly\.io \$2\.02\) \| (\d+) upkeeps",
            r"Above about \*\*(\d+) concurrent hourly upkeeps\*\*",
            r"\| fly\.io, \$2\.02 \| \*\*(\d+) upkeeps\*\* \|",
        ],
    ),
    (
        "keeper break-even",
        WHY,
        r"roughly (\d+) concurrent hourly upkeeps to fund a \$5 host",
        [
            r"\| Keeper funds a \$5 host at \| (\d+) upkeeps",
            r"needs roughly (\d+) concurrent hourly upkeeps to fund a \$5 host",
        ],
    ),
    # ALGO's price history. An earlier draft claimed ALGO had traded above
    # parity within two years. It had not, and that is the kind of claim a
    # reader checks first, so getting it wrong is expensive out of proportion
    # to its importance to the argument.
    (
        "parity price",
        WHY,
        r"\| \$2\.02/mo \| \*\*\$([\d.]+)\*\* \|",
        [
            r"\| Parity with a \$2\.02 host at \| ALGO \$([\d.]+)",
            r"\| \$2\.02/mo \| \*\*\$([\d.]+)\*\* \|",
        ],
    ),
    (
        "two-year high",
        WHY,
        r"over the last two years is \*\*\$([\d.]+)\*\*",
        [
            r"two-year high: \$([\d.]+)\)",
            r"over the last two years is \*\*\$([\d.]+)\*\*",
        ],
    ),
    (
        "last close above $0.70",
        WHY,
        r"last closed above \$0\.70 on ([\d-]+)",
        [r"last closed above \$0\.70 on \*\*([\d-]+)\*\*"],
    ),
    # The register walkthrough. docs/first-upkeep.md has had this wrong twice;
    # the second time it read 0.0851 against a correct console showing 0.0951,
    # two paragraphs above telling the reader such a mismatch is a bug.
    (
        "register total",
        FIRST_UPKEEP,
        r"should read \*\*([\d.]+) ALGO\*\*",
        [
            r"it should read \*\*([\d.]+) ALGO\*\*",
            r"0\.0621 \+ 0\.0300 \+ 0\.0030 = \*\*([\d.]+)\*\*",
        ],
    ),
    (
        "box deposit",
        FIRST_UPKEEP,
        r"\| Box deposit \| ([\d.]+) \|",
        [r"\| Box deposit \| ([\d.]+) \|"],
    ),
    # Drift: docs said 36 hours and the book said 35.
    (
        "daily drift",
        ARCRON,
        r"slides about \*\*(\d+) hours\*\*",
        [r"slides about \*\*(\d+) hours\*\* against the calendar"],
    ),
]


@pytest.mark.parametrize(
    ("label", "relative_path", "owner_pattern", "guide_patterns"), ECONOMICS
)
def test_guide_agrees_with_the_doc_that_owns_the_figure(
    label: str, relative_path: str, owner_pattern: str, guide_patterns: list[str]
) -> None:
    expected = _owned_figure(relative_path, owner_pattern)
    for guide_pattern in guide_patterns:
        actual = _guide_figure(guide_pattern, label)
        assert actual == expected, (
            f"docs/book/ gives the {label} as {actual!r}; {relative_path} gives it as "
            f"{expected!r}. The doc is canonical: fix the guide."
        )


def test_it_does_not_quote_a_superseded_decoder_path() -> None:
    """The TypeScript decoder twin moved to `js/src/upkeep.ts`.

    Appendix B tells a reader where to find the reference implementation. The
    old path is still named in some places in this repo, so it is exactly the
    kind of thing a compiled book inherits and keeps.
    """
    stale = "web/src/app/core/upkeep.ts"
    assert stale not in guide_text(), (
        f"the guide names {stale}, which does not exist. The decoder twin is js/src/upkeep.ts."
    )
    assert (ROOT / "js/src/upkeep.ts").exists()


# --- the admissions -------------------------------------------------------


def test_it_still_says_what_is_unproven() -> None:
    """The guide's value is that it does not oversell, same as the front door.

    These are the admissions most likely to be quietly dropped once there is an
    audience, and dropping them would make this the marketing book it was
    written not to be.
    """
    text = guide_text().lower()
    for admission in ("unaudited", "upgradeable", "alpha", "not frozen"):
        assert admission in text, f"docs/book/ no longer says the deployment is {admission}"


def test_it_says_the_docs_are_canonical() -> None:
    """A book that presents itself as authoritative is a second source of truth.

    The whole reason this is safe to keep is that it defers: `docs/` is the copy
    that gets corrected, and the guide says so in its preface. Drop that and the
    next reader has no way to know which of two disagreeing numbers to believe.
    """
    text = guide_text()
    assert "source of truth" in text and "derived" in text, (
        "the guide's preface no longer says docs/ is canonical and the book derived from it"
    )
    assert "tests/test_book.py" in text, (
        "the guide no longer tells a reader what keeps its numbers pinned"
    )
