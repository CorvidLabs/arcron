"""Asking a refused request again, and knowing when to stop.

`fledge run health` died on two of three runs on 2026-09-01 with a 403 raised
before the report reached any of its own code. The endpoint was over its
shared daily free-tier quota and shedding about 9% of requests; a health run
sends about 40, so it almost always caught one, while the `curl` used to check
the node was fine almost never did.

Nothing here touches a network. The retry is a decision about an exception and
a schedule, so both are asserted directly, with a fake clock so a test that
proves a four second wait takes no time at all.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from scripts.node_retry import (
    FIRST_WAIT_SECONDS,
    MAX_ATTEMPTS,
    MAX_WAIT_SECONDS,
    describe_request,
    install,
    is_refusal,
    retry_after_seconds,
    retrying,
    status_of,
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


def test_a_server_error_is_deliberately_not_retried() -> None:
    """5xx is a different question and this module does not answer it.

    A 403 is refused by the CDN in front of the node, so the request provably
    never reached algod and replaying it cannot submit anything twice. A 502
    can come from a node that already accepted a transaction, and resending
    that is not a decision to make on the way past.
    """
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
