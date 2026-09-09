"""Every question the last F11 row asks a real chain, in one read-only run.

This is the live-chain half of [#250](https://github.com/CorvidLabs/arcron/issues/250)
F11. The code half merged on 2026-09-08 and every reader in it was proven
against fakes and the published algod and indexer specs, because the machine
it was written on cannot reach `testnet-api.algonode.cloud` at all. The F11
evidence table in `docs/design/mainnet-rollout.md` said so in its last row and
listed four commands somebody else would have to run. Four commands that have
to be typed, read and transcribed is a row nobody runs, so this is the one
command that runs them and prints the row.

**It holds no key and signs nothing.** It reads algod, reads the indexer,
reads this tree's build artifacts, and prints. That is enforced by
`tests/test_preflight.py::test_preflight_cannot_sign_anything`, the same way
the notifier's boundary is enforced, because a probe that could act on what it
finds is a different and much larger thing than a probe.

The seven checks, and what each one is evidence for:

  node       the node's genesis and build version. F05 refuses a node older
             than `BOX_PAGINATION_SINCE`, loudly, so which node G1 is about to
             run on is the first thing to establish rather than a footnote.
  boxes      F05 itself: a real paged listing, requested the way every reader
             here requests one, answered by a node rather than by a fake.
  build      the deployed programs against this tree, the comparison
             `verify_build` makes and the precondition F03 counts from.
  clock      F03: the install round walked out of the indexer's history. On
             TestNet app 769891898 a working walk names the alpha-3 update
             round and not the alpha-2 create, and that difference is the
             whole finding.
  solvency   the fifth question of the 2026-09-01 audit, which no reader can
             answer without the ledger's own minimum balance.
  strangers  F01 and F02's one control, exercised against live creators with
             no webhook in the way: how many upkeeps the notifier would
             announce if it started now with this `--ours`.
  rehearsal  F10 has been blocked on funding a throwaway since 2026-09-05.
             A number is easier to close than a sentence, so this prices it.

Exit 0 when nothing failed, 1 when anything did, which is what a `--gate`
somewhere else would read. A SKIP never fails the run and is never evidence
either: it records a question this run could not put to the chain, and it is
in the output precisely so that nobody reads a clean exit as an answer.

Run:  poetry run python -m scripts.preflight [--network N] [--app-id N]
                                             [--ours A,B] [--rehearsal-creator ADDR]
                                             [--rebuild] [--markdown]
"""

from __future__ import annotations

import argparse
import base64
import os
from dataclasses import dataclass
from datetime import datetime, timezone

from algosdk import encoding

from scripts import keeper_bot, mainnet_clock
from scripts import network as net
from scripts import verify_build
from scripts.deploy import SOAKED_APP_ID
from scripts.keeper_bot import (
    BOX_PAGE_LIMIT,
    BOX_PAGINATION_SINCE,
    is_unrecoverable,
    require_keeper_app,
    resolve_app_id,
    scan_upkeeps,
)
from scripts.mainnet_clock import DEFAULT_HOLD_DAYS
from scripts.registry_health import read_escrowed, read_solvency

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"

#: The contract this asks about. Not a flag: the questions above are about the
#: registry that holds the escrow, and Pulse holds nothing and dates nothing.
CONTRACT = "keeper"

#: The names, in the order they run and print. Ordered so the cheapest answer
#: that invalidates the rest comes first: a node that cannot page a box listing
#: makes the four checks after it unreadable rather than merely wrong.
CHECKS = ("node", "boxes", "build", "clock", "solvency", "strangers", "rehearsal")

#: The throwaway generated for the TestNet ceremony rehearsal, named in
#: `docs/design/mainnet-rollout.md`. An account that has never created an app
#: is half of what TestNet adds over the two LocalNet rehearsals, so it is this
#: address that has to be funded and not whichever account is nearest.
REHEARSAL_CREATOR = "CVM4NOTWQYDRAUVF3EYHLZJXWERUI33GLFCNAV4MR4YVNOT6Z3XJMDGKNE"

#: What the rehearsal costs, from the rollout plan: "about two TestNet ALGO".
#: The create, the fund, a code-changing update and a freeze, with a public
#: node advising the fees.
REHEARSAL_ALGO = 2_000_000

#: `BOX_PAGINATION_SINCE` in the shape a `/versions` build compares in. Kept
#: derived from that constant rather than written out again, so a node this
#: check accepts is exactly a node `_box_page` will work against.
MINIMUM_ALGOD = tuple(int(part) for part in BOX_PAGINATION_SINCE.split("."))

#: Where the shortfall gets closed. Named in the report rather than left to a
#: search, because the whole point of pricing the gap is that somebody acts on
#: it in the same minute they read it.
TESTNET_DISPENSER = "https://bank.testnet.algorand.network/"


@dataclass(frozen=True)
class Result:
    """One check: what it asked, how it went, and what it measured.

    `result` is one line and carries the number, because a status on its own
    is not evidence and the row that gets pasted into the F11 table is this
    line. `detail` is for what an operator would want next, and only when
    there is something to do or something that would otherwise be guessed at.
    """

    name: str
    status: str
    result: str
    detail: str = ""

    @property
    def failed(self) -> bool:
        return self.status == FAIL


def _guard(name: str, run) -> Result:
    """Run one check, turning anything it raises into that check's failure.

    Two services are being asked things here and they fail independently: the
    public algod sheds under quota with a 403 and the public indexer times out,
    on different days. A traceback out of either one would take the other five
    answers with it, and the run that is hard to get is the run against a real
    chain, so no check is allowed to end the process.
    """
    try:
        return run()
    except Exception as raised:  # noqa: BLE001 - any failure is this check's failure
        return Result(name, FAIL, f"raised {type(raised).__name__}: {raised}")


def _version(build: dict) -> tuple[int, int, int]:
    """A `/versions` build as the tuple its parts compare in.

    algod reports major, minor and build number as separate integers, so this
    is the comparison the fields were written for. Doing it on the rendered
    string would put 4.10.0 below 4.7.0.
    """
    return tuple(int(build.get(part, 0) or 0) for part in ("major", "minor", "build_number"))


def check_node(algod, network: str) -> Result:
    """What the node is, and whether the readers on this branch will work on it.

    Two answers matter and they fail differently. A genesis that is not this
    network means the rest of the run is measuring some other chain, so it is
    the first thing decided. A build below `BOX_PAGINATION_SINCE` is the node
    every reader here refuses: pagination mode shipped in 4.7.0, an older node
    ignores `limit` and answers in legacy mode, and `_box_page` raises rather
    than reading a listing that will fail outright once the registry outgrows
    the node's cap.

    A `/versions` with no build at all is neither of those. Proxies in front of
    public endpoints do strip it, and an absent version is not an old one, so
    it is a skip that says which question went unanswered.
    """
    info = dict(algod.versions() or {})
    genesis = str(info.get("genesis_id") or "")
    expected = net.genesis_ids(network)
    if genesis not in expected:
        return Result(
            "node",
            FAIL,
            f"the node reports genesis {genesis or 'nothing'}, not one of "
            f"{', '.join(expected)}: this is not {network}",
        )
    build = dict(info.get("build") or {})
    if not build:
        return Result(
            "node",
            SKIP,
            f"genesis {genesis}, but /versions carried no build, so the node's version "
            f"is unknown and cannot be held to {BOX_PAGINATION_SINCE}",
            "Some proxies strip it. Ask the node itself, or read the paging answer of the boxes check instead.",
        )
    running = _version(build)
    version = ".".join(str(part) for part in running)
    where = f"algod {version} on {genesis}"
    channel = str(build.get("channel") or "")
    commit = str(build.get("commit_hash") or "")[:12]
    detail = ", ".join(part for part in (f"channel {channel}" if channel else "", f"commit {commit}" if commit else "") if part)
    if running < MINIMUM_ALGOD:
        return Result(
            "node",
            FAIL,
            f"{where}, below the {BOX_PAGINATION_SINCE} paged box listings need: every "
            f"reader on this branch refuses this node",
            detail,
        )
    return Result("node", PASS, f"{where}, at or above {BOX_PAGINATION_SINCE}", detail)


def check_boxes(algod, app_id: int) -> Result:
    """The F05 request shape, put to a node instead of to a fake.

    `_box_page` is called for the first page exactly as every reader calls it,
    so what is being tested is the request that ships and not a description of
    it, and its refusal of a legacy answer is reported as this check failing
    rather than as a traceback: a node that answers without a `round` has
    ignored `limit`, and that is a fact about the node worth carrying into the
    other rows. Then `_box_names` walks, so the continuation branch that was
    dead code on the real path until 2026-09-08 is exercised too, even on a
    registry that fits in one page.
    """
    try:
        page = keeper_bot._box_page(algod, app_id, None)
        first = len(page.get("boxes") or [])
        token = page.get("next-token") or None
        keys = ", ".join(sorted(page))
        names = keeper_bot._box_names(algod, app_id)
    except Exception as refused:
        if not is_unrecoverable(refused):
            raise
        return Result("boxes", FAIL, str(refused))
    return Result(
        "boxes",
        PASS,
        f"{len(names)} names in the walk, {first} on the first page; keys {keys}; "
        f"{'a next-token' if token else 'no next-token'}",
        f"limit={BOX_PAGE_LIMIT} through algod_request; the response carried a round, "
        f"which is how the reader knows the node paged rather than answering in legacy mode.",
    )


def check_build(algod, app_id: int, contract: str = CONTRACT, rebuild: bool = False) -> Result:
    """The deployed programs against this tree, byte for byte.

    The same digest over both programs `verify_build` compares, so a swap of
    one alone cannot pass. Not rebuilt by default: a rebuild shells out to
    algokit and takes a minute, and the artifacts under
    `smart_contracts/artifacts` are committed and checked for drift by the
    build lane. Which of the two was compared is in the result line, because
    "matches" means something different when it is a stale artifact that
    matched.
    """
    if rebuild:
        verify_build.rebuild()
    approval, clear = verify_build._programs(verify_build._spec(contract))
    local = verify_build._digest(approval, clear)
    params = algod.application_info(app_id)["params"]
    remote = verify_build._digest(
        base64.b64decode(params["approval-program"]),
        base64.b64decode(params["clear-state-program"]),
    )
    source = "rebuilt from source" if rebuild else "from the committed artifacts"
    if local == remote:
        return Result("build", PASS, f"app {app_id} is this tree byte for byte ({source}), sha256 {local}")
    return Result(
        "build",
        FAIL,
        f"app {app_id} is NOT this tree ({source}): local {local}, chain {remote}",
        "Either the tree has moved on since the deployment, or the deployment is not "
        "what this repository says it is. The clock below is then counting for programs "
        "that are about to be replaced.",
    )


def check_clock(
    algod,
    indexer,
    app_id: int,
    seconds_per_round: float,
    contract: str = CONTRACT,
    hold_days: int = DEFAULT_HOLD_DAYS,
) -> Result:
    """F03: how long these programs have been installed, from chain history.

    `measure` walks the indexer through `read_install_history` and turns every
    way of not knowing into an unknown rather than a number, which is the
    fail-closed half of the finding and is reported here as a failure: an
    install round nobody can establish is not a hold anybody can count.

    The install round and the update count are both named because they are the
    finding. On TestNet app 769891898 the create is alpha-2 and the programs
    are alpha-3's, installed a day later; a clock reporting the create round
    here would be the exact bug F03 describes, and reading the two numbers is
    how a person sees which one it did.
    """
    clock = mainnet_clock.measure(algod, indexer, contract, app_id, seconds_per_round)
    if not clock.history_known:
        return Result(
            "clock",
            FAIL,
            f"the install round is unknown, so the hold cannot be counted: {clock.history_error}",
            "Fail-closed by design. Unknown history never passes the gate; point "
            "INDEXER_SERVER at an indexer that holds this app's history.",
        )
    assert clock.installed_round is not None and clock.days is not None
    served = (
        f"the {hold_days} day hold is complete on these programs"
        if clock.complete(hold_days)
        else f"{clock.remaining(hold_days):.1f} day(s) to go on the {hold_days} day hold"
    )
    return Result(
        "clock",
        PASS,
        f"installed at round {clock.installed_round:,}, {clock.update_count} update(s) in "
        f"history; program age {clock.days:.1f}d, app age {clock.app_days:.1f}d from round "
        f"{clock.created_round:,}",
        served,
    )


def check_solvency(algod, app_id: int) -> Result:
    """Whether the ledger would pay out what the boxes promise.

    Read rather than assumed, both halves: the escrow from every box, and the
    spendable balance from the account's own minimum. A node that will not
    report `min-balance` gets an exception out of `read_solvency` and so a
    failed check here, which is right, because the fallback everything else
    uses is a lower bound and substituting it reports an app that cannot pay
    as solvent.
    """
    escrowed = read_escrowed(algod, app_id)
    solvency = read_solvency(algod, app_id, escrowed)
    where = f"{escrowed:,} uALGO owed, {solvency.spendable:,} spendable"
    detail = f"balance {solvency.amount:,} uALGO, ledger minimum {solvency.min_balance:,}"
    if solvency.shortfall:
        return Result(
            "solvency",
            FAIL,
            f"{where}: short by {solvency.shortfall:,} uALGO",
            detail + ". The last executions and the last cancel fail at the ledger while "
            "the boxes still say they are payable; anyone can pay the difference in.",
        )
    return Result("solvency", PASS, f"{where}, so escrow is covered", detail)


def check_strangers(algod, app_id: int, known_creators: frozenset[str]) -> Result:
    """What the notifier would announce, with no webhook in the way.

    The stranger control is the one thing the unfrozen MainNet window exists to
    catch, and until now it has only ever been diffed against invented
    snapshots. This asks the same question of live box state: every creator in
    the registry, against the allowlist that would be passed to `--ours`.

    A stranger found here is a PASS with the ids in it, not a failure. The
    check is whether the control can tell one, and a registry that has one is
    the case where it demonstrably can; deciding what to do about the upkeep is
    an operator's job and this is a probe. What does fail is the check raising,
    which would mean the creators could not be read at all.
    """
    upkeeps = scan_upkeeps(algod, app_id)
    creators = {upkeep.creator for upkeep in upkeeps}
    census = f"{len(upkeeps)} upkeep(s) from {len(creators)} creator(s)"
    if not known_creators:
        return Result(
            "strangers",
            SKIP,
            f"{census}; with no --ours, nobody is a stranger and nothing would be announced",
            "MainNet refuses to start the notifier this way (--ours or ARCRON_OURS is "
            "required there), because a watcher that can name no stranger is watching for "
            "nothing. Pass the allowlist to make this check say something.",
        )
    strangers = sorted(u.upkeep_id for u in upkeeps if u.creator not in known_creators)
    if not strangers:
        return Result(
            "strangers",
            PASS,
            f"{census}, all inside the {len(known_creators)} allowed; 0 would be announced",
        )
    return Result(
        "strangers",
        PASS,
        f"{census}; {len(strangers)} would be announced as a stranger: "
        f"{', '.join('#' + str(i) for i in strangers)}",
        "A stranger is a fact to see rather than a failure of this tool. On the unfrozen "
        "MainNet deployment one starts a 24 hour clock for an operator decision; on "
        "TestNet it usually means the allowlist is short.",
    )


def _account_balance(algod, address: str) -> int:
    """What an account holds, counting one that has never been funded as zero.

    An address that no transaction has ever reached has no record in the
    ledger, and edges differ on how they say so: some answer a zeroed account,
    some a 404. Both mean the same thing here, and it is the thing this check
    is looking for, so the 404 is read as zero. Nothing wider: a 403 from an
    edge shedding under quota, or a 5xx, still reaches the caller, because
    reporting an outage as an empty account would print a shortfall that is
    not there and send somebody to a dispenser for no reason.
    """
    try:
        info = algod.account_info(address)
    except Exception as refused:
        said_missing = "does not exist" in str(refused).lower() or "no accounts found" in str(refused).lower()
        code = getattr(refused, "code", None)
        if said_missing and code in (None, 404):
            return 0
        raise
    return int(info.get("amount") or 0)


def check_rehearsal(algod, network: str, creator: str = REHEARSAL_CREATOR) -> Result:
    """What the blocked ceremony rehearsal is still short, in uALGO.

    F10 wants the ceremony run on TestNet with an `update` that actually
    replaces bytes, from a creator that has never made an app. Both LocalNet
    rehearsals only ever saw the refusal ("already match"), so the path has
    been argued and not walked, and the plan has said "blocked on funding the
    throwaway" since 2026-09-05. A sentence stays true for months; a number
    with a dispenser link beside it gets closed.

    TestNet only. The rehearsal is a TestNet ceremony by definition, and the
    MainNet run of this tool has no business reading a TestNet balance.
    """
    if network != net.TESTNET:
        return Result(
            "rehearsal",
            SKIP,
            f"the ceremony rehearsal is a TestNet one, and this is {network}",
        )
    balance = _account_balance(algod, creator)
    shortfall = REHEARSAL_ALGO - balance
    holding = f"{creator} holds {balance:,} uALGO of the {REHEARSAL_ALGO:,} the rehearsal needs"
    if shortfall > 0:
        return Result(
            "rehearsal",
            FAIL,
            f"{holding}: short by {shortfall:,} uALGO",
            f"The TestNet dispenser ({TESTNET_DISPENSER}) is the way to close it. Until it "
            f"is closed, F10 stays open and G2 with it.",
        )
    return Result("rehearsal", PASS, f"{holding}: funded")


def run_checks(
    algorand,
    network: str,
    app_id: int,
    *,
    known_creators: frozenset[str] = frozenset(),
    rehearsal_creator: str = REHEARSAL_CREATOR,
    rebuild: bool = False,
) -> list[Result]:
    """Every check, in order, each one insulated from the others' failures."""
    algod = algorand.client.algod
    # `indexer_if_present`, not `indexer`: the latter raises when none is
    # configured, and that is a fact for the clock check to report as unknown
    # history, not a traceback that takes the other six checks with it.
    indexer = algorand.client.indexer_if_present
    seconds = net.seconds_per_round(network)
    return [
        _guard("node", lambda: check_node(algod, network)),
        _guard("boxes", lambda: check_boxes(algod, app_id)),
        _guard("build", lambda: check_build(algod, app_id, rebuild=rebuild)),
        _guard("clock", lambda: check_clock(algod, indexer, app_id, seconds)),
        _guard("solvency", lambda: check_solvency(algod, app_id)),
        _guard("strangers", lambda: check_strangers(algod, app_id, known_creators)),
        _guard("rehearsal", lambda: check_rehearsal(algod, network, rehearsal_creator)),
    ]


def exit_code(results: list[Result]) -> int:
    """0 unless something failed. A skip is not a failure and not evidence."""
    return 1 if any(result.failed for result in results) else 0


def _which_app(app_id: int) -> str:
    # Worth saying in the header of a record that gets pasted somewhere else:
    # `SOAKED_APP_ID` is the registry every soak claim in this repository is
    # about, and a preflight against some other app proves nothing about it.
    return " (the soaked registry)" if app_id == SOAKED_APP_ID else ""


def report(results: list[Result], network: str, app_id: int, current_round: int, when: datetime) -> None:
    """The human report.

    Printed rather than logged, for the reason `scripts/why_figures.py` prints:
    this output is meant to be read once and copied into a document, and a
    level prefix on every line is one more thing to strip out of the paste.
    """
    print(
        f"Arcron preflight: {network} app {app_id}{_which_app(app_id)}, round "
        f"{current_round:,}, {when:%Y-%m-%dT%H:%M:%SZ}"
    )
    print()
    width = max(len(result.name) for result in results)
    for result in results:
        print(f"{result.status}  {result.name:<{width}}  {result.result}")
        if result.detail:
            print(f"{'':6}{'':<{width}}  {result.detail}")
    print()
    counted = {status: sum(1 for r in results if r.status == status) for status in (PASS, FAIL, SKIP)}
    print(
        f"{counted[PASS]} passed, {counted[FAIL]} failed, {counted[SKIP]} skipped. "
        f"A skipped check is a question this run could not put to the chain, so it is "
        f"not evidence for anything and a clean exit does not cover it."
    )


def _cell(text: str) -> str:
    """One line of a markdown cell: no newlines, and no pipe that splits it."""
    return " ".join(text.split()).replace("|", "\\|")


def markdown(results: list[Result], network: str, app_id: int, when: datetime) -> None:
    """The same run as rows for the F11 evidence table.

    Shaped like the table already there, so the record is the run rather than
    somebody's transcription of it, which is the failure mode the four-command
    version of that row had.
    """
    print()
    print(
        f"Preflight on {network}, app {app_id}{_which_app(app_id)}, "
        f"{when:%Y-%m-%d} (UTC), `fledge run preflight`:"
    )
    print()
    print("| check | result |")
    print("|---|---|")
    for result in results:
        # The status, then the measurement, then whatever an operator would do
        # about it, as sentences: a cell is read as prose in the table it lands
        # in, and the two halves ran together without the stop.
        cell = _cell(f"{result.status}. {result.result.rstrip('. ')}.")
        if result.detail:
            cell += f" {_cell(result.detail)}"
        print(f"| `preflight {result.name}` | {cell} |")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    net.add_network_argument(parser)
    parser.add_argument(
        "--app-id",
        type=int,
        default=None,
        help="the keeper app (default: KEEPER_APP_ID from the environment or .env.<network>)",
    )
    parser.add_argument(
        "--ours",
        default=None,
        help=(
            "comma-separated 58-character addresses whose upkeeps are expected, exactly as "
            "the notifier takes them. Any other creator is counted as one it would announce. "
            "NFD names are not resolved (corvid.algo is refused). Defaults to ARCRON_OURS"
        ),
    )
    parser.add_argument(
        "--rehearsal-creator",
        default=REHEARSAL_CREATOR,
        help="the throwaway the TestNet ceremony rehearsal is waiting on (default: %(default)s)",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="compile the contracts first, instead of comparing the committed artifacts",
    )
    parser.add_argument(
        "--markdown",
        action="store_true",
        help="also print the run as rows for the F11 evidence table in docs/design/mainnet-rollout.md",
    )
    args = parser.parse_args(argv)

    ours = args.ours if args.ours is not None else os.environ.get("ARCRON_OURS", "")
    known_creators = frozenset(entry.strip() for entry in ours.split(",") if entry.strip())
    for entry in sorted(known_creators):
        # Syntax only, and refused before anything connects, because the way
        # this goes wrong is silent: `corvid.algo` typed where the address was
        # meant makes our own creator look like somebody else's, and the check
        # would then report a stranger that is us. The failure it cannot catch
        # is the opposite one, an allowlist holding a real outsider, and no
        # check on a string can find that. Same rule and same wording as the
        # notifier, so an allowlist that works there works here.
        if not encoding.is_valid_address(entry):
            parser.error(
                f"--ours entry {entry!r} is not an Algorand address. NFD names are not "
                f"resolved here (corvid.algo is refused); use the 58-character address"
            )
    if not encoding.is_valid_address(args.rehearsal_creator):
        parser.error(f"--rehearsal-creator {args.rehearsal_creator!r} is not an Algorand address")

    algorand = net.connect(args.network)
    # After connect, so `.env.<network>` has been read: the MainNet id lives
    # there and in no file in this tree.
    app_id = resolve_app_id(parser, args.app_id, args.network)
    algod = algorand.client.algod
    try:
        # An id that is not a keeper answers a box listing with an empty list
        # and HTTP 200, so every check below would report a quiet, healthy,
        # entirely imaginary registry. Refused here as the bot and the notifier
        # refuse it.
        require_keeper_app(algod, app_id, args.network)
    except RuntimeError as wrong:
        parser.error(str(wrong))

    current_round = int(algod.status()["last-round"])
    when = datetime.now(timezone.utc)
    results = run_checks(
        algorand,
        args.network,
        app_id,
        known_creators=known_creators,
        rehearsal_creator=args.rehearsal_creator,
        rebuild=args.rebuild,
    )
    report(results, args.network, app_id, current_round, when)
    if args.markdown:
        markdown(results, args.network, app_id, when)
    return exit_code(results)


if __name__ == "__main__":
    raise SystemExit(main())
