"""Regression tests for the keeper bot's Upkeep box decoder.

The vector is a real box value, recorded from a chain rather than hand-built,
so it pins the encoding the contract actually produces. Its TypeScript twin is
`js/src/upkeep.test.ts`, which reads the same bytes; if the
contract's Upkeep struct changes, both must change together.

Recorded on LocalNet from the 1.0 contract — #7, #14, #8 and #9 together:
upkeep 0 on app 20153, after its first execution. Every field the batch added
holds a non-zero value, so a decoder that ignores any of them cannot pass:
SKIP_AHEAD, a 12,000 µALGO ceiling, a three-argument call, and an ASA bonus
that was actually paid (the asset escrow is 750,000 of the 1,000,000 funded).
"""

import base64
from collections import Counter
from dataclasses import replace

import pytest
from algosdk.v2client.algod import AlgodClient

from scripts import keeper_bot
from scripts.keeper_backoff import Backoff
from scripts.keeper_bot import (
    BOX_PAGE_LIMIT,
    BOX_PAGINATION_SINCE,
    CATCH_UP,
    HEARTBEAT_ROUNDS,
    HEARTBEAT_SCANS,
    MAX_CACHE_ROUNDS,
    SKIP_AHEAD,
    STARVED_RECHECK_ROUNDS,
    Registry,
    UnrecoverableError,
    is_frozen,
    _as_bytes,
    _decode_upkeep,
    effective_fee,
    find_winner,
    partition_due,
    read_upkeep,
    registry_moved_on,
    resolve_app_id,
    scan_upkeeps,
    select_due,
    wait_for_work,
)

# Box value of upkeep 0 on LocalNet app 20153.
LIVE_BOX_HEX = (
    "5defa167e82d6882b1a57beb7d3bb8583440a2e2e19a27358c94744a4fa7e3cf"
    "0000000000004ebb"  # target_app = 20155
    "0082"  # tail offset = 130
    "000000000000000a"  # interval_rounds = 10
    "0000000000003acf"  # next_execution_round = 15055
    "0000000000000fa0"  # fee_per_execution = 4000
    "0000000000008980"  # balance = 35200
    "0000000000000001"  # times_executed = 1
    "0000000000000001"  # policy = SKIP_AHEAD
    "0000000000002ee0"  # fee_cap = 12000
    "0000000000003ac6"  # last_serviced_round = 15046
    "0000000000004ebc"  # fee_asset = 20156
    "000000000003d090"  # asset_fee = 250000
    "00000000000b71b0"  # asset_balance = 750000
    # tail: byte[][] of absorb(uint64,string)'s selector, 7777 and "arcron"
    "00030006000c00160004cb782a4800080000000000001e6100080006617263726f6e"
)


def test_decode_live_box() -> None:
    upkeep = _decode_upkeep(0, bytes.fromhex(LIVE_BOX_HEX))

    assert upkeep.upkeep_id == 0
    assert upkeep.target_app == 20155
    assert upkeep.interval_rounds == 10
    assert upkeep.next_execution_round == 15055
    assert upkeep.fee_per_execution == 4000
    assert upkeep.balance == 35200
    assert upkeep.times_executed == 1
    assert upkeep.policy == SKIP_AHEAD
    assert upkeep.fee_cap == 12000
    assert upkeep.last_serviced_round == 15046
    assert upkeep.fee_asset == 20156
    assert upkeep.asset_fee == 250_000
    assert upkeep.asset_balance == 750_000


def test_the_recorded_box_is_the_length_the_mbr_formula_assumes() -> None:
    """9-byte name + 130-byte head + the encoded argument list."""
    from algosdk import abi

    from smart_contracts.keeper.contract import BOX_MBR_FIXED

    raw = bytes.fromhex(LIVE_BOX_HEX)
    tail = raw[130:]
    assert len(raw) == 130 + len(tail)
    assert 2_500 + 400 * (9 + len(raw)) == BOX_MBR_FIXED + 400 * len(tail)

    # And the tail really is the three app args the target was called with.
    args = [bytes(a) for a in abi.ABIType.from_string("byte[][]").decode(tail)]
    assert args == [
        abi.Method.from_signature("absorb(uint64,string)uint64").get_selector(),
        (7_777).to_bytes(8, "big"),
        abi.ABIType.from_string("string").encode("arcron"),
    ]


def test_effective_fee_walks_the_documented_curve() -> None:
    """The bot's twin of `execute`'s escalation arithmetic.

    Linear from base to cap over one missed interval, then flat. This pins the
    curve's shape only — the twin is checked *against the contract* by
    `tests/test_keeper.py::test_the_fee_rises_linearly_to_the_cap_and_holds`
    and, on a real chain, by `scripts/keeper_e2e.py` stage 16, which asserts
    every fee the contract charged equals what this function predicted.
    """
    upkeep = _decode_upkeep(0, bytes.fromhex(LIVE_BOX_HEX))
    serviced = upkeep.last_serviced_round

    # On time — one interval since the last service — is not late.
    assert effective_fee(upkeep, serviced + 10) == 4_000
    assert effective_fee(upkeep, serviced + 15) == 8_000
    assert effective_fee(upkeep, serviced + 20) == 12_000
    assert effective_fee(upkeep, serviced + 10_000) == 12_000, "the cap holds"
    # And a keeper that has just serviced it is not owed the ceiling again.
    assert effective_fee(upkeep, serviced) == 4_000


def test_a_zero_cap_never_escalates() -> None:
    upkeep = _decode_upkeep(0, bytes.fromhex(LIVE_BOX_HEX))
    upkeep.fee_cap = 0
    upkeep.policy = CATCH_UP
    assert effective_fee(upkeep, upkeep.last_serviced_round + 10_000) == 4_000


def test_as_bytes_accepts_bytes_and_base64() -> None:
    raw = bytes.fromhex(LIVE_BOX_HEX)
    assert _as_bytes(raw) == raw
    assert _as_bytes(bytearray(raw)) == raw
    assert _as_bytes(base64.b64encode(raw).decode()) == raw


def test_select_due_takes_the_richest_work_first() -> None:
    """The bot's actual selection, not a copy of it.

    This is the one behavioural change escalation asks of a keeper: take what
    pays most now, rather than whatever has the lowest id. A regression to
    registry order has to fail something.
    """
    base = _decode_upkeep(0, bytes.fromhex(LIVE_BOX_HEX))
    # Due one interval after the last service, so this is genuine neglect
    # rather than a replay — a replay never escalates.
    due = base.last_serviced_round + 10
    late = replace(base, upkeep_id=1, fee_cap=12_000, next_execution_round=due)
    richer = replace(
        base, upkeep_id=2, fee_per_execution=6_000, fee_cap=0, next_execution_round=due
    )
    not_due = replace(base, upkeep_id=3, fee_cap=0, next_execution_round=due + 90_000)
    broke = replace(base, upkeep_id=4, fee_cap=0, balance=1, next_execution_round=due)

    at_round = base.last_serviced_round + 20  # a whole interval past the service
    order = select_due([richer, late, not_due, broke], at_round)

    assert [u.upkeep_id for u in order] == [1, 2], "escalated first, then the richer one"
    assert effective_fee(order[0], at_round) == 12_000
    assert select_due([richer, late], at_round, is_blocked=lambda i: i == 1)[0].upkeep_id == 2


def test_select_due_falls_back_to_id_order_when_nothing_escalates() -> None:
    base = _decode_upkeep(0, bytes.fromhex(LIVE_BOX_HEX))
    due = base.last_serviced_round + 10
    flat = [replace(base, upkeep_id=i, fee_cap=0, next_execution_round=due) for i in (3, 1, 2)]
    assert [u.upkeep_id for u in select_due(flat, due + 2)] == [1, 2, 3]


def test_decode_rejects_a_box_from_an_older_contract() -> None:
    """Silently decoding the wrong struct is worse than refusing to.

    A box written by a deployment that predates the 1.0 batch is 88 bytes
    against this struct's 130-byte head. Read past its end and Python hands
    back zeros, so a keeper would compute a fee from numbers that were never
    in the box — and act on it. The tail offset is the fingerprint: the
    contract always writes 130 there.
    """
    old_box = bytes.fromhex(
        "2759a71fb768d8d0053eab8aea563a42a2f11a07e6df5175fb1da10d2ebaaa6b"
        "000000002de1cd6a"  # target_app
        "0052"  # tail offset = 82, the head this struct replaced
        "000000000000000a0000000003f864f30000000000000fa0"
        "0000000000003e800000000000000001"
        "00044d4d5f0b"
    )
    with pytest.raises(ValueError, match="too short to be an Upkeep"):
        _decode_upkeep(4, old_box)

    # Long enough, but still not this struct: caught by the offset.
    wrong_shape = bytearray(bytes.fromhex(LIVE_BOX_HEX))
    wrong_shape[40:42] = (82).to_bytes(2, "big")
    with pytest.raises(ValueError, match="different version of the contract"):
        _decode_upkeep(4, bytes(wrong_shape))


def test_resolve_app_id_refuses_to_guess() -> None:
    """No default: there is no deployment of this contract to default to."""
    import argparse

    parser = argparse.ArgumentParser()
    assert resolve_app_id(parser, 123, "testnet") == 123
    with pytest.raises(SystemExit):
        resolve_app_id(parser, None, "testnet")


# --- the frozen guard on the deployer fallback ------------------------

class _FakeAlgod:
    """Just enough of algod to answer `is_frozen`."""

    def __init__(self, state: list[dict]) -> None:
        self._state = state

    def application_info(self, app_id: int) -> dict:
        return {"params": {"global-state": self._state}}


def _entry(key: str, value: int) -> dict:
    return {"key": base64.b64encode(key.encode()).decode(), "value": {"uint": value}}


def test_an_app_with_frozen_zero_is_not_frozen() -> None:
    """The value the guard exists for: the creator can still rewrite execute."""
    assert is_frozen(_FakeAlgod([_entry("frozen", 0)]), 1) is False


def test_an_app_with_frozen_one_is_frozen() -> None:
    assert is_frozen(_FakeAlgod([_entry("frozen", 1)]), 1) is True


def test_an_app_with_no_frozen_key_predates_governance_and_is_immutable() -> None:
    """Absent is not unknown.

    A deployment made before `update` and `freeze` existed carries no flag and
    has no update path at all, so reading a missing key as "not frozen" would
    refuse the fallback on precisely the apps that cannot be rewritten.
    """
    assert is_frozen(_FakeAlgod([_entry("next_upkeep_id", 23)]), 1) is True


def test_the_recorded_box_names_its_creator() -> None:
    """The box always carried the creator and the decoder dropped it.

    Nothing downstream could tell one creator's upkeep from another's, which
    is the detector the unfrozen MainNet window depends on: a stranger's
    upkeep starts a 24-hour clock for an operator decision (freeze only
    bytecode already accepted for permanence, an approved update, or a
    recorded acceptance of the exposure; docs/design/mainnet-rollout.md),
    and until now nothing could say which upkeeps those were.

    Pinned against the same recorded box as the rest of the decoder, so the
    offset is checked against real bytes rather than against the docstring.
    """
    upkeep = _decode_upkeep(1, bytes.fromhex(LIVE_BOX_HEX))
    assert len(upkeep.creator) == 58, "an Algorand address is 58 characters"
    assert upkeep.creator.isupper() or any(c.isdigit() for c in upkeep.creator)


# --- reading the registry back after a failure ------------------------
#
# What separates a lost race from a broken target is what the boxes say a beat
# later, so the code that reads them has to be exact about one thing: a box
# that is gone means the upkeep was cancelled, and a node that will not answer
# means nothing at all. Conflating them was a real bug, found when a free
# TestNet endpoint started returning 403 under load: every failure during the
# outage would have read as a lost race, and the bot would have sailed through
# it convinced it was merely unlucky.


class _BoxAlgod:
    """Just enough of algod to answer `read_upkeep`."""

    def __init__(self, error: Exception | None = None, value: bytes | None = None) -> None:
        self._error = error
        self._value = value

    def application_box_by_name(self, app_id: int, name: bytes) -> dict:
        if self._error is not None:
            raise self._error
        return {"value": base64.b64encode(self._value or b"").decode()}


def _http_error(code: int, message: str) -> Exception:
    from algosdk import error

    return error.AlgodHTTPError(message, code)


def test_a_cancelled_upkeep_reads_as_gone() -> None:
    algod = _BoxAlgod(error=_http_error(404, "box not found"))
    assert read_upkeep(algod, 1, 7) is None


def test_a_404_that_does_not_say_box_not_found_is_not_a_cancelled_upkeep() -> None:
    """A 404 alone is also an edge's answer for a path it does not serve, and a
    node's for an application that does not exist. algod's own words for a
    missing box are `box not found` (errBoxDoesNotExist), and when a status is
    there to check, both are required before the upkeep is called gone."""
    algod = _BoxAlgod(error=_http_error(404, "HTTP Error 404: Not Found"))
    with pytest.raises(Exception, match="404"):
        read_upkeep(algod, 1, 7)


def test_a_missing_box_reported_without_a_status_is_still_gone() -> None:
    """Not every client carries a code; one that hands back algod's body and
    nothing else has said the same thing in the only way it can."""
    algod = _BoxAlgod(error=RuntimeError("box not found"))
    assert read_upkeep(algod, 1, 7) is None


def test_a_node_that_will_not_answer_is_not_a_cancelled_upkeep() -> None:
    """The 403 that found this. Anything but a missing box has to be raised."""
    algod = _BoxAlgod(error=_http_error(403, "Forbidden"))
    with pytest.raises(Exception, match="Forbidden"):
        read_upkeep(algod, 1, 7)


def test_registry_moved_on_reports_no_news_when_the_node_is_down() -> None:
    """None, not False: "cannot tell" must not read as "nothing happened"."""
    algod = _BoxAlgod(error=_http_error(403, "Forbidden"))
    before = _decode_upkeep(0, bytes.fromhex(LIVE_BOX_HEX))
    moved, after = registry_moved_on(algod, 1, before)
    assert moved is None
    assert after is None


def test_registry_moved_on_sees_an_execution() -> None:
    before = _decode_upkeep(0, bytes.fromhex(LIVE_BOX_HEX))
    raw = bytearray(bytes.fromhex(LIVE_BOX_HEX))
    raw[74:82] = (before.times_executed + 1).to_bytes(8, "big")
    moved, after = registry_moved_on(_BoxAlgod(value=bytes(raw)), 1, before)
    assert moved is True
    assert after is not None and after.times_executed == before.times_executed + 1


def test_registry_moved_on_sees_a_still_upkeep() -> None:
    before = _decode_upkeep(0, bytes.fromhex(LIVE_BOX_HEX))
    moved, after = registry_moved_on(_BoxAlgod(value=bytes.fromhex(LIVE_BOX_HEX)), 1, before)
    assert moved is False
    assert after == before


def test_a_vanished_box_counts_as_moved_on() -> None:
    """Cancelled mid-flight is a race nobody won, and nothing to back off."""
    before = _decode_upkeep(0, bytes.fromhex(LIVE_BOX_HEX))
    algod = _BoxAlgod(error=_http_error(404, "box not found"))
    moved, after = registry_moved_on(algod, 1, before)
    assert moved is True
    assert after is None


# --- naming the winner ------------------------------------------------


class _BlockAlgod:
    def __init__(self, block: dict) -> None:
        self._block = block

    def block_info(self, block_round: int) -> dict:
        return {"block": self._block}


def test_find_winner_reads_the_sender_out_of_the_block() -> None:
    """The only durable record of a race: the winner's own transaction.

    A losing keeper's is in no block at all, so if this cannot name the winner
    a lost race leaves nothing behind but an opinion.
    """
    winner = "3PF5XJY3NDUHLTQ45LCTJCRWIN3PMLXUDMXYKPWAH7VW7FOR7JDZKQMHDY"
    block = {
        "txns": [
            {"txn": {"apid": 999, "apaa": ["W0nMXA==", "AAAAAAAAAR4="], "snd": winner}},
            {
                "txn": {
                    "apid": 1002,
                    "apaa": ["W0nMXA==", base64.b64encode((286).to_bytes(8, "big")).decode()],
                    "snd": winner,
                }
            },
        ]
    }
    assert find_winner(_BlockAlgod(block), 1002, 286, 5186) == winner
    # A different upkeep in the same block is not this race.
    assert find_winner(_BlockAlgod(block), 1002, 287, 5186) is None


def test_find_winner_never_raises() -> None:
    """Best effort: a node that does not serve blocks must not break a scan."""

    class _Broken:
        def block_info(self, block_round: int) -> dict:
            raise RuntimeError("no blocks here")

    assert find_winner(_Broken(), 1002, 1, 5186) is None
    assert find_winner(_BlockAlgod({}), 1002, 1, 0) is None


# --- what the loop reads, and how often -------------------------------
#
# `docs/reviews/2026-09-01-opus-5-audit-verification.md` §5 measured this bot
# at about **211,000 requests a day** against a public node whose refused
# daily-quota counter stood at 230,824: one process, essentially the whole
# allowance. The shape was 11,543 scans over 63,013 rounds — one every 5.46
# rounds — and every scan re-read all 33 boxes to find the handful that were
# due.
#
# `Registry` is the repair, and these tests are how the new figure is measured
# rather than asserted. They count at the client, through a subclass of the
# real `AlgodClient` with only the methods that reach the network stubbed, so a
# call the production path makes and this fake does not implement is a failure
# here rather than a surprise against a node.


def _box_with(**fields) -> bytes:
    """The recorded LocalNet box with some fields moved, still 130 bytes of head."""
    offsets = {
        "target_app": (32, 40),
        "interval_rounds": (42, 50),
        "next_execution_round": (50, 58),
        "fee_per_execution": (58, 66),
        "balance": (66, 74),
        "times_executed": (74, 82),
        "policy": (82, 90),
        "fee_cap": (90, 98),
        "last_serviced_round": (98, 106),
        "fee_asset": (106, 114),
        "asset_fee": (114, 122),
        "asset_balance": (122, 130),
    }
    raw = bytearray(bytes.fromhex(LIVE_BOX_HEX))
    for name, value in fields.items():
        start, end = offsets[name]
        raw[start:end] = int(value).to_bytes(end - start, "big")
    return bytes(raw)


class Chain:
    """A registry that keeps its own round and rewrites boxes when they execute."""

    def __init__(self, round: int, upkeeps: dict[int, dict]) -> None:
        self.round = round
        self.upkeeps = upkeeps

    def box(self, upkeep_id: int) -> bytes:
        state = self.upkeeps[upkeep_id]
        return _box_with(
            interval_rounds=state["interval"],
            next_execution_round=state["next"],
            fee_per_execution=state["fee"],
            balance=state["balance"],
            fee_cap=0,
            fee_asset=0,
            asset_fee=0,
            asset_balance=0,
            last_serviced_round=state.get("serviced", 0),
        )

    def execute(self, upkeep_id: int) -> int:
        """What `execute` does to the box, so the simulation stays honest."""
        state = self.upkeeps[upkeep_id]
        assert self.round >= state["next"], "executed an upkeep that was not due"
        assert state["balance"] >= state["fee"], "executed an upkeep that cannot pay"
        state["balance"] -= state["fee"]
        state["serviced"] = self.round
        missed = (self.round - state["next"]) // state["interval"]
        state["next"] += (missed + 1) * state["interval"]  # SKIP_AHEAD
        return state["next"]


class CountingAlgod(AlgodClient):
    """The real client with only its network calls stubbed, counting each one.

    Subclassed rather than duck-typed for the reason
    `tests/test_registry_health.py::TestEveryBoxIsCounted` gives: a fake that
    accepts a keyword the real client rejects lets a broken reader pass here
    and fail against a node, which is how the box pagination shipped broken.
    The subclassing earned its keep a second time on 2026-09-08: when the
    readers moved their listing to `algod_request`, the real client's
    `algod_request` was reached and fell over on the network it has no access
    to, which is how this fake found out it had to change too.

    The listing is answered the way an algod of `BOX_PAGINATION_SINCE` or
    later answers it, by the handler read for `keeper_bot._box_page`: names
    sorted, at most `limit` of them, a `round`, and a `next-token` (the last
    name of the page, in `b64:` form) exactly when names remain after it.
    `page_size`, when smaller than the limit asked for, stands in for the
    server's byte cap: a page shorter than `limit` with a token on it.

    Three things a real registry does between one request and the next are
    scripted per upkeep, so a test can say which box misbehaves:
    `vanished` boxes are still listed but 404 on the read, which is a
    `cancel` landing in the gap; `refusing` boxes answer the read with the
    403 the public edge sheds under quota; `malformed` boxes read back bytes
    that are not an Upkeep.
    """

    def __init__(self, chain: Chain, page_size: int | None = None) -> None:
        self.chain = chain
        self.counts: Counter[str] = Counter()
        self.page_size = page_size
        #: The query parameters of every listing request, in order.
        self.listings: list[dict] = []
        self.vanished: set[int] = set()
        self.refusing: set[int] = set()
        self.malformed: set[int] = set()

    def status(self, **kwargs):
        self.counts["status"] += 1
        return {"last-round": self.chain.round}

    def status_after_block(self, block_num=None, round_num=None, **kwargs):
        self.counts["status"] += 1
        wanted = (block_num if block_num is not None else round_num) or 0
        self.chain.round = max(self.chain.round + 1, wanted + 1)
        return {"last-round": self.chain.round}

    def application_boxes(self, application_id: int, limit: int = 0, **kwargs):
        # The typed method sends `limit` as the legacy `max` and can carry no
        # `next`, so it cannot page. Until 2026-09-01 the readers asked it for
        # `next` and would have raised TypeError against a node; until
        # 2026-09-08 they took the first page from it, which never carries a
        # token, so the continuation was dead code. Either way round, a
        # reader that reaches this method is one that cannot read past the
        # server's cap, and it fails here rather than against a node.
        raise AssertionError(
            "the box readers must list through algod_request with limit and next; "
            "application_boxes cannot page"
        )

    def algod_request(self, method, requrl, params=None, **kwargs):
        # The one method on the real client that reaches the network, so this
        # is what the request actually said. `node_retry.install` wraps this
        # same attribute, which is why the readers' listing is retried too.
        assert (method, requrl) == ("GET", f"/applications/{APP_ID}/boxes"), (
            f"unexpected request {method} {requrl}"
        )
        params = dict(params or {})
        self.listings.append(params)
        self.counts["boxes"] += 1
        limit = params["limit"]  # a listing without a page size is the legacy read
        assert isinstance(limit, int) and limit > 0, f"limit={limit!r}"
        names = sorted(b"u" + i.to_bytes(8, "big") for i in self.chain.upkeeps)
        if "next" in params:
            kind, _, encoded = params["next"].partition(":")
            assert kind == "b64", f"a next-token is a box name in b64: form, not {params['next']!r}"
            after = base64.b64decode(encoded)
            names = [name for name in names if name > after]  # the cursor is exclusive
        take = min(limit, self.page_size or limit)
        page: dict = {
            "boxes": [{"name": base64.b64encode(name).decode()} for name in names[:take]],
            "round": self.chain.round,
        }
        if len(names) > take:
            page["next-token"] = "b64:" + base64.b64encode(names[take - 1]).decode()
        return page

    def application_box_by_name(self, application_id: int, box_name: bytes, **kwargs):
        from algosdk import error

        self.counts["box_read"] += 1
        upkeep_id = int.from_bytes(box_name[1:9], "big")
        if upkeep_id in self.refusing:
            raise error.AlgodHTTPError("HTTP Error 403: Forbidden", 403)
        if upkeep_id in self.vanished or upkeep_id not in self.chain.upkeeps:
            raise error.AlgodHTTPError("box not found", 404)
        if upkeep_id in self.malformed:
            return {"value": base64.b64encode(b"not an upkeep").decode()}
        return {"value": base64.b64encode(self.chain.box(upkeep_id)).decode()}

    def account_info(self, address: str, exclude=None, **kwargs):
        self.counts["account"] += 1
        return {"amount": 5_000_000, "min-balance": 100_000, "assets": []}

    @property
    def requests(self) -> int:
        return sum(self.counts.values())


# The 33 upkeeps live on TestNet app 769891898 at round 66,894,910, read
# read-only on 2026-09-01: (id, interval, rounds until due, escrow, base fee).
# Thirteen of them hold less than one fee and have been overdue for 94,000
# rounds, which is the case the starved recheck exists for; nothing here is
# invented, and `scripts/testnet_snapshot.py` reproduces it.
LIVE_REGISTRY = [
    (19, 15_428, 6_095, 312_578, 4_000),
    (20, 15_428, 6_098, 312_578, 4_000),
    (21, 15_428, 6_101, 357_380, 4_000),
    (22, 15_428, 6_104, 312_578, 4_000),
    (81, 1_286, 164, 3_650_000, 10_000),
    (82, 1_286, 418, 6_982_530, 10_000),
    (84, 1_286, 24, 6_986_891, 10_000),
    (85, 1_286, 33, 4_962_326, 10_000),
    (86, 1_286, 39, 4_986_688, 10_000),
    (89, 1_286, 239, 2_812_000, 4_000),
    (91, 1_286, 0, 2_824_000, 4_000),
    (92, 1_286, 0, 2_812_000, 4_000),
    (93, 1_286, 0, 6_981_301, 10_000),
    (94, 1_286, 0, 7_002_212, 10_000),
    (98, 20, 0, 0, 4_000),
    (99, 20, 0, 0, 4_000),
    (100, 20, 0, 0, 4_000),
    (101, 20, 0, 0, 4_000),
    (102, 20, 0, 0, 4_000),
    (103, 20, 0, 0, 4_000),
    (104, 20, 0, 0, 4_000),
    (105, 20, 0, 0, 4_000),
    (106, 20, 0, 0, 4_000),
    (107, 20, 0, 2_000, 4_000),
    (108, 20, 0, 0, 4_000),
    (109, 20, 0, 0, 4_000),
    (110, 224_000, 159_338, 500_000, 4_000),
    (111, 30_857, 2_060, 496_000, 4_000),
    (112, 1_700, 127, 364_000, 4_000),
    (113, 1_286, 0, 0, 4_000),
    (114, 7_200, 1_434, 472_000, 4_000),
    (115, 1_700, 0, 404_000, 4_000),
    (116, 30_857, 17_913, 500_000, 4_000),
]

#: The window §5 counted over, so the two figures are comparable.
MEASURED_ROUNDS = 63_013
MEASURED_DAYS = 1.97
#: A target refusing the way a real one does: algod attributes it to the inner
#: transaction, and no assert message reaches the chain.
REFUSAL = "inner tx 0 failed: logic eval error: assert failed pc=249"
#: What that window cost before this change, from §5: 11,543 scans at 36
#: requests each plus one account read per twenty scans.
OLD_REQUESTS = 416_125
APP_ID = 769891898


def live_chain(start_round: int = 66_894_910) -> Chain:
    return Chain(
        start_round,
        {
            upkeep_id: {
                "interval": interval,
                "next": start_round + due_in,
                "balance": balance,
                "fee": fee,
                "serviced": start_round + due_in - interval,
            }
            for upkeep_id, interval, due_in, balance, fee in LIVE_REGISTRY
        },
    )


def settle(
    algod: CountingAlgod, registry: Registry, backoff, current: int, refusing: set[int] = frozenset()
) -> None:
    """Refresh, then take everything that is due, as one turn of the loop does.

    Several of the 33 live upkeeps are due at the snapshot round, so a test
    that asserts about a *quiet* registry has to do the work first. Leaving
    them undone would assert against a keeper that is behind, which is not the
    state this cache is about.
    """
    for upkeep in partition_due(
        registry.refresh(algod, APP_ID, current, backoff),
        current,
        lambda upkeep_id: backoff.blocked(upkeep_id, current),
    )[0]:
        if upkeep.upkeep_id in refusing:
            backoff.record_failure(
                upkeep.upkeep_id, REFUSAL, current, upkeep.interval_rounds, False
            )
            continue
        registry.remember_execution(
            upkeep.upkeep_id, algod.chain.execute(upkeep.upkeep_id), current
        )


def run_the_loop(algod: CountingAlgod, rounds: int, monkeypatch) -> dict:
    """The read half of `main`'s loop, over `rounds` rounds of the live registry.

    Everything that costs a request is here — the clock, the box listing, the
    box reads, the heartbeat's account read — and it is driven through the same
    `Registry`, `partition_due` and `wait_for_work` the bot runs. What is left
    out is the signing, so the two requests an execution costs (the simulate
    inside `_resolve_execute_references` and the send) are added by hand where
    they happen. §5's 416,125 excluded execution traffic on both sides of the
    comparison, and it has not changed, so the split is reported rather than
    folded in.
    """
    chain = algod.chain
    # A local sleep costs no requests and takes no time here; it advances the
    # chain by what it would have advanced by in the real world.
    seconds_per_round = 2.752
    monkeypatch.setattr(
        keeper_bot,
        "sleep_until",
        lambda seconds, stop=None: setattr(
            chain, "round", chain.round + max(1, round(seconds / seconds_per_round))
        ),
    )
    backoff = Backoff(None)
    registry = Registry()
    stop_at = chain.round + rounds
    scans = executions = 0
    last_heartbeat_round = 0
    while chain.round < stop_at:
        current = chain.round
        upkeeps = registry.refresh(algod, APP_ID, current, backoff)
        due, _held = partition_due(
            upkeeps, current, lambda upkeep_id: backoff.blocked(upkeep_id, current)
        )
        scans += 1
        for upkeep in due:
            # Not a client count: the execute path goes through algokit-utils'
            # composer, which this harness does not drive. Measured instead,
            # from a real `--once` against LocalNet on 2026-09-01 with 17 due
            # and one that went through: 103 requests, of which 17 simulates,
            # 21 suggested-params, one send and three pending-transaction
            # polls. That is about eight for an execution that lands.
            #
            # It was 2 until Fable 5.1 pointed out that a hand-added constant
            # is not a measurement and that the number this test exists to
            # produce was therefore a fifth of the truth on its execution
            # half. `reading` below is the half that really is counted at the
            # client, and is the figure to quote when only one is wanted.
            algod.counts["execute"] += REQUESTS_PER_EXECUTION
            registry.remember_execution(
                upkeep.upkeep_id, chain.execute(upkeep.upkeep_id), current
            )
            executions += 1
        if scans % HEARTBEAT_SCANS == 0 or current - last_heartbeat_round >= HEARTBEAT_ROUNDS:
            last_heartbeat_round = current
            keeper_bot.account_state(algod, "KEEPER")
        wait_for_work(
            algod, current, registry.next_wake_round(current, backoff), seconds_per_round
        )
        assert chain.round > current, "the loop must always make progress"
    return {
        "scans": scans,
        "executions": executions,
        "requests": algod.requests,
        "reading": algod.requests - algod.counts["execute"],
    }


REQUESTS_PER_EXECUTION = 8


class TestWhatOneDayCosts:
    """The number, measured the way the old one was.

    §5 counted a status, the box listing, a read of every box and the block
    wait, plus an account read per twenty scans: 416,125 requests over 63,013
    rounds, **about 211,000 a day**. The same four things are counted here,
    over the same window, against the registry as it actually stood on
    2026-09-01.
    """

    def test_the_live_registry_over_the_same_window(self, monkeypatch) -> None:
        algod = CountingAlgod(live_chain())
        result = run_the_loop(algod, MEASURED_ROUNDS, monkeypatch)

        per_day = result["requests"] / MEASURED_DAYS
        reading_per_day = result["reading"] / MEASURED_DAYS
        detail = (
            f"{result['requests']:,} requests over {MEASURED_ROUNDS:,} rounds "
            f"= {per_day:,.0f} a day, from {result['scans']:,} scans and "
            f"{result['executions']:,} executions "
            f"({dict(algod.counts)})"
        )
        # Measured on 2026-09-01, over the live registry and §5's own window:
        # about 9,500 requests in 63,013 rounds, of which 4,713 are reading it
        # and the rest are the 594 executions at the measured eight apiece.
        # That is **about 4,800 a day, 2,400 of them reading**, against
        # 211,000: a fortieth, not the seventieth this test claimed while it
        # was hand-adding two per execution. Bounded rather than pinned to the
        # digit, because the arithmetic moves with any of the constants above;
        # bounded tightly enough that putting the traffic back has to come and
        # edit this line.
        assert 2_000 <= reading_per_day <= 2_800, detail
        assert 4_200 <= per_day <= 5_400, detail
        # The claim, as an assertion: a different order of magnitude, not a trim.
        assert result["requests"] * 30 < OLD_REQUESTS, detail

    def test_a_scan_no_longer_reads_every_box(self) -> None:
        """The heart of it: 33 boxes read once, then only what could matter."""
        algod = CountingAlgod(live_chain())
        backoff, registry = Backoff(None), Registry()
        current = algod.chain.round

        registry.refresh(algod, APP_ID, current, backoff)
        assert algod.counts["box_read"] == 33, "the first scan has no cache to spare it"

        settle(algod, registry, backoff, current)
        # Nothing else can happen in the next round, so the next scan reads no
        # boxes at all: thirteen of the due upkeeps are starved and only a
        # top-up changes that, and the rest are not due for hundreds of rounds.
        algod.counts.clear()
        registry.refresh(algod, APP_ID, current + 1, backoff)
        assert algod.counts["box_read"] == 0
        assert algod.counts["boxes"] == 1, "the listing is still read every scan"

    def test_what_one_permanently_refusing_target_costs(self, monkeypatch) -> None:
        """The price of the short retry in `keeper_backoff`, measured.

        A target that refuses for ever is retried at
        TARGET_REFUSAL_BACKOFF_ROUNDS, and each retry is a wake: the clock, the
        listing, a box read and the simulate that refuses. Nothing is
        broadcast, which is why it is four requests and not more — the whole
        argument for a three-minute ceiling instead of an hour rests on that
        number being small enough that the two halves of this branch are not
        pulling against each other.

        Measured as a difference between the same day run twice, because the
        registry costs a few thousand requests a day on its own and an
        absolute figure would be mostly that.
        """
        def a_day(refusing: set[int]) -> tuple[int, int]:
            algod = CountingAlgod(live_chain())
            seconds_per_round = 2.752
            monkeypatch.setattr(
                keeper_bot,
                "sleep_until",
                lambda seconds, stop=None: setattr(
                    algod.chain,
                    "round",
                    algod.chain.round + max(1, round(seconds / seconds_per_round)),
                ),
            )
            backoff, registry = Backoff(None), Registry()
            stop_at = algod.chain.round + 30_857  # a day at 2.8 s/round
            attempts = 0
            while algod.chain.round < stop_at:
                current = algod.chain.round
                upkeeps = registry.refresh(algod, APP_ID, current, backoff)
                due, _ = partition_due(
                    upkeeps, current, lambda upkeep_id: backoff.blocked(upkeep_id, current)
                )
                for upkeep in due:
                    if upkeep.upkeep_id in refusing:
                        algod.counts["execute"] += 1  # the simulate; nothing is sent
                        backoff.record_failure(
                            upkeep.upkeep_id, REFUSAL, current, upkeep.interval_rounds, False
                        )
                        attempts += 1
                    else:
                        algod.counts["execute"] += 2
                        registry.remember_execution(
                            upkeep.upkeep_id, algod.chain.execute(upkeep.upkeep_id), current
                        )
                wait_for_work(
                    algod,
                    current,
                    registry.next_wake_round(current, backoff),
                    seconds_per_round,
                )
            return algod.requests, attempts

        # Upkeep 89 runs hourly and would otherwise be executed 24 times.
        clean, _ = a_day(set())
        refusing, attempts = a_day({89})

        assert attempts > 400, f"only {attempts} retries in a day of rounds"
        per_retry = (refusing - clean) / attempts
        assert per_retry < 5, (
            f"{refusing - clean} extra requests over {attempts} retries "
            f"= {per_retry:.1f} each, against a clean day of {clean:,}"
        )
        # And it is a slice of the day rather than the day: the old schedule
        # bought its 48 retries with an hour of not looking.
        assert refusing < clean * 2


class TestNothingDueIsMissed:
    """Correctness first: the cache may be early, and must never be late."""

    def test_an_upkeep_is_read_again_on_the_round_it_falls_due(self) -> None:
        algod = CountingAlgod(live_chain())
        backoff, registry = Backoff(None), Registry()
        start = algod.chain.round
        settle(algod, registry, backoff, start)

        # Upkeep 84 is due in 24 rounds and is the soonest in the registry.
        assert registry.next_wake_round(start, backoff) == start + 24

        algod.counts.clear()
        upkeeps = registry.refresh(algod, APP_ID, start + 24, backoff)
        assert algod.counts["box_read"] == 1, "only the one that came due"
        due, _ = partition_due(upkeeps, start + 24, None)
        assert [u.upkeep_id for u in due] == [84]

    def test_a_new_registration_is_read_the_scan_it_appears(self) -> None:
        algod = CountingAlgod(live_chain())
        backoff, registry = Backoff(None), Registry()
        current = algod.chain.round
        settle(algod, registry, backoff, current)

        algod.chain.upkeeps[200] = {
            "interval": 10, "next": current + 10, "balance": 100_000, "fee": 4_000,
        }
        algod.counts.clear()
        upkeeps = registry.refresh(algod, APP_ID, current, backoff)

        assert algod.counts["box_read"] == 1
        assert 200 in {u.upkeep_id for u in upkeeps}
        # And the loop wakes for it rather than sleeping past its first cycle.
        assert registry.next_wake_round(current, backoff) == current + 10

    def test_a_cancelled_upkeep_leaves_the_cache_with_its_box(self) -> None:
        algod = CountingAlgod(live_chain())
        backoff, registry = Backoff(None), Registry()
        current = algod.chain.round
        registry.refresh(algod, APP_ID, current, backoff)

        del algod.chain.upkeeps[84]
        upkeeps = registry.refresh(algod, APP_ID, current, backoff)

        assert 84 not in {u.upkeep_id for u in upkeeps}
        assert len(upkeeps) == len(LIVE_REGISTRY) - 1

    def test_a_top_up_that_revives_a_starved_upkeep_is_noticed(self) -> None:
        """The one thing caching a due upkeep could hide.

        Thirteen live upkeeps are due and hold less than one fee. Nothing but a
        `top_up` changes that, and a top-up is somebody with a wallet rather
        than a race, so the recheck is an hour rather than a round — but it has
        to happen, or a funded upkeep would sit unserviced for ever.
        """
        algod = CountingAlgod(live_chain())
        backoff, registry = Backoff(None), Registry()
        start = algod.chain.round
        registry.refresh(algod, APP_ID, start, backoff)

        algod.chain.upkeeps[98]["balance"] = 400_000  # a top-up lands

        # Not seen immediately, which is the trade this makes on purpose.
        upkeeps = registry.refresh(algod, APP_ID, start + 1, backoff)
        assert 98 not in {u.upkeep_id for u in select_due(upkeeps, start + 1)}

        upkeeps = registry.refresh(algod, APP_ID, start + STARVED_RECHECK_ROUNDS, backoff)
        assert 98 in {u.upkeep_id for u in select_due(upkeeps, start + STARVED_RECHECK_ROUNDS)}

    def test_nothing_is_trusted_for_longer_than_a_day(self) -> None:
        """Upkeep 110 is not due for 159,338 rounds; its box is still re-read."""
        algod = CountingAlgod(live_chain())
        backoff, registry = Backoff(None), Registry()
        start = algod.chain.round
        registry.refresh(algod, APP_ID, start, backoff)

        algod.counts.clear()
        registry.refresh(algod, APP_ID, start + MAX_CACHE_ROUNDS, backoff)
        assert algod.counts["box_read"] == len(LIVE_REGISTRY)

    def test_backoff_holds_a_box_shut_and_then_opens_it(self) -> None:
        """A blocked upkeep is not going to be attempted, so its bytes cannot
        change a decision — and the round it reopens on is a round the loop has
        to be awake for. The cache and the clock read the same number."""
        algod = CountingAlgod(live_chain())
        backoff, registry = Backoff(None), Registry()
        start = algod.chain.round
        # 91 is one of the five upkeeps due at the snapshot round; its target
        # refuses rather than being executed, and the rest are settled.
        settle(algod, registry, backoff, start, refusing={91})
        entry = backoff.entry(91)
        assert entry is not None and entry.next_attempt_round == start + 1

        algod.counts.clear()
        registry.refresh(algod, APP_ID, start, backoff)
        assert algod.counts["box_read"] == 0, "not while it is held back"
        assert registry.next_wake_round(start, backoff) == start + 1

        registry.refresh(algod, APP_ID, start + 1, backoff)
        assert algod.counts["box_read"] == 1


class TestTheListingIsPaged:
    """#250, F05: every page of the box listing is asked for, the first included.

    The live registry is 33 boxes, so nothing here has ever needed a second
    page, and that is the problem: a pager whose first page comes from a
    method that never returns a token is a pager that has never paged. These
    run the readers over more boxes than a page holds and read what the fake
    node was actually asked.
    """

    def test_the_first_request_carries_the_page_size_and_no_token(self) -> None:
        algod = CountingAlgod(live_chain())
        Registry().refresh(algod, APP_ID, algod.chain.round, Backoff(None))
        assert algod.listings == [{"limit": BOX_PAGE_LIMIT}]

    def test_the_bot_reads_a_registry_larger_than_a_page(self, monkeypatch) -> None:
        monkeypatch.setattr(keeper_bot, "BOX_PAGE_LIMIT", 10)
        algod = CountingAlgod(live_chain())
        registry = Registry()

        upkeeps = registry.refresh(algod, APP_ID, algod.chain.round, Backoff(None))

        assert sorted(u.upkeep_id for u in upkeeps) == sorted(u[0] for u in LIVE_REGISTRY)
        assert len(upkeeps) == 33 and algod.counts["boxes"] == 4, dict(algod.counts)
        assert [p["limit"] for p in algod.listings] == [10, 10, 10, 10]
        assert "next" not in algod.listings[0]
        assert all("next" in p for p in algod.listings[1:]), algod.listings

    def test_scan_upkeeps_reads_a_registry_larger_than_a_page(self, monkeypatch) -> None:
        """The reader `health`, the notifier, the top-up planner and the
        preview all share; a short read here is a solvency check that lies."""
        monkeypatch.setattr(keeper_bot, "BOX_PAGE_LIMIT", 10)
        algod = CountingAlgod(live_chain())

        upkeeps = scan_upkeeps(algod, APP_ID)

        assert sorted(u.upkeep_id for u in upkeeps) == sorted(u[0] for u in LIVE_REGISTRY)
        assert algod.counts["boxes"] == 4 and algod.counts["box_read"] == 33
        assert "next" not in algod.listings[0]
        assert all("next" in p for p in algod.listings[1:])

    def test_a_short_page_with_a_token_is_not_the_end(self) -> None:
        """The server's byte cap can return fewer than `limit` while more
        remain; the spec says the token is the only reliable signal, so a
        reader that stopped at a page shorter than it asked for would be
        wrong. Here every page is five names against a limit of a thousand."""
        algod = CountingAlgod(live_chain(), page_size=5)
        assert len(scan_upkeeps(algod, APP_ID)) == 33
        assert algod.counts["boxes"] == 7  # ceil(33 / 5)

    def test_what_the_listing_request_looks_like_on_the_wire(self, monkeypatch) -> None:
        """Through the real client down to `urlopen`, so the URL a node would
        receive is what is asserted: the versioned path, the page size, and
        the continuation token with its `b64:` colon percent-encoded and
        decoding back to exactly the token the previous page handed out."""
        import io
        from urllib.parse import parse_qs, urlsplit

        from algosdk.v2client import algod as algod_module

        sent: list[str] = []

        def urlopen(request, timeout=None):
            sent.append(request.full_url)
            return io.BytesIO(b'{"boxes": [], "round": 1}')

        monkeypatch.setattr(algod_module, "urlopen", urlopen)
        client = AlgodClient("", "http://node.test")
        token = "b64:" + base64.b64encode(b"u" + (84).to_bytes(8, "big")).decode()
        assert token == "b64:dQAAAAAAAABU"

        keeper_bot._box_page(client, APP_ID, token)

        assert sent == [
            f"http://node.test/v2/applications/{APP_ID}/boxes?limit=1000&next=b64%3AdQAAAAAAAABU"
        ]
        assert parse_qs(urlsplit(sent[0]).query) == {"limit": ["1000"], "next": [token]}

    def test_a_node_that_ignores_the_page_size_is_refused(self) -> None:
        """An algod older than 4.7.0 does not reject `limit`; it ignores it
        and answers in legacy mode, without a `round`. That listing fails
        outright once the app outgrows the node's cap, so it is refused with
        the version to upgrade to rather than read as if it could page."""

        class LegacyAlgod(CountingAlgod):
            def algod_request(self, method, requrl, params=None, **kwargs):
                page = super().algod_request(method, requrl, params, **kwargs)
                del page["round"]
                return page

        algod = LegacyAlgod(live_chain())
        with pytest.raises(UnrecoverableError, match=BOX_PAGINATION_SINCE):
            scan_upkeeps(algod, APP_ID)


class TestACancelledBoxDoesNotAbortTheScan:
    """#250, F04: a box that vanishes between the listing and its read.

    `cancel` deletes the box, and a scan is a listing followed by a read per
    name, so a creator cancelling while a scan is in flight hands the reader a
    name that 404s. Until 2026-09-08 that 404 propagated: the bot logged
    `scan_failed` and backed off the whole registry, and `health` and the
    notifier died before saying anything. The 404 is the one error forgiven,
    and only on the read of a box that was just listed; a node refusing to
    answer, or a box that does not decode, still fails the scan, because a
    reader that took an outage for a wave of cancellations would be the worse
    bug.
    """

    def test_scan_upkeeps_leaves_the_vanished_box_out(self) -> None:
        algod = CountingAlgod(live_chain())
        algod.vanished = {84}

        upkeeps = scan_upkeeps(algod, APP_ID)

        assert 84 not in {u.upkeep_id for u in upkeeps}
        assert len(upkeeps) == len(LIVE_REGISTRY) - 1
        assert algod.counts["box_read"] == 33, "the read was attempted, and answered 404"

    def test_the_registry_drops_the_vanished_box_and_keeps_the_rest(self) -> None:
        algod = CountingAlgod(live_chain())
        backoff, registry = Backoff(None), Registry()
        start = algod.chain.round
        settle(algod, registry, backoff, start)
        assert 84 in registry.cached

        # Upkeep 84 comes due 24 rounds on, which is what makes the bot read
        # it again; by then its creator has cancelled it, and the listing the
        # bot took a moment earlier still names it.
        algod.vanished = {84}
        upkeeps = registry.refresh(algod, APP_ID, start + 24, backoff)

        assert 84 not in {u.upkeep_id for u in upkeeps}
        assert 84 not in registry.cached, "the stale copy has to go with the box"
        assert len(upkeeps) == len(LIVE_REGISTRY) - 1

    def test_a_refusal_on_a_read_still_fails_the_scan(self) -> None:
        """The 403 that free TestNet nodes shed under quota: not a cancellation."""
        algod = CountingAlgod(live_chain())
        algod.refusing = {84}
        with pytest.raises(Exception, match="403"):
            scan_upkeeps(algod, APP_ID)
        with pytest.raises(Exception, match="403"):
            Registry().refresh(algod, APP_ID, algod.chain.round, Backoff(None))

    def test_a_box_that_does_not_decode_still_fails_the_scan(self) -> None:
        """Malformed contract state is a different failure from a race, and
        skipping it would hide a box the decoder does not understand."""
        algod = CountingAlgod(live_chain())
        algod.malformed = {84}
        with pytest.raises(ValueError, match="upkeep 84"):
            scan_upkeeps(algod, APP_ID)
        with pytest.raises(ValueError, match="upkeep 84"):
            Registry().refresh(algod, APP_ID, algod.chain.round, Backoff(None))
