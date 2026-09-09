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

Every check is a row, including the two that used to refuse the run before it
started. A preflight that exits with one line about an app id, having asked the
node nothing it could print, sends an operator to fix the wrong thing: the run
always prints a table, and the table says which of the node and the id was
wrong.

The eight checks, and what each one is evidence for:

  node       the node's genesis and build version, and the address it was
             asked at. F05 refuses a node older than `BOX_PAGINATION_SINCE`,
             loudly, so which node G1 is about to run on is the first thing to
             establish rather than a footnote.
  app        that the id is a keeper at all. A wrong id answers a box listing
             with an empty list and HTTP 200, so every check after this one
             would report a quiet, healthy, entirely imaginary registry.
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

Exit 3 is the third answer, and it is not a failed check: it is no run at all.
Connecting is the one step that happens before any row can exist, and it fails
for reasons that are about this machine rather than about the deployment (no
`.env.<network>` and no `ALGOD_SERVER`, a node that cannot be reached, a node
that turns out to be another chain, MainNet without `ARCRON_ALLOW_MAINNET`).
Those print the reason and nothing else, and they are given their own code so
that a runner cannot read "nothing was measured" as "something was measured
and it failed".

`--markdown` adds table rows after the human report: a context row naming the
network, the app, the date and the endpoints, then one row per check. The rows
carry no preamble and no `| check | result |` header, because the F11 table
already has one and the point of the flag is that the block appends to it
without being edited first.

Run:  poetry run python -m scripts.preflight [--network N] [--app-id N]
                                             [--ours A,B] [--rehearsal-creator ADDR]
                                             [--rebuild] [--markdown]
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone

from algosdk import encoding

from scripts import keeper_bot, mainnet_clock
from scripts import network as net
from scripts import verify_build
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

#: The exit code for a run that never started, as opposed to one that found
#: something wrong. Distinct from 1 on purpose: no row was printed, so nothing
#: was measured, and a runner treating that as a failed check would record a
#: verdict this process never reached.
NOTHING_RAN = 3

#: The contract this asks about. Not a flag: the questions above are about the
#: registry that holds the escrow, and Pulse holds nothing and dates nothing.
CONTRACT = "keeper"

#: The names, in the order they run and print. Ordered so the cheapest answer
#: that invalidates the rest comes first: a node that cannot be reached, or an
#: id that is not a keeper, makes every row under it unreadable rather than
#: merely wrong, and an operator reading the table top to bottom meets the
#: explanation before the symptoms.
CHECKS = ("node", "app", "boxes", "build", "clock", "solvency", "strangers", "rehearsal")

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
    on different days. A traceback out of either one would take the other seven
    answers with it, and the run that is hard to get is the run against a real
    chain, so no check is allowed to end the process.

    `SystemExit` is caught with the rest, which is not the usual advice and is
    right here. It is a `BaseException`, so `except Exception` let it past, and
    two of the helpers this drives raise it as an ordinary error return:
    `verify_build._spec` on a missing ARC-56 spec and `verify_build.rebuild` on
    a failed compile, both reachable from the build check and, through
    `mainnet_clock.measure`, from the clock check. An uncompiled tree would
    therefore have thrown away the node and box answers, which are the two this
    exists to collect, and the process would have exited on the code path whose
    docstring promises it never does.
    """
    try:
        return run()
    except (Exception, SystemExit) as raised:  # noqa: BLE001 - any failure is this check's failure
        return Result(name, FAIL, f"raised {type(raised).__name__}: {raised}")


def _version(build: dict) -> tuple[int, int, int] | None:
    """A `/versions` build as the tuple its parts compare in, or None if absent.

    algod reports major, minor and build number as separate integers, so this
    is the comparison the fields were written for. Doing it on the rendered
    string would put 4.10.0 below 4.7.0.

    None when there is no `major` to read. A build object carrying only a
    channel and a commit hash is a version this response did not state, and
    defaulting the numbers to zero turned that into `algod 0.0.0`, refused as
    ancient: the same unknown-read-as-old conflation the missing-build skip
    exists to avoid, one field deeper.
    """
    if build.get("major") is None:
        return None
    return tuple(int(build.get(part, 0) or 0) for part in ("major", "minor", "build_number"))


def check_node(algod, network: str) -> Result:
    """What the node is, and whether the readers on this branch will work on it.

    Two answers matter and they fail differently. A build below
    `BOX_PAGINATION_SINCE` is the node every reader here refuses: pagination
    mode shipped in 4.7.0, an older node ignores `limit` and answers in legacy
    mode, and `_box_page` raises rather than reading a listing that will fail
    outright once the registry outgrows the node's cap. That is the decisive
    half and it is reported whatever else is missing.

    The genesis is the other half, and the reading of an *absent* one is the
    thing to get right. A `/versions` that names a different network is a real
    failure and says so. A `/versions` with no `genesis_id` at all is not: an
    edge that strips or renames the field is common, and `network.connect` has
    already put the same question to `suggested_params` and refused to return
    if the answer was wrong, so this run is on the right chain whatever this
    endpoint chose to include. Printing "this is not testnet" into an evidence
    table about a node whose genesis was verified moments earlier would be
    false, so an absent field is recorded as a question this response did not
    answer.

    A `/versions` with no build is the same shape of unknown. Proxies do strip
    it, and an absent version is not an old one.
    """
    info = dict(algod.versions() or {})
    genesis = str(info.get("genesis_id") or "")
    expected = net.genesis_ids(network)
    if genesis and genesis not in expected:
        return Result(
            "node",
            FAIL,
            f"the node reports genesis {genesis}, not one of {', '.join(expected)}: "
            f"this is not {network}",
        )
    build = dict(info.get("build") or {})
    unnamed = (
        "" if genesis
        else " /versions carried no genesis_id, so the genesis here is the one "
             "network.connect already verified through suggested_params, not one this "
             "response named."
    )
    running = _version(build)
    if not build or running is None:
        stated = "carried no build either" if not build else "named no version in its build"
        return Result(
            "node",
            SKIP,
            f"genesis {genesis or 'not named'}, and /versions {stated}, so "
            f"the node's version is unknown and cannot be held to {BOX_PAGINATION_SINCE}",
            ("Some edges strip both. Ask the node itself, or read the paging answer of the "
             "boxes check, which is the same question asked of behaviour instead of of a "
             "version string." + unnamed),
        )
    version = ".".join(str(part) for part in running)
    where = f"algod {version} on genesis {genesis or 'not named'}"
    channel = str(build.get("channel") or "")
    commit = str(build.get("commit_hash") or "")[:12]
    detail = ", ".join(part for part in (f"channel {channel}" if channel else "", f"commit {commit}" if commit else "") if part)
    if running < MINIMUM_ALGOD:
        # A version this old is decisive on its own: every reader here refuses
        # the node, whether or not the response also named its genesis.
        return Result(
            "node",
            FAIL,
            f"{where}, below the {BOX_PAGINATION_SINCE} paged box listings need: every "
            f"reader on this branch refuses this node",
            (detail + unnamed).strip(),
        )
    if not genesis:
        return Result(
            "node",
            SKIP,
            f"{where}, at or above {BOX_PAGINATION_SINCE}, but this response named no "
            f"genesis, so only the version half was answered here",
            (detail + unnamed).strip(),
        )
    return Result("node", PASS, f"{where}, at or above {BOX_PAGINATION_SINCE}", detail)


def check_app(algod, app_id: int, network: str) -> Result:
    """That the id is a keeper, as a row rather than as a refusal.

    `require_keeper_app` is the same startup check the bot and the notifier
    make, and it exists because algod answers a box listing for an app that
    does not exist with an empty list and HTTP 200: a mistyped `KEEPER_APP_ID`
    produced a process that ran clean forever and watched nothing.

    It used to run before the checks and end the process through
    `parser.error`. That is wrong here for a reason worth stating: it wraps
    *any* failure to read the app, a 403 from an edge shedding under quota
    included, in the words "does not exist ... Check KEEPER_APP_ID". An
    operator whose node was refusing requests would have been sent to fix an
    app id that was correct, with no rows printed and nothing to say what the
    node had done. As a row it is one line in the table, under the node row
    that says what the node answered, and the two are read together.
    """
    try:
        require_keeper_app(algod, app_id, network)
    except Exception as refused:
        if not is_unrecoverable(refused):
            raise
        # Which failure it was, from the exception it was raised from. A read
        # that never got an answer is not evidence that the app is absent, and
        # `require_keeper_app` words every failure as "does not exist ... Check
        # KEEPER_APP_ID": true for the 404 it was written for, false for the
        # 403 an edge sheds under quota, and it is the sentence that gets
        # pasted. The caveat used to be in the detail line, where a row read on
        # its own does not carry it, so the head itself is rewritten.
        cause = refused.__cause__
        if cause is not None and getattr(cause, "code", None) != 404:
            return Result(
                "app",
                FAIL,
                f"the node would not say whether app {app_id} exists on {network}: {cause}. "
                f"That is this endpoint refusing to answer, not evidence about the id",
                "Read it with the node row above. An id that is wrong and a node that is "
                "unwell look identical from one refused request, and only one of them is "
                "fixed by editing KEEPER_APP_ID.",
            )
        return Result("app", FAIL, str(refused))
    return Result(
        "app",
        PASS,
        f"app {app_id} exists on {network} and its global state carries next_upkeep_id, "
        f"so it is a keeper",
    )


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
    installed = (
        f"installed at round {clock.installed_round:,}, {clock.update_count} update(s) in "
        f"history; program age {clock.days:.1f}d, app age {clock.app_days:.1f}d from round "
        f"{clock.created_round:,}"
    )
    if not clock.matches_source:
        # `Clock.complete` already refuses this, and reading `complete` alone
        # still printed a program age and a days-to-go beside a PASS, which is
        # the F03 misreading inside the check written to prevent it: those days
        # were served by programs this tree is about to replace, and deploying
        # the change restarts the hold at zero. `mainnet_clock.report` says
        # exactly that about the same Clock, so no number of days to go is
        # printed here either.
        return Result(
            "clock",
            FAIL,
            f"the hold is not running: the local build no longer matches what is deployed "
            f"(local {clock.local_digest}, chain {clock.remote_digest})",
            f"The deployed programs were {installed}, but that time was served by code this "
            f"tree has moved on from; deploying the change restarts the hold at zero. The "
            f"build row above is the same disagreement, seen from the digests.",
        )
    served = (
        f"the {hold_days} day hold is complete on these programs"
        if clock.complete(hold_days)
        else f"{clock.remaining(hold_days):.1f} day(s) to go on the {hold_days} day hold"
    )
    return Result("clock", PASS, installed, served)


def check_solvency(algod, app_id: int, upkeeps=None) -> Result:
    """Whether the ledger would pay out what the boxes promise.

    Read rather than assumed, both halves: the escrow from every box, and the
    spendable balance from the account's own minimum. A node that will not
    report `min-balance` gets an exception out of `read_solvency` and so a
    failed check here, which is right, because the fallback everything else
    uses is a lower bound and substituting it reports an app that cannot pay
    as solvent.

    `upkeeps` is the registry already read for this run, and the sum over their
    escrow is what `read_escrowed` returns (`registry_health.read_upkeeps`
    carries `balance` through as `escrow`). Passing it in is what keeps this
    run to one scan instead of three, against an endpoint `scripts/node_retry`
    measured shedding about one request in eleven. Left out, this reads the
    registry itself, so the check still stands on its own.
    """
    escrowed = (
        read_escrowed(algod, app_id) if upkeeps is None
        else sum(upkeep.balance for upkeep in upkeeps)
    )
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


def check_strangers(algod, app_id: int, known_creators: frozenset[str], upkeeps=None) -> Result:
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

    `upkeeps` is the registry already read for this run; left out, this reads
    it. See `check_solvency` for why the run shares one scan.
    """
    if upkeeps is None:
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


def _spendable(algod, address: str) -> tuple[int, int]:
    """What an account can actually spend, and the minimum under it.

    An address that no transaction has ever reached has no record in the
    ledger, and edges differ on how they say so: some answer a zeroed account,
    some a 404. Both mean the same thing here, and it is the thing this check
    is looking for, so the 404 is read as zero. Nothing wider: a 403 from an
    edge shedding under quota, or a 5xx, still reaches the caller, because
    reporting an outage as an empty account would print a shortfall that is
    not there and send somebody to a dispenser for no reason.

    Spendable rather than the balance, because the balance is the number that
    reads as funded and is not: an account cannot spend below its own minimum,
    so a throwaway holding exactly two ALGO has 0.1 less than the ceremony
    needs. Both numbers are returned so the report can say which it used.

    Spendable can be negative, and on the case this check is for it usually is:
    algod answers for an address with no record by returning a zeroed account
    carrying the 100,000 floor, so a throwaway nobody has funded reads as
    -100,000. That is the right number to subtract from (2.1 ALGO has to
    arrive, not 2) and the wrong number to print, so the arithmetic keeps it
    and the report clamps it.
    """
    try:
        info = algod.account_info(address)
    except Exception as refused:
        said_missing = "does not exist" in str(refused).lower() or "no accounts found" in str(refused).lower()
        code = getattr(refused, "code", None)
        if said_missing and code in (None, 404):
            return 0, 0
        raise
    return int(info.get("amount") or 0) - int(info.get("min-balance") or 0), int(info.get("min-balance") or 0)


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
    spendable, minimum = _spendable(algod, creator)
    holding = (
        f"{creator} has {max(0, spendable):,} uALGO spendable (minimum balance {minimum:,}) "
        f"of the {REHEARSAL_ALGO:,} the rehearsal needs"
    )
    # From the unclamped figure: an account holding nothing behind a 100,000
    # floor needs 2.1 ALGO sent to it, not 2.
    shortfall = REHEARSAL_ALGO - spendable
    if shortfall > 0:
        return Result(
            "rehearsal",
            FAIL,
            f"{holding}: short by {shortfall:,} uALGO",
            f"The TestNet dispenser ({TESTNET_DISPENSER}) is the way to close it. Until it "
            f"is closed, F10 stays open and G2 with it.",
        )
    return Result("rehearsal", PASS, f"{holding}: funded")


def _scan_once(algod, app_id: int):
    """One registry scan for the two checks that both need every box.

    A scan is a listing plus a read per box, and solvency and strangers each
    used to do their own: on the 33 live upkeeps that was 66 box reads where 33
    would do, against an endpoint `scripts/node_retry.py` measured shedding
    about one request in eleven. Halving the reads halves the chance that a run
    an operator went to some trouble to make comes back with a 403 in a row it
    did not need to ask for.

    Lazy and memoised, including the failure: whichever check asks first pays
    for the scan, and if it fails, the same error is handed to the second
    check, so both rows say what happened rather than one of them saying it and
    the other repeating the request that just failed. The box check is
    deliberately not fed from here, because asking for the pages itself is the
    whole of what it is evidence for.
    """
    memo: dict = {}

    def read():
        if "error" in memo:
            raise memo["error"]
        if "upkeeps" not in memo:
            try:
                memo["upkeeps"] = scan_upkeeps(algod, app_id)
            except BaseException as refused:
                memo["error"] = refused
                raise
        return memo["upkeeps"]

    return read


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
    # history, not a traceback that takes the other seven checks with it.
    indexer = algorand.client.indexer_if_present
    seconds = net.seconds_per_round(network)
    registry = _scan_once(algod, app_id)
    return [
        _guard("node", lambda: check_node(algod, network)),
        _guard("app", lambda: check_app(algod, app_id, network)),
        _guard("boxes", lambda: check_boxes(algod, app_id)),
        _guard("build", lambda: check_build(algod, app_id, rebuild=rebuild)),
        _guard("clock", lambda: check_clock(algod, indexer, app_id, seconds)),
        _guard("solvency", lambda: check_solvency(algod, app_id, registry())),
        _guard("strangers", lambda: check_strangers(algod, app_id, known_creators, registry())),
        _guard("rehearsal", lambda: check_rehearsal(algod, network, rehearsal_creator)),
    ]


def exit_code(results: list[Result]) -> int:
    """0 unless something failed. A skip is not a failure and not evidence."""
    return 1 if any(result.failed for result in results) else 0


def _which_app(app_id: int) -> str:
    # Worth saying in the header of a record that gets pasted somewhere else:
    # `net.SOAKED_APP_ID` is the registry every soak claim in this repository is
    # about, and a preflight against some other app proves nothing about it.
    return " (the soaked registry)" if app_id == net.SOAKED_APP_ID else ""


@dataclass(frozen=True)
class Run:
    """What the run was: which chain, which app, when, and asked where.

    The endpoints are here because the row is evidence about a node and used
    not to name one. G1 asks for a preflight against the VPS's own node, and a
    pasted row that cannot tell that node from a public one does not answer it.
    """

    network: str
    app_id: int
    when: datetime
    algod_address: str
    indexer_address: str
    #: None when the node would not say, which is a fact and not a crash.
    current_round: int | None = None

    @property
    def where(self) -> str:
        return f"algod {self.algod_address}, indexer {self.indexer_address}"

    @property
    def at(self) -> str:
        return "round unknown" if self.current_round is None else f"round {self.current_round:,}"


def _address(client, attribute: str) -> str:
    """The endpoint a client is pointed at, as far as it will say."""
    if client is None:
        return "none configured"
    return str(getattr(client, attribute, "") or "unknown")


def describe(algorand, network: str, app_id: int) -> Run:
    """The run's own context, read from the clients rather than from arguments.

    The round is asked for here and not in a check, because it heads the report
    rather than answering a question. A node that will not say is recorded as
    unknown: this used to raise, outside every guard, so an endpoint refusing
    one status request threw away all eight answers before the first was asked.
    """
    algod = algorand.client.algod
    try:
        current_round = int(algod.status()["last-round"])
    except Exception:  # noqa: BLE001 - a header, not a check
        current_round = None
    return Run(
        network=network,
        app_id=app_id,
        when=datetime.now(timezone.utc),
        algod_address=_address(algod, "algod_address"),
        indexer_address=_address(algorand.client.indexer_if_present, "indexer_address"),
        current_round=current_round,
    )


def report(results: list[Result], run: Run) -> None:
    """The human report.

    Printed rather than logged, for the reason `scripts/why_figures.py` prints:
    this output is meant to be read once and copied into a document, and a
    level prefix on every line is one more thing to strip out of the paste.
    """
    print(
        f"Arcron preflight: {run.network} app {run.app_id}{_which_app(run.app_id)}, "
        f"{run.at}, {run.when:%Y-%m-%dT%H:%M:%SZ}"
    )
    print(f"  {run.where}")
    print()
    width = max(len(result.name) for result in results)
    for result in results:
        print(f"{result.status}  {result.name:<{width}}  {result.result}")
        if result.detail:
            print(f"{'':6}{'':<{width}}  {result.detail}")
    print()
    print(
        f"{_tally(results)}. A skipped check is a question this run could not put to the "
        f"chain, so it is not evidence for anything and a clean exit does not cover it."
    )


def _tally(results: list[Result]) -> str:
    counted = {status: sum(1 for r in results if r.status == status) for status in (PASS, FAIL, SKIP)}
    return f"{counted[PASS]} passed, {counted[FAIL]} failed, {counted[SKIP]} skipped"


#: Characters that mean something to a markdown table or to the renderer
#: reading it. Escaped rather than stripped, so what the node said survives
#: intact in the row.
_MARKDOWN = ("\\", "|", "`", "*")


def _cell(text: str) -> str:
    """One line of a markdown cell, whoever wrote the text.

    Most of what lands in a cell is a node's own words: an error body, a
    genesis id, an address. None of it is written for a table, and a 500 page
    quoted verbatim has already been seen to carry pipes and newlines. A pipe
    ends the cell early and silently rewrites every column after it; a stray
    backtick opens a code span that swallows the rest of the row; a pair of
    asterisks turns a fragment bold. Those are escaped and the whitespace is
    collapsed, and the row a node cannot break is the row an operator can paste
    without reading it first. The backslash goes first, or escaping would
    double the ones already there. The underscore is deliberately left alone:
    an intraword one is not emphasis in any renderer this table is read in, and
    escaping it would put a backslash in the middle of `next_upkeep_id` and
    `KEEPER_APP_ID` in almost every row, which is a legibility cost paid on
    every run against a risk that is not there.
    """
    line = " ".join(text.split())
    for mark in _MARKDOWN:
        line = line.replace(mark, "\\" + mark)
    return line


def markdown(results: list[Result], run: Run) -> None:
    """The same run as rows for the F11 evidence table, and nothing else.

    Rows only: no preamble, no `| check | result |` header, no separator. The
    table this joins already has all three, and a block that has to be trimmed
    before it can be pasted is the transcription step this flag exists to
    delete. The first row carries the context the others would otherwise each
    have to repeat, so the whole block is one clean append and one clean
    deletion.
    """
    print()
    print(
        f"| `fledge run preflight` on {run.network}, app {run.app_id}{_which_app(run.app_id)}, "
        f"{run.when:%Y-%m-%d} (UTC) | {_cell(run.where)}; {run.at}; {_tally(results)} |"
    )
    for result in results:
        # The status, then the measurement, then whatever an operator would do
        # about it, as sentences: a cell is read as prose in the table it lands
        # in, and the two halves ran together without the stop.
        cell = _cell(f"{result.status}. {result.result.rstrip('. ')}.")
        if result.detail:
            cell += f" {_cell(result.detail)}"
        print(f"| `preflight {result.name}` | {cell} |")


def _allowlist(parser: argparse.ArgumentParser, source: str, raw: str) -> frozenset[str]:
    """The creators an upkeep may belong to without being announced.

    Syntax only, and it is worth knowing which way that fails. A mistyped
    address, or `corvid.algo` written where the address was meant, makes our
    own creator look like a stranger: loud, wrong, and noticed immediately. The
    failure this cannot catch is the quiet one, an allowlist that includes a
    real outsider, which suppresses exactly the alert it exists for. No check
    on a string can tell those apart; the list is short and should be read by a
    person. Same rule and same wording as the notifier, so an allowlist that
    works there works here.
    """
    entries = frozenset(entry.strip() for entry in raw.split(",") if entry.strip())
    for entry in sorted(entries):
        if not encoding.is_valid_address(entry):
            parser.error(
                f"{source} entry {entry!r} is not an Algorand address. NFD names are not "
                f"resolved here (corvid.algo is refused); use the 58-character address"
            )
    return entries


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
            "NFD names are not resolved (corvid.algo is refused). Defaults to ARCRON_OURS, "
            "which .env.<network> may set: it is read after connecting, because connecting "
            "is what loads that file"
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
        help=(
            "also print the run as table rows to append to the F11 evidence table in "
            "docs/design/mainnet-rollout.md (rows only, no header to trim)"
        ),
    )
    args = parser.parse_args(argv)

    # What was typed is checked before anything connects, because a typo in an
    # address is worth catching without a network round trip. What was
    # *configured* cannot be read yet: `.env.<network>` is loaded by `connect`,
    # so anything sourced from the environment is validated below.
    typed = None if args.ours is None else _allowlist(parser, "--ours", args.ours)
    if not encoding.is_valid_address(args.rehearsal_creator):
        parser.error(f"--rehearsal-creator {args.rehearsal_creator!r} is not an Algorand address")

    try:
        algorand = net.connect(args.network)
    except (Exception, SystemExit) as unreachable:
        # The one step with no row to put its failure in, and the reasons are
        # about this machine rather than about the deployment: no env file and
        # no ALGOD_SERVER, a node that cannot be reached, a node that turns out
        # to be another chain, MainNet without ARCRON_ALLOW_MAINNET. A
        # traceback here reads as a crash in the tool, and exiting 1 would make
        # "nothing ran" indistinguishable from "a check failed" to anything
        # reading the code. So: the reason, on stderr, and a code of its own.
        print(f"Nothing was measured: {unreachable}", file=sys.stderr)
        return NOTHING_RAN

    # After connect, so `.env.<network>` has been read. The MainNet app id
    # lives there and in no file in this tree, and so does the allowlist the
    # MainNet run is refused without: reading ARCRON_OURS before this point
    # made `fledge run preflight-mainnet` impossible to satisfy, because the
    # file that sets it had not been loaded when the refusal fired.
    app_id = resolve_app_id(parser, args.app_id, args.network)
    known_creators = (
        typed if typed is not None
        else _allowlist(parser, "ARCRON_OURS", os.environ.get("ARCRON_OURS", ""))
    )
    if args.network == net.MAINNET and not known_creators:
        # The same refusal the notifier makes, and for the same reason. On a
        # deployment whose id is meant to be unpublished, the stranger count is
        # the one MainNet-critical thing this tool measures, and without an
        # allowlist it measures nothing: the row skips, the other checks pass,
        # and the clean exit that gets pasted is evidence of a question nobody
        # asked. An allowlist that works for the notifier works here.
        parser.error(
            "--ours (or ARCRON_OURS) is required on MainNet: without it no creator is a "
            "stranger, so the one check this run exists to make there says nothing and "
            "still exits zero"
        )
    # Nothing between here and the report is allowed to end the run. Whether
    # the id is a keeper is the `app` check, whether the node answers at all is
    # the `node` check, and both print a row: an operator who gets one line
    # about an app id, from a run that asked the node nothing it could show
    # them, goes and fixes the wrong thing.
    run = describe(algorand, args.network, app_id)
    results = run_checks(
        algorand,
        args.network,
        app_id,
        known_creators=known_creators,
        rehearsal_creator=args.rehearsal_creator,
        rebuild=args.rebuild,
    )
    report(results, run)
    if args.markdown:
        markdown(results, run)
    return exit_code(results)


if __name__ == "__main__":
    raise SystemExit(main())
