"""Asking a refused request again, and knowing when to stop.

`fledge run health` died on two of three runs on 2026-09-01 with a 403 raised
before the report reached any of its own code. The endpoint was over its daily
free-tier quota and shedding about 9% of requests; a health run sends about 40,
so it almost always caught one, while the `curl` used to check the node was
fine almost never did. (Whose quota that is was asserted here once and is now
measured; `scripts/node_retry` carries the numbers, and the short version is
that our own keeper is the one blowing it.)

Nothing here touches a network. The retry is a decision about an exception and
a schedule, so both are asserted directly, with a fake clock so a test that
proves a four second wait takes no time at all. The signed blobs are real, but
signing is arithmetic: `generate_account` and a signer reach nothing.
"""

from __future__ import annotations

import ast
import base64
from pathlib import Path

import pytest

from scripts.node_retry import (
    FIRST_WAIT_SECONDS,
    MAX_ATTEMPTS,
    MAX_WAIT_SECONDS,
    describe_request,
    install,
    is_duplicate_submission,
    is_refusal,
    is_submission,
    request_method,
    retry_after_seconds,
    retrying,
    should_retry,
    status_of,
    submitted_txid,
    wait_before,
)

NETWORK_SOURCE = Path("scripts/network.py")

# The exact message from the traceback that started this. The body Nodely
# returns ("Daily free API quota exceeded.") is not JSON, so algosdk's parse of
# it fails and the message falls back to urllib's rendering of the status. The
# code is genuinely all that survives, which is why `is_refusal` leans on it.
LIVE_403 = "HTTP Error 403: Forbidden"
# And what the indexer client raises for the same refusal: `IndexerHTTPError`
# is a bare Exception carrying the response body and no status at all.
LIVE_INDEXER_403 = "Daily free API quota exceeded.\n230978 requests\n0.15 GB\n"


def algod_error(message: str, code: int | None) -> Exception:
    from algosdk import error

    return error.AlgodHTTPError(message, code)


def indexer_error(message: str) -> Exception:
    from algosdk import error

    return error.IndexerHTTPError(message)


def signed_blob(count: int = 1) -> tuple[bytes, str]:
    """A real submission body, and the id algod would answer it with.

    Offline: a throwaway account and a signature are arithmetic, and no field
    here has to be plausible to anything but the encoder. `count` above 1 makes
    a group, which is several signed transactions concatenated in one body and
    the case `submitted_txid` has to unpack by hand, because algod answers a
    group with its first transaction's id.
    """
    from algosdk import account, encoding, transaction
    from algosdk.atomic_transaction_composer import AccountTransactionSigner

    private_key, address = account.generate_account()
    params = transaction.SuggestedParams(
        fee=1_000,
        first=1,
        last=1_001,
        gh=base64.b64encode(b"arcron-test-genesis-hash-32byte!").decode(),
        flat_fee=True,
    )
    payments = [
        transaction.PaymentTxn(address, params, address, index) for index in range(count)
    ]
    if count > 1:
        transaction.assign_group_id(payments)
    signer = AccountTransactionSigner(private_key)
    signed = signer.sign_transactions(payments, list(range(count)))
    blob = b"".join(base64.b64decode(encoding.msgpack_encode(one)) for one in signed)
    return blob, str(signed[0].get_txid())


def duplicate_error(txid: str) -> Exception:
    """What algod says when it is handed a transaction it already holds."""
    return algod_error(f"TransactionPool.Remember: transaction already in ledger: {txid}", 400)


class _Clock:
    """A `sleep` that records instead of waiting."""

    def __init__(self) -> None:
        self.waits: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)


class _Node:
    """A callable that answers with whatever the script says, in order."""

    def __init__(self, *outcomes: object) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    def __call__(self, *args: object, **kwargs: object) -> object:
        self.calls += 1
        outcome = self.outcomes.pop(0) if self.outcomes else "ok"
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


# --- which failures are worth asking again about -----------------------


def test_the_403_that_started_this_is_a_refusal() -> None:
    assert is_refusal(algod_error(LIVE_403, 403)) is True


def test_a_429_is_a_refusal_too() -> None:
    """Nodely's published policy returns this for the per-second limit.

    We have not measured one, because the quota gave out first, but it is the
    same kind of answer: the edge declining to pass a request on.
    """
    assert is_refusal(algod_error("HTTP Error 429: Too Many Requests", 429)) is True


def test_a_missing_box_is_never_retried() -> None:
    """The one that would break a keeper if it got this wrong.

    `keeper_bot.read_upkeep` reads a 404 as an upkeep cancelled mid-flight,
    which is a lost race and backs nothing off. Retrying it four times would
    make every cancelled upkeep cost four seconds and still end in a 404.
    """
    assert is_refusal(algod_error("box not found", 404)) is False


def test_a_logic_error_is_not_a_refusal() -> None:
    """A target rejecting a call is an answer, and asking again gets the same one."""
    assert is_refusal(algod_error("logic eval error: assert failed pc=1122", 400)) is False


def test_a_server_error_is_not_an_edge_refusal() -> None:
    """`is_refusal` stays narrow: it means the CDN declined to pass a request
    on, which is the thing that was measured at 9%. A 5xx is a different claim
    about a different machine, and whether to ask again about one is
    `should_retry`'s decision because it depends on what was asked."""
    assert is_refusal(algod_error("HTTP Error 502: Bad Gateway", 502)) is False
    assert is_refusal(algod_error("HTTP Error 503: Service Unavailable", 503)) is False


def test_an_indexer_refusal_is_recognised_by_its_words() -> None:
    """`IndexerHTTPError` carries no status, so the body is the only evidence."""
    error = indexer_error(LIVE_INDEXER_403)
    assert status_of(error) is None
    assert is_refusal(error) is True


def test_an_indexer_error_that_is_not_a_refusal_is_left_alone() -> None:
    assert is_refusal(indexer_error("no transactions found")) is False


def test_status_of_ignores_a_code_that_is_not_a_status() -> None:
    """Plenty of exceptions carry a `code` attribute meaning something else."""

    class _Odd(Exception):
        code = "quota"

    assert status_of(_Odd()) is None
    assert is_refusal(_Odd()) is False


# --- 5xx, which depends on what was asked ------------------------------


def test_a_server_error_is_worth_asking_about_again_for_a_read() -> None:
    """The opportunity the first version of this module gave up.

    A 502 or 503 from the edge kills a `health` run exactly as a 403 does, and
    a GET is idempotent whoever generated the error, so refusing to ask again
    bought nothing at all.
    """
    server = algod_error("HTTP Error 502: Bad Gateway", 502)
    assert should_retry(server, "GET") is True
    assert should_retry(algod_error("HTTP Error 503: Service Unavailable", 503), "GET") is True


def test_a_server_error_is_not_asked_about_again_for_a_submission() -> None:
    """Not because a replay could pay twice: an id is a hash of the signed
    blob, so the chain sees one transaction however many copies arrive. It is
    that a 5xx means the node behind the edge is unwell rather than the edge
    shedding, and the duplicate recovery needs algod well enough to answer
    "already in ledger". Nothing here has been measured — 47 hours of keeper
    log hold 2,067 refusals and every one of them is a 403 — so the request
    that spends money is the one that keeps surfacing it."""
    assert should_retry(algod_error("HTTP Error 502: Bad Gateway", 502), "POST") is False


def test_an_unknown_verb_is_treated_as_one_that_cannot_be_replayed() -> None:
    """`retrying` is general, and a caller wrapping something that is not an
    algosdk funnel has no method to offer. Reading that as idempotent would
    make the safe default the one nobody chose."""
    assert should_retry(algod_error("HTTP Error 502: Bad Gateway", 502)) is False


def test_the_edge_refusals_are_retried_whatever_the_verb() -> None:
    """The 403 and the 429 are the edge declining to pass a request on, and
    the argument for replaying a submission holds for both."""
    assert should_retry(algod_error(LIVE_403, 403), "POST") is True
    assert should_retry(algod_error("HTTP Error 429: Too Many Requests", 429), "POST") is True
    assert should_retry(algod_error(LIVE_403, 403)) is True


def test_what_is_neither_a_refusal_nor_a_server_error_is_still_left_alone() -> None:
    assert should_retry(algod_error("box not found", 404), "GET") is False
    assert should_retry(algod_error("logic eval error: assert failed pc=1122", 400), "GET") is False


def test_request_method_reads_the_verb_positionally_or_by_name() -> None:
    assert request_method("get", "/v2/status") == "GET"
    assert request_method(method="post", requrl="/transactions") == "POST"
    assert request_method() == ""
    assert request_method(None) == ""


# --- the schedule ------------------------------------------------------


def test_the_wait_doubles_and_then_stops_doubling() -> None:
    error = algod_error(LIVE_403, 403)
    waits = [wait_before(attempt, error) for attempt in range(1, 6)]
    assert waits == [0.5, 1.0, 2.0, 4.0, 4.0]
    assert waits[0] == FIRST_WAIT_SECONDS
    assert max(waits) == MAX_WAIT_SECONDS


def test_a_stated_retry_after_wins_over_the_schedule() -> None:
    """Unreachable for algod, which discards headers before raising, but the
    wrapper is general and honouring a stated wait beats guessing at one."""

    class _WithHeaders(Exception):
        code = 429
        headers = {"Retry-After": "1.5"}

    assert retry_after_seconds(_WithHeaders()) == 1.5
    assert wait_before(1, _WithHeaders()) == 1.5


def test_a_retry_after_cannot_park_a_report_indefinitely() -> None:
    class _Greedy(Exception):
        code = 429
        headers = {"Retry-After": "600"}

    assert wait_before(1, _Greedy()) == MAX_WAIT_SECONDS


def test_a_retry_after_date_falls_back_to_the_schedule() -> None:
    """`Retry-After` may be an HTTP date. We have never seen one here, and
    guessing at a parse is worse than using the schedule we already trust."""

    class _Dated(Exception):
        code = 429
        headers = {"Retry-After": "Wed, 02 Sep 2026 20:30:57 GMT"}

    assert retry_after_seconds(_Dated()) is None
    assert wait_before(1, _Dated()) == FIRST_WAIT_SECONDS


def test_the_measured_403_asks_for_no_wait_at_all() -> None:
    """Recorded because it is the reason the schedule exists.

    The real refusals carry no `Retry-After`, so there is nothing to obey and
    the doubling is what actually decides every retry this repository makes.
    """
    assert retry_after_seconds(algod_error(LIVE_403, 403)) is None


# --- retrying ----------------------------------------------------------


def test_a_refusal_is_retried_and_the_answer_comes_back() -> None:
    clock = _Clock()
    node = _Node(algod_error(LIVE_403, 403), {"boxes": []})
    call = retrying(node, sleep=clock)

    assert call("GET", "/v2/applications/769891898/boxes") == {"boxes": []}
    assert node.calls == 2
    assert clock.waits == [FIRST_WAIT_SECONDS]


def test_it_gives_up_rather_than_looping_forever() -> None:
    """An endpoint refusing everything is a real failure and has to surface.

    The whole point of a bounded schedule: five attempts and about four
    seconds is a failure an operator can read, where an unbounded loop would
    turn `fledge run health` into a command that never returns.
    """
    clock = _Clock()
    node = _Node(*[algod_error(LIVE_403, 403)] * 20)
    call = retrying(node, sleep=clock)

    with pytest.raises(Exception, match="403"):
        call("GET", "/v2/status")
    assert node.calls == MAX_ATTEMPTS
    assert len(clock.waits) == MAX_ATTEMPTS - 1
    assert sum(clock.waits) == pytest.approx(7.5)


def test_the_error_it_gives_up_with_is_the_node_s_own() -> None:
    """Not wrapped in anything. Whatever a caller already does with an
    `AlgodHTTPError` has to keep working after the retries are exhausted."""
    from algosdk import error

    node = _Node(*[algod_error(LIVE_403, 403)] * 10)
    with pytest.raises(error.AlgodHTTPError) as caught:
        retrying(node, sleep=_Clock())("GET", "/v2/status")
    assert caught.value.code == 403


def test_anything_that_is_not_a_refusal_is_raised_at_once() -> None:
    clock = _Clock()
    node = _Node(algod_error("box not found", 404), {"value": ""})
    with pytest.raises(Exception, match="box not found"):
        retrying(node, sleep=clock)("GET", "/v2/applications/1/box")
    assert node.calls == 1
    assert clock.waits == []


def test_a_run_survives_the_shedding_rate_that_broke_it() -> None:
    """The failure reproduced, then fixed, at the rate that was measured.

    Four refusals in forty-five requests came back from one edge with a frozen
    quota counter. A health run reads a box listing and then each of 33 boxes,
    so it makes about 40 requests: every ninth being refused sinks it, and one
    retry each is enough to carry it through.
    """
    clock = _Clock()
    outcomes: list[object] = []
    for request in range(40):
        if request % 9 == 0:
            outcomes.append(algod_error(LIVE_403, 403))
        outcomes.append({"ok": request})
    node = _Node(*outcomes)
    call = retrying(node, sleep=clock)

    answers = [call("GET", f"/v2/box/{request}") for request in range(40)]

    assert answers == [{"ok": request} for request in range(40)]
    # Five refusals, one retry each, and never a second one in a row.
    assert clock.waits == [FIRST_WAIT_SECONDS] * 5


def test_a_warning_names_the_call_that_was_refused(caplog) -> None:
    """"The node refused something" sends an operator to the wrong place."""
    node = _Node(algod_error(LIVE_403, 403), {})
    with caplog.at_level("WARNING"):
        retrying(node, sleep=_Clock(), describe=describe_request)(
            "GET", "/v2/applications/769891898/boxes"
        )
    assert "GET /v2/applications/769891898/boxes" in caplog.text


def test_describe_reads_the_url_positionally_or_by_name() -> None:
    assert describe_request("GET", "/v2/status") == "GET /v2/status"
    assert describe_request(method="POST", requrl="/v2/transactions") == (
        "POST /v2/transactions"
    )
    assert describe_request() == "request"


def test_a_read_survives_a_bad_gateway() -> None:
    """End to end for the 5xx half: the report keeps running."""
    clock = _Clock()
    node = _Node(algod_error("HTTP Error 502: Bad Gateway", 502), {"boxes": []})
    call = retrying(node, sleep=clock)

    assert call("GET", "/v2/applications/769891898/boxes") == {"boxes": []}
    assert node.calls == 2
    assert clock.waits == [FIRST_WAIT_SECONDS]


def test_a_submission_that_hits_a_bad_gateway_is_raised_at_once() -> None:
    clock = _Clock()
    node = _Node(*[algod_error("HTTP Error 503: Service Unavailable", 503)] * 5)
    with pytest.raises(Exception, match="503"):
        retrying(node, sleep=clock)("POST", "/transactions", data=b"")
    assert node.calls == 1
    assert clock.waits == []


# --- a submission whose first copy landed after all --------------------


def test_the_id_is_taken_from_the_blob_rather_than_the_node_s_prose() -> None:
    """The safety argument used the other way round.

    An Algorand transaction id is a hash of the signed transaction, so the
    bytes already in our hand name the transaction algod says it holds. Reading
    the id out of algod's message instead would tie this to a format we do not
    control, and leaning on an unverifiable claim about the endpoint is exactly
    the mistake that made this function necessary.
    """
    blob, txid = signed_blob()
    assert submitted_txid("POST", "/transactions", None, blob) == txid
    assert submitted_txid("POST", "/transactions", data=blob) == txid


def test_a_group_is_named_by_its_first_transaction() -> None:
    """A group arrives as several signed transactions concatenated in one body,
    which `msgpack_decode` rejects as extra data, and algod answers it with the
    first one's id."""
    group, first = signed_blob(3)
    single, _ = signed_blob(1)
    assert len(group) > len(single)
    assert submitted_txid("POST", "/transactions", data=group) == first


def test_a_body_that_names_nothing_is_not_guessed_at() -> None:
    """This runs while an error is already on its way to the caller, so
    anything unexpected has to leave that error alone rather than replace it."""
    assert submitted_txid("POST", "/transactions") is None
    assert submitted_txid("POST", "/transactions", data=b"") is None
    assert submitted_txid("POST", "/transactions", data="not even bytes") is None


def test_a_replayed_submission_that_already_landed_is_not_a_failure() -> None:
    """The consequence the first version of this module did not handle.

    A 403 is written at the edge, which does not prove the edge had not already
    forwarded the request. When it had, the replay comes back as algod's
    "already in ledger" and the transaction is on chain. Raised, `keeper_bot`
    would ask the registry whether it had moved on, find that it had — we moved
    it — and emit `race_lost` naming the keeper that actually won. Nothing is
    lost but the truth: the fee was paid once and collected once.
    """
    blob, txid = signed_blob()
    clock = _Clock()
    node = _Node(algod_error(LIVE_403, 403), duplicate_error(txid))
    call = retrying(node, sleep=clock)

    assert call("POST", "/transactions", data=blob) == {"txId": txid}
    assert node.calls == 2
    # The caller now waits on its own transaction and watches it confirm, which
    # is what happened. It never learns there was a second copy.
    assert clock.waits == [FIRST_WAIT_SECONDS]


def test_a_transaction_still_in_the_pool_reads_the_same_way() -> None:
    """Whether the first copy is committed or merely accepted decides the
    wording and nothing else: either way the chain has the blob we sent."""
    blob, txid = signed_blob()
    node = _Node(
        algod_error(LIVE_403, 403),
        algod_error("TransactionPool.Remember: transaction already in the pool", 400),
    )
    assert retrying(node, sleep=_Clock())("POST", "/transactions", data=blob) == {"txId": txid}


def test_the_recovery_says_so_in_the_log(caplog) -> None:
    """An `executed` line that came out of a duplicate is a strange enough
    thing that an operator should be able to find why."""
    blob, txid = signed_blob()
    node = _Node(algod_error(LIVE_403, 403), duplicate_error(txid))
    with caplog.at_level("WARNING"):
        retrying(node, sleep=_Clock(), describe=describe_request)(
            "POST", "/transactions", data=blob
        )
    assert txid in caplog.text
    assert "not a failure" in caplog.text


def test_a_duplicate_on_the_first_try_is_the_caller_s_to_hear() -> None:
    """Only a replay this wrapper made can be assumed to be ours.

    A caller whose very first submission is told "already in ledger" sent that
    transaction itself, earlier, and swallowing it here would hide a genuine
    double submission behind a success.
    """
    blob, txid = signed_blob()
    node = _Node(duplicate_error(txid))
    with pytest.raises(Exception, match="already in ledger"):
        retrying(node, sleep=_Clock())("POST", "/transactions", data=blob)
    assert node.calls == 1


def test_a_duplicate_this_cannot_name_is_raised_rather_than_invented() -> None:
    """No blob, no id, and a made-up one would send the caller off to wait on a
    transaction that does not exist. A wrong log line is the lesser wrong."""
    node = _Node(algod_error(LIVE_403, 403), duplicate_error("SOMETXID"))
    with pytest.raises(Exception, match="already in ledger"):
        retrying(node, sleep=_Clock())("POST", "/transactions")
    assert node.calls == 2


def test_only_the_call_that_broadcasts_counts_as_a_submission() -> None:
    """`/v2/transactions/params` and `/v2/transactions/pending/<id>` share the
    prefix, spend nothing, and must not be answered with an invented id."""
    assert is_submission("POST", "/transactions") is True
    assert is_submission("POST", "/v2/transactions") is True
    assert is_submission("POST", "/v2/transactions?foo=1") is True
    assert is_submission("POST", requrl="/transactions") is True
    assert is_submission("GET", "/transactions") is False
    assert is_submission("GET", "/transactions/params") is False
    assert is_submission("POST", "/transactions/pending/ABCDEF") is False
    assert is_submission("POST", "/v2/applications/769891898/boxes") is False
    assert is_submission() is False


def test_a_duplicate_is_recognised_by_its_words_not_its_status() -> None:
    """A bare 400 is also what a target's logic error looks like, and that one
    has to keep reaching the caller untouched."""
    assert is_duplicate_submission(duplicate_error("SOMETXID")) is True
    assert is_duplicate_submission(algod_error("transaction already in pool", 400)) is True
    assert (
        is_duplicate_submission(algod_error("logic eval error: assert failed pc=1122", 400))
        is False
    )


def test_a_duplicate_answer_to_something_that_is_not_a_submission_is_raised() -> None:
    """The gate is the request, not the error text. A read that somehow came
    back with those words has no blob behind it and no id to answer with."""
    node = _Node(algod_error(LIVE_403, 403), duplicate_error("SOMETXID"))
    with pytest.raises(Exception, match="already in ledger"):
        retrying(node, sleep=_Clock())("GET", "/v2/applications/769891898/boxes")
    assert node.calls == 2


# --- installing on a client --------------------------------------------


class _FakeAlgod:
    """Shaped like `algosdk.v2client.algod.AlgodClient` where it matters.

    Every endpoint method on the real client funnels through `algod_request`,
    which is the whole reason `install` patches that one method: an endpoint
    someone reaches for later is covered without their having to remember.
    `application_boxes` is here to prove that, not to be useful.
    """

    def __init__(self, *outcomes: object) -> None:
        self.node = _Node(*outcomes)

    def algod_request(self, method: str, requrl: str, **kwargs: object) -> object:
        return self.node(method, requrl, **kwargs)

    def application_boxes(self, app_id: int, **kwargs: object) -> object:
        return self.algod_request("GET", f"/v2/applications/{app_id}/boxes")


def test_installing_covers_an_endpoint_it_was_never_told_about() -> None:
    client = _FakeAlgod(algod_error(LIVE_403, 403), {"boxes": []})
    install(client, sleep=_Clock())

    assert client.application_boxes(769891898) == {"boxes": []}
    assert client.node.calls == 2


def test_installing_twice_does_not_nest_the_retries() -> None:
    """Five attempts nested inside five is twenty-five, and four seconds
    becomes over a minute. `connect` is called once per script, but a script
    calling it twice must not quietly buy that."""
    clock = _Clock()
    client = _FakeAlgod(*[algod_error(LIVE_403, 403)] * 40)
    install(client, sleep=clock)
    install(client, sleep=clock)

    with pytest.raises(Exception, match="403"):
        client.application_boxes(769891898)
    assert client.node.calls == MAX_ATTEMPTS


def test_installing_on_no_indexer_is_not_an_error() -> None:
    """LocalNet is configured without one, and `indexer_if_present` is None
    there. A TestNet concern must not stop a LocalNet script connecting."""
    assert install(None) is None


def test_install_returns_the_same_client() -> None:
    client = _FakeAlgod({})
    assert install(client, sleep=_Clock()) is client


# --- and that every script actually gets it ----------------------------


def connect_source() -> str:
    tree = ast.parse(NETWORK_SOURCE.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "connect":
            return ast.unparse(node)
    raise AssertionError("network.connect has gone missing")


def test_every_script_gets_this_by_connecting() -> None:
    """The reason this lives in `connect` rather than in `registry_health`.

    Every script in this repository reaches a node through `net.connect`, so
    installing there covers the unattended ones — the notifier and the keeper
    bot — as well as the report that found the problem. A fix applied only
    where it was noticed would have left those two to keep dying quietly.
    """
    source = connect_source()
    assert "node_retry.install" in source
    assert "client.algod" in source
    assert "indexer_if_present" in source
