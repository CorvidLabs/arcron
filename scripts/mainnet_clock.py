"""How long the installed programs have been the deployment, and what resets it.

MainNet is gated on sustained TestNet time: a hold of some weeks during which
the contract does not change and is not redeployed. If it changes, the clock
starts again.

That rule is easy to state and easy to get wrong from memory, because what
resets it is not "did anyone edit a file". Most work in this repository is
scripts, docs and the console, and none of it touches the deployed programs.
Since app 769891898 went live, 98 commits landed and 15 touched
`smart_contracts/` at all; none of those 15 changed what is on chain.

What resets the clock is new programs, and there are two ways to get them: a
new app id, and an in-place `govern update` that keeps the id and replaces the
bytecode. The first is obvious. The second is the one this module used to get
wrong (#250, F03): it measured from the application's creation round, so an
update inherited the whole lifetime of the app it had just replaced the code
of. On TestNet that is not hypothetical. Alpha-3 was an in-place update the
day after the alpha-2 create, so the clock ran a day fast from its second day.

So there are two ages, and they are reported separately:

- the **app age**, from the creation round, which is how long the boxes, the
  escrow and the upkeeps have existed; and
- the **program age**, from the round the programs now installed were
  installed, which is the only thing the hold is about.

The install round comes from chain history, not from a digest comparison. The
indexer is asked for every application transaction against the app, back to
the create, and the latest one whose on-completion is `update` is the install
round (the creation round if there was never one). Two consequences are worth
stating because both are deliberate:

- **A no-op update resets the clock.** The indexer records that an update
  transaction was confirmed and nothing about whether the bytes it carried
  were the bytes already there. Deciding that it changed nothing would take
  the program bytes, which the indexer does not keep, so the conservative
  reading is that an update is an update. Whoever sends one owns the reset.
- **A -> B -> A resets the clock.** The programs on chain today matching the
  programs on chain a month ago says nothing about the month in between. Only
  the latest update round says how long these bytes have been serving
  continuously, so digest equality now is a precondition of the hold and not
  evidence of it.

Fails closed. If the indexer cannot be reached, the search cannot be paged to
exhaustion, or the creation transaction is not in what came back (pruned
history), the program age is unknown, the report says why, and `--gate` exits
non-zero. Unknown history never passes the gate.

Reads public state and the local build. Signs nothing and deploys nothing.

Run:  poetry run python -m scripts.mainnet_clock --app-id N [--contract keeper]
                                                 [--network N] [--hold-days N]

Exits 0 whatever it finds, because it is a report and a report that fails is
noise in every runner that calls it. Pass `--gate` to make an unfinished, reset
or unknowable hold exit non-zero, which is what you want if something depends
on it.
"""

from __future__ import annotations

import argparse
import base64
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from scripts import network as net
from scripts.keeper_bot import resolve_app_id
from scripts import verify_build

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

REPO = Path(__file__).resolve().parent.parent

#: The hold this repository talks about. Reported rather than enforced: a gate
#: nobody can measure is a gate nobody keeps.
DEFAULT_HOLD_DAYS = 30

#: The indexer's page size ceiling. Every page is one request, and an app with
#: more application transactions than this many pages hold is not this one.
HISTORY_PAGE_LIMIT = 1_000
HISTORY_MAX_PAGES = 10_000


class HistoryUnknown(Exception):
    """The install round could not be established from chain history.

    Raised, not returned, so that every way of not knowing (a network error, a
    truncated search, a create that is not there) ends up in the same place: a
    clock that says it does not know, and a gate that stays shut.
    """


@dataclass(frozen=True)
class InstallHistory:
    """What the indexer says happened to the app's programs, and when."""

    created_round: int
    #: Confirmed rounds of every `update` against the app, ascending.
    update_rounds: tuple[int, ...]

    @property
    def installed_round(self) -> int:
        """When the programs now on chain were put there.

        The latest update if there was one, else the create. Not the earliest
        update whose bytes match today's, because the indexer does not keep
        bytes and because A -> B -> A is not continuity.
        """
        return self.update_rounds[-1] if self.update_rounds else self.created_round


@dataclass(frozen=True)
class Clock:
    contract: str
    app_id: int
    current_round: int
    seconds_per_round: float
    local_digest: str
    remote_digest: str
    #: None when the history could not be read; `history_error` then says why.
    history: InstallHistory | None = None
    history_error: str = ""

    @property
    def history_known(self) -> bool:
        return self.history is not None

    @property
    def matches_source(self) -> bool:
        return self.local_digest == self.remote_digest

    @property
    def created_round(self) -> int | None:
        return self.history.created_round if self.history else None

    @property
    def installed_round(self) -> int | None:
        return self.history.installed_round if self.history else None

    @property
    def update_count(self) -> int:
        return len(self.history.update_rounds) if self.history else 0

    def _days_since(self, round_: int) -> float:
        return (self.current_round - round_) * self.seconds_per_round / 86_400

    @property
    def app_days(self) -> float | None:
        """How long the app has existed. Context; not what the hold measures."""
        return None if self.created_round is None else self._days_since(self.created_round)

    @property
    def days(self) -> float | None:
        """How long the programs now installed have been installed.

        This is the hold. None when the history is unknown, rather than zero
        or the app age, because a number here is read as time served and an
        unknown is not that.
        """
        return None if self.installed_round is None else self._days_since(self.installed_round)

    def remaining(self, hold_days: int) -> float:
        # Unknown history is not "the whole hold to go": the programs may well
        # have served it. It is "cannot say", and the report says that. This is
        # the number for the line under it, and the full hold is the honest
        # floor.
        return float(hold_days) if self.days is None else max(0.0, hold_days - self.days)

    def complete(self, hold_days: int) -> bool:
        """The hold is done only on programs we can see, whose install we can date.

        Three things, and all three: the local build is what is on chain (or
        the hold is running on code about to be replaced), the history is
        known (or the days below are a guess), and the program age has reached
        the hold.
        """
        return (
            self.matches_source
            and self.days is not None
            and self.days >= hold_days
        )


def _is_create(txn: dict, app_id: int) -> bool:
    # A create is the one application transaction whose `application-id` is 0
    # in the transaction itself; the id it made is reported beside it as
    # `created-application-index`, and that is the field to match, because a
    # group can create some other app on the way to calling this one.
    return int(txn.get("created-application-index") or 0) == app_id


def _walk(txns: list[dict]):
    """Every transaction and, recursively, every inner transaction.

    The indexer returns the root of a group that touched the app, with anything
    inner nested under it. An update sent from inside another contract is
    nested there, and the AVM permits one: `itxn_field OnCompletion` accepts
    `UpdateApplication`, and the approval and clear program pages are inner
    transaction fields (go-algorand `logic/eval.go`). Only the top level is
    what a flat scan of the page sees, so a reader that stopped there would
    miss an install that counts exactly as much as a top-level one. That is
    why this walks.
    """
    for txn in txns:
        yield txn
        yield from _walk(txn.get("inner-txns") or [])


def read_install_history(indexer, app_id: int) -> InstallHistory:
    """The create and every update against `app_id`, from the indexer.

    One `search_transactions` per page, `txn_type="appl"` filtered to the app,
    following `next-token` until the indexer stops handing one out. That
    exhaustion is the point: a partial history has no latest update in it,
    only the latest update so far, and the clock would then be counting from
    the wrong one. So anything short of a complete walk raises rather than
    returns what it has.

    `indexer` may be None, which is what `network.connect` yields when
    `INDEXER_SERVER` is not set. That is not a chain that says nothing
    happened; it is a reader with nothing to read, and it is unknown like the
    rest.
    """
    if indexer is None:
        raise HistoryUnknown(
            f"no indexer configured (INDEXER_SERVER), so app {app_id}'s history "
            "cannot be read"
        )
    txns: list[dict] = []
    token: str | None = None
    seen_tokens: set[str] = set()
    for _ in range(HISTORY_MAX_PAGES):
        try:
            page = indexer.search_transactions(
                application_id=app_id,
                txn_type="appl",
                limit=HISTORY_PAGE_LIMIT,
                next_page=token,
            )
        except Exception as cause:  # noqa: BLE001 - every failure means "unknown"
            raise HistoryUnknown(
                f"the indexer could not be searched for app {app_id}'s history: {cause}"
            ) from cause
        txns.extend(page.get("transactions") or [])
        token = page.get("next-token") or None
        if not token:
            break
        if token in seen_tokens:
            # An indexer that hands the same token back is not going to end.
            raise HistoryUnknown(
                f"the indexer repeated page token {token!r} for app {app_id}; "
                "its history cannot be paged to exhaustion"
            )
        seen_tokens.add(token)
    else:
        raise HistoryUnknown(
            f"app {app_id} has more than {HISTORY_MAX_PAGES:,} pages of history; "
            "it cannot be paged to exhaustion"
        )

    created: int | None = None
    updates: list[int] = []
    for txn in _walk(txns):
        call = txn.get("application-transaction") or {}
        if "confirmed-round" not in txn:
            raise HistoryUnknown(
                f"a transaction in app {app_id}'s history has no confirmed round"
            )
        round_ = int(txn["confirmed-round"])
        if _is_create(txn, app_id):
            created = round_ if created is None else min(created, round_)
        elif int(call.get("application-id") or 0) == app_id and call.get("on-completion") == "update":
            updates.append(round_)

    if created is None:
        # A search that does not reach the create is one that stopped short,
        # whatever the indexer said about its next token: pruned history, a
        # partial indexer, or the wrong app. The updates found are real, but
        # the one before them may not be the earliest, so none of it dates
        # anything.
        raise HistoryUnknown(
            f"app {app_id}'s creation transaction is not in the indexer's history "
            f"({len(txns)} application transactions searched); the install round "
            "cannot be established"
        )
    return InstallHistory(created_round=created, update_rounds=tuple(sorted(updates)))


def last_source_commit(contract: str) -> str:
    """The last commit touching this contract's source. Context, not the measure.

    A commit that rewords a docstring changes this and not the compiled
    programs, and the compiled programs are what is deployed. Shown so a reader
    can see the difference rather than assume the two move together.
    """
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%h %ad %s", "--date=short", "--",
             f"smart_contracts/{contract}/"],
            capture_output=True, text=True, check=True, cwd=REPO,
        )
        return out.stdout.strip() or "no commits"
    except Exception as cause:  # noqa: BLE001 - context only, never fatal
        return f"unknown ({cause})"


def measure(algod, indexer, contract: str, app_id: int, seconds_per_round: float) -> Clock:
    spec = verify_build._spec(contract)
    approval, clear = verify_build._programs(spec)
    params = algod.application_info(app_id)["params"]
    try:
        history: InstallHistory | None = read_install_history(indexer, app_id)
        history_error = ""
    except HistoryUnknown as cause:
        # Recorded, not raised: the report still has the digests and the
        # current round to show, and it is the report that says the hold
        # cannot be measured. The gate reads `complete`, which is False.
        history, history_error = None, str(cause)
    return Clock(
        contract=contract,
        app_id=app_id,
        current_round=int(algod.status()["last-round"]),
        seconds_per_round=seconds_per_round,
        local_digest=verify_build._digest(approval, clear),
        remote_digest=verify_build._digest(
            base64.b64decode(params["approval-program"]),
            base64.b64decode(params["clear-state-program"]),
        ),
        history=history,
        history_error=history_error,
    )


def _installed_wording(clock: Clock) -> str:
    if clock.installed_round is None:
        return "programs installed at an unknown round"
    if not clock.update_count:
        return "programs installed at creation"
    history = clock.history
    assert history is not None
    ordinal = history.update_rounds.index(clock.installed_round) + 1
    return (
        f"programs installed at round {clock.installed_round:,} "
        f"(update {ordinal} of {clock.update_count})"
    )


def report(clock: Clock, hold_days: int) -> None:
    logger.info(f"{clock.contract} app {clock.app_id} on round {clock.current_round:,}")
    if clock.created_round is not None and clock.app_days is not None:
        logger.info(
            f"  created at round {clock.created_round:,}, "
            f"app age {clock.app_days:.1f} days at {clock.seconds_per_round} s/round"
        )
    logger.info(f"  {_installed_wording(clock)}")
    if clock.days is not None:
        logger.info(f"  program age {clock.days:.1f} days at {clock.seconds_per_round} s/round")
    logger.info(f"  source matches chain: {'yes' if clock.matches_source else 'NO'}")
    logger.info(f"  last commit to its source: {last_source_commit(clock.contract)}")
    logger.info("")

    if not clock.history_known:
        logger.warning(
            "The install round of the deployed programs is unknown, so this hold "
            "cannot be measured and is not reported as running. Nothing about "
            "the programs is in doubt; what is in doubt is how long they have "
            "been there, and a hold is that number."
        )
        logger.warning(f"  {clock.history_error}")
        return

    if not clock.matches_source:
        logger.warning(
            "The local build no longer matches what is deployed, so this hold is "
            "not running. Deploying that change restarts it at zero. Until then "
            f"the {clock.days:.1f} days above are time served by programs that are "
            "about to be replaced."
        )
        logger.warning(f"  local  {clock.local_digest}")
        logger.warning(f"  chain  {clock.remote_digest}")
        return

    assert clock.installed_round is not None
    if clock.complete(hold_days):
        logger.info(
            f"The {hold_days} day hold is COMPLETE, on the programs installed at "
            f"round {clock.installed_round:,}."
        )
        logger.info(
            "That is the only thing this measures. It says nothing about whether "
            "the documentation is ready, whether anyone else has used it, or "
            "whether you want to."
        )
    else:
        logger.info(
            f"{clock.remaining(hold_days):.1f} days to go on a {hold_days} day hold, "
            f"on the programs installed at round {clock.installed_round:,}."
        )
        if clock.update_count and clock.app_days is not None:
            logger.info(
                f"  (the app is {clock.app_days:.1f} days old; the "
                f"{clock.update_count} update{'s' if clock.update_count != 1 else ''} "
                "since the create do not count towards these programs)"
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    net.add_network_argument(parser)
    parser.add_argument("--app-id", type=int, default=None, help="the keeper app (default: KEEPER_APP_ID from the environment or .env.<network>)")
    parser.add_argument("--contract", default="keeper", choices=verify_build.CONTRACTS)
    parser.add_argument("--hold-days", type=int, default=DEFAULT_HOLD_DAYS)
    parser.add_argument(
        "--gate",
        action="store_true",
        help="exit non-zero unless the hold is complete on the programs installed, "
             "with their install round established from chain history",
    )
    args = parser.parse_args(argv)

    net.load_network(args.network)
    algorand = net.connect(args.network)
    args.app_id = resolve_app_id(parser, args.app_id, args.network)
    # `indexer_if_present`, not `indexer`: the latter raises when no indexer is
    # configured, and a traceback is neither the report this promises to
    # print whatever it finds nor the reason `--gate` promises to give.
    # `measure` turns None into "unknown history", which is what it is.
    clock = measure(
        algorand.client.algod,
        algorand.client.indexer_if_present,
        args.contract,
        args.app_id,
        net.seconds_per_round(args.network),
    )
    report(clock, args.hold_days)
    if args.gate and not clock.complete(args.hold_days):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
