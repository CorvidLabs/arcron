"""What the preflight has to get right before anyone runs it against a chain.

Every check here exists because the thing it probes has only ever been proven
against a fake, so the one thing this suite must not do is prove the probe
against a *forgiving* fake. The node is `tests.test_keeper_bot.CountingAlgod`,
which is the real `AlgodClient` with only its network calls stubbed and which
answers a box listing the way an algod of `BOX_PAGINATION_SINCE` answers one:
sorted names, an explicit page size, a `round`, and a `next-token` exactly
when names remain. A reader that only works against a permissive stub fails
here rather than in front of the operator who ran it once from a machine that
can reach TestNet.

The registry underneath is the 33 upkeeps recorded from TestNet app 769891898,
so the counts in these assertions are the counts a real run would print.
"""

from __future__ import annotations

import ast
import base64
from pathlib import Path
from types import SimpleNamespace

import pytest
from algosdk import encoding, error
from algosdk.logic import get_application_address

from scripts import preflight, verify_build
from scripts.keeper_bot import scan_upkeeps
from scripts.preflight import (
    CHECKS,
    FAIL,
    PASS,
    REHEARSAL_ALGO,
    REHEARSAL_CREATOR,
    SKIP,
    check_app,
    check_boxes,
    check_build,
    check_clock,
    check_node,
    check_rehearsal,
    check_solvency,
    check_strangers,
    exit_code,
    run_checks,
)
from tests.test_keeper_bot import APP_ID, LIVE_BOX_HEX, Chain, CountingAlgod, live_chain

PREFLIGHT_SOURCE = Path("scripts/preflight.py")

#: The creator in the recorded box, which is therefore every upkeep's creator
#: unless a test says otherwise.
OURS = encoding.encode_address(bytes.fromhex(LIVE_BOX_HEX)[:32])
STRANGER = encoding.encode_address(bytes(range(32)))

#: A node new enough for the readers on this branch, answering the way algod
#: answers `/versions`: the parts of the build are separate integers.
CURRENT_BUILD = {
    "major": 4,
    "minor": 7,
    "build_number": 0,
    "channel": "stable",
    "commit_hash": "0123456789abcdef0123456789abcdef01234567",
}
TESTNET_VERSIONS = {"genesis_id": "testnet-v1.0", "build": dict(CURRENT_BUILD)}

ALGOD_ADDRESS = "https://node.of.ours.example"
INDEXER_ADDRESS = "https://indexer.of.ours.example"

#: An account's own minimum balance, which it cannot spend and which the
#: rehearsal therefore cannot use.
ACCOUNT_MBR = 100_000

ROUND = 66_894_910
CREATED_ROUND = 66_000_000
UPDATED_ROUND = 66_500_000

#: A throwaway with the two ALGO the ceremony needs *spendable*, which is the
#: two ALGO plus the account minimum it can never spend.
REHEARSAL_FUNDED = {REHEARSAL_CREATOR: {"amount": REHEARSAL_ALGO + ACCOUNT_MBR, "min-balance": ACCOUNT_MBR}}

#: What the local artifacts hash to, and what the fake chain therefore serves
#: back when the deployment is meant to be this tree.
LOCAL_PROGRAMS = verify_build._programs(verify_build._spec("keeper"))
LOCAL_DIGEST = verify_build._digest(*LOCAL_PROGRAMS)


class CreatorChain(Chain):
    """The recorded registry with a creator that can differ per upkeep.

    `Chain` rewrites the numeric fields of the recorded box and leaves its
    32-byte creator alone, which is right for every other suite and useless
    for the one check that is about creators.
    """

    def __init__(self, chain: Chain, creators: dict[int, str]) -> None:
        super().__init__(chain.round, chain.upkeeps)
        self.creators = creators

    def box(self, upkeep_id: int) -> bytes:
        raw = bytearray(super().box(upkeep_id))
        address = self.creators.get(upkeep_id)
        if address is not None:
            raw[0:32] = encoding.decode_address(address)
        return bytes(raw)


class PreflightAlgod(CountingAlgod):
    """The paging node, plus the three endpoints the other checks ask.

    `versions`, `application_info` and a per-address `account_info`: the
    listing and the box reads are inherited exactly as the bot's tests exercise
    them, so nothing about the F05 path is re-implemented here to be kind to
    it.
    """

    def __init__(
        self,
        chain: Chain,
        *,
        versions: dict | None = None,
        programs: tuple[bytes, bytes] | None = None,
        accounts: dict[str, dict] | None = None,
    ) -> None:
        super().__init__(chain)
        # The endpoint the report has to name: a row that cannot tell our own
        # node from a public one does not answer G1's "against the VPS's own
        # node", which is the reason the address is in the header at all.
        self.algod_address = ALGOD_ADDRESS
        self._versions = TESTNET_VERSIONS if versions is None else versions
        self._programs = LOCAL_PROGRAMS if programs is None else programs
        approval, clear = self._programs
        self.accounts = {
            get_application_address(APP_ID): {"amount": 60_000_000, "min-balance": 3_000_000},
            **(accounts or {}),
        }
        self.params = {
            "approval-program": base64.b64encode(approval).decode(),
            "clear-state-program": base64.b64encode(clear).decode(),
            # `require_keeper_app` reads this and nothing else: an app without
            # it is not a keeper, whatever its id.
            "global-state": [
                {"key": base64.b64encode(b"next_upkeep_id").decode(), "value": {"uint": 117}}
            ],
        }

    def versions(self, **kwargs):
        self.counts["versions"] += 1
        return self._versions

    def application_info(self, application_id: int, **kwargs):
        self.counts["app_info"] += 1
        return {"id": application_id, "params": self.params}

    def account_info(self, address: str, exclude=None, **kwargs):
        self.counts["account"] += 1
        if address not in self.accounts:
            raise error.AlgodHTTPError(f"account {address} does not exist", 404)
        return self.accounts[address]


class FakeIndexer:
    """The install history of an app created once and updated once.

    Shaped as `read_install_history` reads it: a create carries the app id it
    made as `created-application-index`, an update is an application
    transaction against the app with `on-completion: update`, and one page
    with no `next-token` is a walk that reached the end.
    """

    indexer_address = INDEXER_ADDRESS

    def __init__(self, *, updates: tuple[int, ...] = (UPDATED_ROUND,), raises: Exception | None = None) -> None:
        self.updates = updates
        self.raises = raises
        self.searches = 0

    def search_transactions(self, **kwargs):
        self.searches += 1
        if self.raises is not None:
            raise self.raises
        transactions = [
            {"confirmed-round": CREATED_ROUND, "created-application-index": APP_ID,
             "application-transaction": {"application-id": 0, "on-completion": "noop"}},
        ]
        transactions += [
            {"confirmed-round": round_,
             "application-transaction": {"application-id": APP_ID, "on-completion": "update"}}
            for round_ in self.updates
        ]
        return {"transactions": transactions}


def algorand(algod, indexer=None):
    """What `network.connect` hands back, as far as anything here reads it."""
    return SimpleNamespace(client=SimpleNamespace(algod=algod, indexer_if_present=indexer))


def node(**kwargs) -> PreflightAlgod:
    return PreflightAlgod(live_chain(ROUND), **kwargs)


def named(results, name):
    return next(result for result in results if result.name == name)


# --- the boundary that matters ---------------------------------------

def test_preflight_cannot_sign_anything() -> None:
    """Read-only is a structural property here, not a promise in a docstring.

    The same test the notifier has, for the same reason: this reads a live
    deployment, including a MainNet one whose creator can still replace its
    programs, and a probe that could act on what it found would be a liability
    with no upside. It also keeps `tests/test_govern.py`'s signing inventory
    honest, which greps for exactly the three ways a script here turns a
    secret into a signature.
    """
    source = PREFLIGHT_SOURCE.read_text()
    forbidden = ("mnemonic", "private_key", "signer", "sign_transaction", "from_environment")
    found = [word for word in forbidden if word in source.lower()]
    assert found == [], f"the preflight must hold no keys, but mentions: {found}"

    for mark in ("account.from_environment(", "mnemonic.to_private_key(", "from_mnemonic("):
        assert mark not in source, f"{mark} would put this in the signing inventory"

    tree = ast.parse(source)
    imported = {entry.module or "" for entry in ast.walk(tree) if isinstance(entry, ast.ImportFrom)}
    assert not any("account" in module for module in imported)


def test_the_checks_run_in_the_documented_order() -> None:
    results = run_checks(algorand(node(), FakeIndexer()), "testnet", APP_ID)
    assert [result.name for result in results] == list(CHECKS)


# --- node -------------------------------------------------------------

class TestTheNode:
    def test_a_current_node_passes_with_its_version_and_genesis(self) -> None:
        result = check_node(node(), "testnet")
        assert result.status == PASS
        assert "algod 4.7.0" in result.result
        assert "testnet-v1.0" in result.result

    def test_a_node_below_the_paging_release_fails_naming_the_minimum(self) -> None:
        # 4.6.0 has `max` and nothing else, so it answers every listing in
        # legacy mode. Every reader on this branch refuses it, and the point of
        # asking first is to learn that from one request rather than from a
        # scan that raises halfway through.
        old = dict(TESTNET_VERSIONS, build=dict(CURRENT_BUILD, minor=6))
        result = check_node(node(versions=old), "testnet")
        assert result.status == FAIL
        assert "4.6.0" in result.result and "4.7.0" in result.result

    def test_a_two_digit_minor_is_not_read_as_smaller(self) -> None:
        # The parts are compared as integers, so 4.10 is above 4.7. Compared as
        # a string it would be below it, and the check would refuse the newest
        # node there is.
        new = dict(TESTNET_VERSIONS, build=dict(CURRENT_BUILD, minor=10))
        assert check_node(node(versions=new), "testnet").status == PASS

    def test_another_network_fails_however_new_the_node_is(self) -> None:
        wrong = dict(TESTNET_VERSIONS, genesis_id="mainnet-v1.0")
        result = check_node(node(versions=wrong), "testnet")
        assert result.status == FAIL
        assert "mainnet-v1.0" in result.result and "not testnet" in result.result

    def test_a_stripped_genesis_is_not_reported_as_the_wrong_network(self) -> None:
        """The branch that is actually reachable, and it used to print a lie.

        A genuine mismatch cannot get this far: `network.connect` asks
        `suggested_params` and refuses to return. What does happen is an edge
        that strips or renames `genesis_id`, and saying "this is not testnet"
        about a node whose genesis was verified twenty lines earlier would put
        a false sentence into an evidence table.
        """
        stripped = {"build": dict(CURRENT_BUILD)}
        result = check_node(node(versions=stripped), "testnet")
        assert result.status == SKIP
        assert "not testnet" not in result.result
        assert "algod 4.7.0" in result.result
        assert "network.connect already verified" in result.detail

    def test_a_stripped_genesis_does_not_soften_an_old_node(self) -> None:
        # The version is decisive on its own: a 4.6.0 node is refused by every
        # reader here whether or not its /versions named a genesis.
        old = {"build": dict(CURRENT_BUILD, minor=6)}
        result = check_node(node(versions=old), "testnet")
        assert result.status == FAIL
        assert "4.6.0" in result.result and "4.7.0" in result.result

    def test_a_versions_without_a_build_skips_rather_than_failing(self) -> None:
        # Proxies do strip it, and an absent version is not an old one. The
        # skip says which question went unanswered.
        stripped = {"genesis_id": "testnet-v1.0"}
        result = check_node(node(versions=stripped), "testnet")
        assert result.status == SKIP
        assert "no build" in result.result


# --- the app id -------------------------------------------------------

class TestTheAppRow:
    def test_a_keeper_passes_and_says_what_made_it_one(self) -> None:
        result = check_app(node(), APP_ID, "testnet")
        assert result.status == PASS
        assert "next_upkeep_id" in result.result

    def test_an_app_that_is_not_a_keeper_fails_naming_the_id(self) -> None:
        algod = node()
        algod.params = {**algod.params, "global-state": []}
        result = check_app(algod, APP_ID, "testnet")
        assert result.status == FAIL
        assert str(APP_ID) in result.result and "not a keeper" in result.result

    def test_a_node_refusing_to_answer_is_not_blamed_on_the_id(self) -> None:
        """`require_keeper_app` wraps every failure as "does not exist".

        That is fine as its own message and misleading as the only thing an
        operator sees, so the row carries the caveat and the node row above it
        carries the 403. The id in `.env.testnet` was correct all along.
        """
        class Refusing(PreflightAlgod):
            def application_info(self, application_id: int, **kwargs):
                raise error.AlgodHTTPError("HTTP Error 403: Forbidden", 403)

        result = check_app(Refusing(live_chain(ROUND)), APP_ID, "testnet")
        assert result.status == FAIL
        assert "403" in result.result
        assert "the node refusing to answer rather than as the id being wrong" in result.detail


# --- boxes ------------------------------------------------------------

class TestTheBoxListing:
    def test_a_paged_response_passes_and_counts_the_names(self) -> None:
        algod = node()
        result = check_boxes(algod, APP_ID)
        assert result.status == PASS
        assert "33 names" in result.result
        assert "keys boxes, round" in result.result
        assert "no next-token" in result.result
        # The request that shipped, not a description of it: an explicit page
        # size through `algod_request`, which is what a node needs to answer in
        # pagination mode at all.
        assert all("limit" in listing for listing in algod.listings)

    def test_a_short_page_with_a_token_is_walked_to_the_end(self) -> None:
        algod = PreflightAlgod(live_chain(ROUND))
        algod.page_size = 10  # the server's byte cap, not the limit asked for
        result = check_boxes(algod, APP_ID)
        assert result.status == PASS
        assert "33 names in the walk, 10 on the first page" in result.result
        assert "a next-token" in result.result

    def test_a_legacy_response_is_a_failed_check_and_not_a_crash(self) -> None:
        """A node that ignores `limit` answers without a round, which is the tell.

        `_box_page` raises `UnrecoverableError` there by design. Reported as
        this check failing, with the message that names the version to move to,
        because the other six answers are still worth having and one of them is
        which node this was.
        """
        result = check_boxes(LegacyAlgod(live_chain(ROUND)), APP_ID)
        assert result.status == FAIL
        assert "4.7.0" in result.result and "legacy" in result.result


    def test_a_reloaded_keeper_bot_cannot_smuggle_the_refusal_past_this_check(self) -> None:
        """The stale-class hazard, pinned where it is legible.

        `tests/test_keeper_sweep.py` reloads `scripts.keeper_bot`, which
        installs a new `UnrecoverableError` class object while every module
        that imported the old one keeps holding it. A check matching on
        identity alone would then let the one error it exists to report escape
        as an unexplained raise, and the whole suite would find it by ordering
        alone, three files away from the cause. Matched by name, as
        `keeper_bot.is_unrecoverable` does and for the same reason.
        """

        class UnrecoverableError(RuntimeError):
            """What a reload of scripts.keeper_bot leaves behind."""

        class Reloaded(PreflightAlgod):
            def algod_request(self, method, requrl, params=None, **kwargs):
                raise UnrecoverableError(
                    "answered the box listing without a round ... needs algod 4.7.0 or later"
                )

        result = check_boxes(Reloaded(live_chain(ROUND)), APP_ID)
        assert result.status == FAIL
        assert "4.7.0" in result.result


class LegacyAlgod(PreflightAlgod):
    """An algod from before pagination: it ignores `limit` and drops `round`."""

    def algod_request(self, method, requrl, params=None, **kwargs):
        page = super().algod_request(method, requrl, params, **kwargs)
        del page["round"]
        return page


# --- build ------------------------------------------------------------

class TestTheDeployedBuild:
    def test_matching_digests_pass_and_say_where_the_artifacts_came_from(self) -> None:
        result = check_build(node(), APP_ID)
        assert result.status == PASS
        assert LOCAL_DIGEST in result.result
        assert "from the committed artifacts" in result.result

    def test_differing_digests_fail_and_print_both(self) -> None:
        other = PreflightAlgod(live_chain(ROUND), programs=(b"not this tree", b"nor this"))
        result = check_build(other, APP_ID)
        assert result.status == FAIL
        assert LOCAL_DIGEST in result.result
        assert verify_build._digest(b"not this tree", b"nor this") in result.result

    def test_a_rebuild_is_only_done_when_it_is_asked_for(self, monkeypatch) -> None:
        # The default has to stay cheap: a rebuild shells out to algokit, and
        # an operator running this to find out whether a node can page should
        # not wait a minute for a compiler.
        builds = []
        monkeypatch.setattr(verify_build, "rebuild", lambda: builds.append(1))
        check_build(node(), APP_ID)
        assert builds == []
        check_build(node(), APP_ID, rebuild=True)
        assert builds == [1]


    def test_an_uncompiled_tree_fails_this_check_and_not_the_run(self, monkeypatch) -> None:
        """`verify_build._spec` and `rebuild` raise `SystemExit`, not `Exception`.

        Both are reachable from here and, through `mainnet_clock.measure`, from
        the clock check, and `SystemExit` is a `BaseException`: an `except
        Exception` guard let it past and the process died having thrown away
        the node and box answers it had already collected, which is exactly
        what the guard promises never to happen.
        """
        def no_artifacts(contract):
            raise SystemExit(f"no ARC-56 spec for {contract}; run `fledge run build` first")

        monkeypatch.setattr(verify_build, "_spec", no_artifacts)
        results = run_checks(algorand(node(), FakeIndexer()), "testnet", APP_ID)
        assert named(results, "build").status == FAIL
        assert "SystemExit" in named(results, "build").result
        assert "no ARC-56 spec" in named(results, "build").result
        # The clock reaches `_spec` through `measure`, so it fails too, and the
        # four answers that do not need the tree are still there.
        assert named(results, "clock").status == FAIL
        assert [named(results, name).status for name in ("node", "app", "boxes", "solvency")] == [PASS] * 4
        assert exit_code(results) == 1


# --- clock ------------------------------------------------------------

class TestTheInstallClock:
    def test_a_known_history_names_the_install_round_and_the_update_count(self) -> None:
        """The whole of F03 in one line: the update, not the create.

        The app here was created at 66,000,000 and updated at 66,500,000, the
        shape TestNet 769891898 is in. A clock counting from the create would
        report the app age as the program age, which is the bug.
        """
        result = check_clock(node(), FakeIndexer(), APP_ID, 2.695)
        assert result.status == PASS
        assert f"installed at round {UPDATED_ROUND:,}" in result.result
        assert "1 update(s) in history" in result.result
        assert f"from round {CREATED_ROUND:,}" in result.result
        # The two ages differ, which is the observation the finding is about.
        assert "program age 12.3d" in result.result
        assert "app age 27.9d" in result.result

    def test_unknown_history_fails_naming_the_reason(self) -> None:
        broken = FakeIndexer(raises=RuntimeError("indexer refused the search"))
        result = check_clock(node(), broken, APP_ID, 2.695)
        assert result.status == FAIL
        assert "indexer refused the search" in result.result
        assert "unknown" in result.result

    def test_no_indexer_at_all_is_unknown_rather_than_zero_updates(self) -> None:
        result = check_clock(node(), None, APP_ID, 2.695)
        assert result.status == FAIL
        assert "no indexer configured" in result.result


# --- solvency ---------------------------------------------------------

class TestSolvency:
    def test_covered_escrow_passes_with_both_numbers(self) -> None:
        owed = sum(state["balance"] for state in live_chain(ROUND).upkeeps.values())
        result = check_solvency(node(), APP_ID)
        assert result.status == PASS
        assert f"{owed:,} uALGO owed" in result.result
        assert "57,000,000 spendable" in result.result

    def test_a_shortfall_fails_with_the_number(self) -> None:
        owed = sum(state["balance"] for state in live_chain(ROUND).upkeeps.values())
        broke = node(accounts={
            get_application_address(APP_ID): {"amount": 1_100_000, "min-balance": 1_000_000}
        })
        result = check_solvency(broke, APP_ID)
        assert result.status == FAIL
        assert f"short by {owed - 100_000:,} uALGO" in result.result

    def test_a_node_that_will_not_say_the_minimum_balance_is_a_failure(self) -> None:
        # Never the 100,000 floor: it is a lower bound, and assuming it reports
        # an app that cannot pay out its escrow as solvent.
        silent = node(accounts={get_application_address(APP_ID): {"amount": 60_000_000}})
        result = preflight._guard("solvency", lambda: check_solvency(silent, APP_ID))
        assert result.status == FAIL
        assert "min-balance" in result.result


    def test_the_shared_scan_is_used_when_it_is_handed_over(self) -> None:
        algod = node()
        upkeeps = scan_upkeeps(algod, APP_ID)
        reads = algod.counts["box_read"]
        result = check_solvency(algod, APP_ID, upkeeps)
        assert result.status == PASS
        assert algod.counts["box_read"] == reads  # not a box re-read between them


# --- strangers --------------------------------------------------------

class TestStrangers:
    def test_no_allowlist_skips_and_says_nobody_would_be_announced(self) -> None:
        result = check_strangers(node(), APP_ID, frozenset())
        assert result.status == SKIP
        assert "33 upkeep(s) from 1 creator(s)" in result.result
        assert "nobody is a stranger" in result.result
        assert "MainNet refuses to start the notifier this way" in result.detail

    def test_an_allowlist_holding_every_creator_passes_with_zero(self) -> None:
        result = check_strangers(node(), APP_ID, frozenset({OURS}))
        assert result.status == PASS
        assert "0 would be announced" in result.result

    def test_an_outside_creator_passes_and_names_its_upkeep(self) -> None:
        """A stranger is a fact to see, not a failure of this tool.

        The control is what is being checked, and a registry with one in it is
        the case that shows the control works. Deciding what to do about
        upkeep 84 is an operator's job.
        """
        chain = CreatorChain(live_chain(ROUND), {84: STRANGER})
        result = check_strangers(PreflightAlgod(chain), APP_ID, frozenset({OURS}))
        assert result.status == PASS
        assert "2 creator(s)" in result.result
        assert "1 would be announced as a stranger: #84" in result.result

    def test_an_nfd_name_is_refused_before_anything_connects(self, monkeypatch, capsys) -> None:
        # `corvid.algo` written where the address was meant would make our own
        # creator look like somebody else's, so it is refused with the same
        # words the notifier refuses it with, and before a node is reached.
        monkeypatch.setattr(preflight.net, "connect", _must_not_connect)
        with pytest.raises(SystemExit) as refused:
            preflight.main(["--network", "testnet", "--app-id", str(APP_ID), "--ours", "corvid.algo"])
        assert refused.value.code == 2
        assert "NFD names are not resolved" in capsys.readouterr().err


def _must_not_connect(network):
    raise AssertionError("the allowlist must be validated before anything connects")


# --- rehearsal --------------------------------------------------------

class TestTheRehearsalThrowaway:
    def test_a_funded_throwaway_passes(self) -> None:
        result = check_rehearsal(node(accounts=REHEARSAL_FUNDED), "testnet")
        assert result.status == PASS
        assert "funded" in result.result

    def test_exactly_two_algo_is_short_by_the_minimum_balance(self) -> None:
        """The number that reads as funded and is not.

        An account cannot spend below its own minimum, so a throwaway holding
        exactly the two ALGO the plan names has 0.1 less than the ceremony can
        actually use, and the ceremony would fail on its last transaction.
        """
        exact = node(accounts={REHEARSAL_CREATOR: {"amount": REHEARSAL_ALGO, "min-balance": ACCOUNT_MBR}})
        result = check_rehearsal(exact, "testnet")
        assert result.status == FAIL
        assert f"short by {ACCOUNT_MBR:,} uALGO" in result.result
        assert "spendable" in result.result

    def test_an_underfunded_throwaway_fails_with_the_exact_shortfall(self) -> None:
        # Half a TestNet ALGO is what the deployer had spendable on 2026-09-05,
        # which is how this came to be blocked in the first place.
        thin = node(accounts={REHEARSAL_CREATOR: {"amount": 500_000, "min-balance": ACCOUNT_MBR}})
        result = check_rehearsal(thin, "testnet")
        assert result.status == FAIL
        assert "short by 1,600,000 uALGO" in result.result
        assert "dispenser" in result.detail

    def test_an_account_that_does_not_exist_yet_reads_as_zero(self) -> None:
        # The throwaway was generated and never funded, so the ledger has no
        # record of it at all. That is the case this check is for, not an error.
        result = check_rehearsal(node(), "testnet")
        assert result.status == FAIL
        assert f"has 0 uALGO spendable (minimum balance 0) of the {REHEARSAL_ALGO:,}" in result.result

    def test_an_edge_shedding_is_not_read_as_an_empty_account(self) -> None:
        class Shedding(PreflightAlgod):
            def account_info(self, address, exclude=None, **kwargs):
                raise error.AlgodHTTPError("HTTP Error 403: Forbidden", 403)

        result = preflight._guard(
            "rehearsal", lambda: check_rehearsal(Shedding(live_chain(ROUND)), "testnet")
        )
        assert result.status == FAIL
        assert "403" in result.result and "short by" not in result.result

    def test_mainnet_skips_it(self) -> None:
        result = check_rehearsal(node(), "mainnet")
        assert result.status == SKIP
        assert "TestNet" in result.result


# --- the run as a whole -----------------------------------------------

class TestTheRunAsAWhole:
    def test_one_check_raising_does_not_stop_the_others(self) -> None:
        """The public algod and the public indexer fail on different days.

        A traceback out of either would take the other five answers with it,
        and the run that is hard to come by is the one against a real chain.
        """
        class Sulking(PreflightAlgod):
            def versions(self, **kwargs):
                raise error.AlgodHTTPError("HTTP Error 403: Forbidden", 403)

        results = run_checks(
            algorand(Sulking(live_chain(ROUND)), FakeIndexer()),
            "testnet",
            APP_ID,
            known_creators=frozenset({OURS}),
        )
        assert named(results, "node").status == FAIL
        assert "AlgodHTTPError" in named(results, "node").result
        assert [r.status for r in results if r.name != "node"] == [
            PASS, PASS, PASS, PASS, PASS, PASS, FAIL  # the throwaway is unfunded here
        ]
        assert exit_code(results) == 1

    def test_a_clean_run_exits_zero(self) -> None:
        funded = node(accounts=REHEARSAL_FUNDED)
        results = run_checks(
            algorand(funded, FakeIndexer()), "testnet", APP_ID, known_creators=frozenset({OURS})
        )
        assert [result.status for result in results] == [PASS] * len(CHECKS)
        assert exit_code(results) == 0

    def test_a_skip_never_fails_the_run(self) -> None:
        # No allowlist, so `strangers` skips; nothing else changes.
        results = run_checks(
            algorand(node(accounts=REHEARSAL_FUNDED), FakeIndexer()), "testnet", APP_ID
        )
        assert named(results, "strangers").status == SKIP
        assert exit_code(results) == 0

    def test_the_registry_is_scanned_once_for_the_whole_run(self) -> None:
        """Solvency and strangers each used to scan, so two thirds of the box
        reads in a run were duplicates of the other third, against an endpoint
        `scripts/node_retry.py` measured shedding about one request in eleven.

        The box check still lists twice on its own, because asking for the
        pages is the whole of what it is evidence for.
        """
        algod = node(accounts=REHEARSAL_FUNDED)
        run_checks(algorand(algod, FakeIndexer()), "testnet", APP_ID,
                   known_creators=frozenset({OURS}))
        assert algod.counts["box_read"] == 33  # one read per box, not two
        assert algod.counts["boxes"] == 3  # the check's page and walk, and the shared scan

    def test_a_failed_scan_is_reported_by_both_checks_that_needed_it(self) -> None:
        # The memoised failure: the second check says what happened rather than
        # repeating a request that has just been refused.
        class Refusing(PreflightAlgod):
            asked = 0

            def application_box_by_name(self, application_id, box_name, **kwargs):
                type(self).asked += 1
                raise error.AlgodHTTPError("HTTP Error 403: Forbidden", 403)

        algod = Refusing(live_chain(ROUND))
        results = run_checks(algorand(algod, FakeIndexer()), "testnet", APP_ID,
                             known_creators=frozenset({OURS}))
        assert named(results, "solvency").status == FAIL
        assert named(results, "strangers").status == FAIL
        assert "403" in named(results, "strangers").result
        assert Refusing.asked == 1  # the refusal was not asked for a second time


class TestTheOutput:
    def test_the_human_report_names_the_network_the_app_the_round_and_the_node(self, monkeypatch, capsys) -> None:
        _run_main(monkeypatch, ["--network", "testnet", "--app-id", str(APP_ID)])
        out = capsys.readouterr().out
        assert f"testnet app {APP_ID} (the soaked registry)" in out
        assert f"round {ROUND:,}" in out
        # G1 asks for a run against the VPS's own node, so the row has to say
        # which node answered. It could not, before.
        assert f"algod {ALGOD_ADDRESS}, indexer {INDEXER_ADDRESS}" in out
        assert "FAIL  rehearsal" in out
        assert "passed, 1 failed" in out

    def test_a_node_that_will_not_give_a_round_still_prints_the_table(self, monkeypatch, capsys) -> None:
        # The round heads the report; it used to be read outside every guard,
        # so one refused status request threw away all eight answers.
        class Speechless(PreflightAlgod):
            def status(self, **kwargs):
                raise error.AlgodHTTPError("HTTP Error 403: Forbidden", 403)

        algod = Speechless(live_chain(ROUND))
        monkeypatch.setattr(preflight.net, "connect", lambda network: algorand(algod, FakeIndexer()))
        code = preflight.main(["--network", "testnet", "--app-id", str(APP_ID)])
        out = capsys.readouterr().out
        assert "round unknown" in out
        assert "PASS  boxes" in out
        assert code == 1  # the unfunded throwaway, not the missing round

    def test_markdown_emits_one_row_per_check_and_nothing_to_trim(self, monkeypatch, capsys) -> None:
        code = _run_main(monkeypatch, ["--network", "testnet", "--app-id", str(APP_ID), "--markdown"])
        out = capsys.readouterr().out
        rows = [line for line in out.splitlines() if line.startswith("| ")]
        checks = [row for row in rows if row.startswith("| `preflight ")]
        assert len(checks) == len(CHECKS)
        assert len(rows) == len(CHECKS) + 1  # the context row, and nothing else
        # The block appends to the table that is already in the rollout doc, so
        # it must carry no header of its own to delete first.
        assert "| check | result |" not in out
        assert "|---|---|" not in out
        context = rows[0]
        assert f"app {APP_ID} (the soaked registry)" in context
        assert ALGOD_ADDRESS in context and INDEXER_ADDRESS in context
        assert "passed," in context
        # A row is a record of a run, so it carries the status and the number.
        assert any(row.startswith("| `preflight boxes` | PASS. 33 names") for row in checks)
        assert code == 1  # the unfunded throwaway

    def test_main_exits_zero_when_every_check_passes(self, monkeypatch, capsys) -> None:
        code = _run_main(
            monkeypatch,
            ["--network", "testnet", "--app-id", str(APP_ID), "--ours", OURS],
            accounts=REHEARSAL_FUNDED,
        )
        capsys.readouterr()
        assert code == 0

    def test_an_app_that_is_not_a_keeper_is_a_failed_row_with_a_table_around_it(self, monkeypatch, capsys) -> None:
        # Still refused, and still clear, but as a row: the operator sees what
        # the node answered as well as what the id was.
        algod = node()
        algod.params = {**algod.params, "global-state": []}
        monkeypatch.setattr(preflight.net, "connect", lambda network: algorand(algod, FakeIndexer()))
        code = preflight.main(["--network", "testnet", "--app-id", str(APP_ID)])
        out = capsys.readouterr().out
        assert code == 1
        assert "FAIL  app" in out and "not a keeper" in out
        assert "PASS  node" in out

    def test_a_node_refusing_everything_does_not_send_the_operator_after_the_app_id(
        self, monkeypatch, capsys
    ) -> None:
        """The failure H3 is about, end to end.

        `require_keeper_app` reads any failure as "App N does not exist ...
        Check KEEPER_APP_ID", and it used to run outside the checks and end the
        process. Against a node shedding 403s the operator got that one line,
        exit 2, and no rows at all, and went to fix an app id that was right.
        """
        class Refusing(PreflightAlgod):
            def versions(self, **kwargs):
                raise error.AlgodHTTPError("HTTP Error 403: Forbidden", 403)

            def application_info(self, application_id: int, **kwargs):
                raise error.AlgodHTTPError("HTTP Error 403: Forbidden", 403)

        algod = Refusing(live_chain(ROUND))
        monkeypatch.setattr(preflight.net, "connect", lambda network: algorand(algod, FakeIndexer()))
        code = preflight.main(["--network", "testnet", "--app-id", str(APP_ID)])
        out = capsys.readouterr().out
        assert code == 1
        assert "FAIL  node" in out and "403" in out
        assert "the node refusing to answer rather than as the id being wrong" in out

    def test_mainnet_without_an_allowlist_is_refused_at_startup(self, monkeypatch, capsys) -> None:
        """The notifier refuses this and so does this, for the same reason.

        Without `--ours` the stranger row skips, everything else passes, and
        the clean exit that gets pasted is evidence of a question nobody asked.
        `fledge run preflight-mainnet` passes no allowlist, so this is the
        refusal that makes it set ARCRON_OURS.
        """
        monkeypatch.delenv("ARCRON_OURS", raising=False)
        monkeypatch.setattr(preflight.net, "connect", _must_not_connect)
        with pytest.raises(SystemExit) as refused:
            preflight.main(["--network", "mainnet", "--app-id", str(APP_ID)])
        assert refused.value.code == 2
        assert "--ours (or ARCRON_OURS) is required on MainNet" in capsys.readouterr().err


def _run_main(monkeypatch, argv, accounts=None) -> int:
    algod = node(accounts=accounts)
    monkeypatch.setattr(preflight.net, "connect", lambda network: algorand(algod, FakeIndexer()))
    return preflight.main(argv)
