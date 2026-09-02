"""Surviving a public node that refuses to answer.

`fledge run health` died on two of three consecutive runs on 2026-09-01 with
`algosdk.error.AlgodHTTPError: HTTP Error 403: Forbidden`, thrown before the
report reached any of its own logic. Both obvious readings were wrong: the
node was not down, and we were not sending too fast. A hand-typed `curl` for
the same box listing succeeded, and so did 41 of 45 consecutive `/v2/status`
calls sent as fast as a shell could send them.

What the endpoint actually says, in headers and a body the SDK throws away:

    x-and-quota: block=1;reqs=230824;bytes=154489173;ts=2026-09-01
    Daily free API quota exceeded. / 230824 requests / 0.15 GB

The measurement that makes retrying the right answer rather than a hopeful
one: once `block=1` is set the edge does not refuse everything, it sheds a
fraction. Measured against a single edge (`x-and-nl: us4@us_losangeles`) whose
quota counter did not move during the sample, so which request gets shed is
the only variable: 4 refusals in 45 requests, about 9%. One request is very
likely to succeed, and a run of 40 is very likely to contain a failure —
1 - 0.91**40 is 98%, which is exactly why two runs in three died while a
one-off `curl` never did.

So this is not waiting for a window to reopen. It is asking to be sampled
again. `MAX_ATTEMPTS` is 5, so four retries follow the first try, and the odds
of all five being shed are 0.09**5: about 1 in 169,000.


Whose quota this is
-------------------

An earlier version of this docstring called the counter above "Nodely's
free-tier allowance for the endpoint as a whole, shared by everyone using it",
and concluded that "there is nothing here to fix by sending fewer". That was
asserted rather than established, and measuring it turned it around.

This repository's own keeper runs on the machine that ran the report, as the
launchd agent `scripts/keeper_daemon.py` installs
(`xyz.corvidlabs.arcron.keeper.testnet`), and `.env.testnet` points it at the
same `testnet-api.algonode.cloud` from the same address. Counted out of its own
log at `~/Library/Logs/arcron/keeper-testnet.log`: 11,543 `scan` events across
63,013 rounds, so a 2.70 s round and a scan every 5.46 of them. One scan of the
live 33-upkeep registry is 36 requests — `status`, the box listing, 33 box
reads, and the `status_after_block` long poll. `account_info` is not among
them: it runs on the heartbeat, one scan in `HEARTBEAT_SCANS` = 20, which an
earlier count here folded into every scan. That is 416,125 requests in 1.97
days, or **about 211,000 a day from this one bot**.
The counter that was blocked stood at 230,824. Both of those are measured, and
they are the same number to within 9%: one keeper of ours accounts for
substantially all of the traffic the endpoint refused us over.

And that is the figure the refusals have already throttled us down to. 96%
of those scans are 2 to 4 rounds apart, a mean of 2.48 rounds or 6.70 s, which
unrefused is 5.53 requests a second, or 477,403 a day. The 1,964 `scan_failed`
403s in the same log, each followed by a doubling sleep, are the only reason
the number is as low as it is. (200,000 a day is what this file has always
cited for the free tier. It is the one figure here nobody has checked against
Nodely, so the argument above is built on the counter instead, which we read
off our own responses.)

Nor is the counter the world's. Sampled twice 45 seconds apart on 2026-09-01,
`reqs` went 233,586 to 233,636 — 50 requests in 45.4 s, about one a second. An
allowance shared by everyone using a public Algorand endpoint would not climb
at one request a second.

What stays open is what the bucket is keyed to: our address, the absent token,
something else. Nobody here has read Nodely's side of it. The direction is not
open, and it is the opposite of what this file used to say. Sending fewer is
exactly the fix, and it is `scripts/keeper_bot.py` that owns it rather than
this module: a scan re-read all 33 boxes every 2.5 rounds to notice that a
handful were due. Retrying keeps a report alive in the meantime; it is not a
reason the traffic is fine.

**Done, and here is the new figure.** `keeper_bot.Registry` re-reads a box on
the round its cached copy stops being able to change a decision, and the loop
sleeps until the soonest of those rounds rather than scanning every couple of
them. Counted the same way, over the same 63,013 rounds, against the same live
registry: **about 9,500 requests, some 4,800 a day**, of which 2,400 are
reading the registry and the rest are the 594 executions at a measured eight
apiece. That is a fortieth of the 211,000 above.

Only the reading half is a client count. The execution half goes through
algokit-utils' composer, which that harness does not drive, so it is a
constant measured from a real `--once` on LocalNet and multiplied. It read
"about 3,000 a day, a seventieth" until Fable 5.1 observed that the constant
was 2 rather than the 8 an execution actually costs.
`tests/test_keeper_bot.py::TestWhatOneDayCosts` is the measurement and
`TestNothingDueIsMissed` is the argument that none of it is bought by missing
work.

One correction to the accounting above, in the unhelpful direction: the loop
also read `account_info` on **every** scan, for the bonus assets the keeper was
opted in to, which this paragraph placed on the heartbeat. So one scan was 37
requests rather than 36 and the 211,000 is a floor. Both reads are one
`account_info` on the heartbeat now (`keeper_bot.account_state`).


What is retried
---------------

Two statuses are retried whatever was asked:

    429  the per-second or concurrency limit, per Nodely's published policy
    403  the daily request or byte quota, which is what we hit

Both are refused by the CDN in front of the node rather than by algod. That is
why the body never reaches the exception: it is a plain sentence rather than
algod's JSON, so algosdk's `json.loads` fails, the message falls back to the
bare `HTTP Error 403: Forbidden` in the traceback above, and the status code
is the only thing left to match on.

5xx is retried for reads only. A 502 or 503 from the edge kills a `health` run
exactly as a 403 does, and asking a GET again cannot change anything whatever
produced the error, so refusing to ask bought nothing. A POST is a different
matter — not because replaying one is unsafe, since the argument below holds
for any status, but because a 5xx says the node behind the edge is in trouble
rather than the edge shedding, and the recovery below needs algod well enough
to answer "already in ledger". We have measured nothing about that: 47 hours of
keeper log hold 2,067 refusals and every one is a 403. Until there is something
to measure, the one call that spends money hands a 5xx to its caller, which for
a keeper costs a round rather than a fee.


Why replaying a POST is safe
----------------------------

The first version of this argued that the `x-and-quota` header and the
plain-text body prove the request never reached the node, so there was nothing
to submit twice. They prove no such thing. They prove the *response* was
written at the edge, which says nothing about whether the edge had already
forwarded the request, and a safety argument that rests on where a refusal was
generated is an argument we cannot check.

The real reason is better and does not depend on that. The only POST any of
this makes is `send_raw_transaction`, which sends an already-signed blob, and
an Algorand transaction id is a hash of that blob. `retrying` replays the same
bytes rather than re-signing, so a replay carries the same id, and a chain that
has already seen that id answers "already in ledger" instead of paying
anything a second time. Nor could a replay collide with somebody else's work:
the id hashes a signature only our key can produce.

Which leaves the case the old argument never had to face. If the *first* copy
landed, the replay comes back as a 400 in algod's own words, and a caller
reading that as a failure would be wrong in a specific and expensive-sounding
way. `keeper_bot` would ask the registry whether it had moved on, find that it
had — we moved it — and emit `race_lost`, reporting a keeper as having lost a
race against itself. Nothing is lost but the truth; the fee was paid once and
collected once. So the duplicate is not passed on. When, and only when, this
wrapper is the thing that replayed the request, the duplicate is recognised and
answered with the id algod would have returned, derived from the blob we sent
by the same hash the paragraph above rests on. The caller then waits on its own
transaction and watches it confirm, which is what actually happened.

Nodely's policy says failed requests still count towards the quota, so
retrying does add to a number this repository is itself blowing. Four extra
requests against 219,564 a day is not the part worth arguing about; the scan
rate is.

Installed once, on the clients `network.connect` hands out, so every script
that talks to a public node gets it without anyone remembering to ask.
"""

import base64
import functools
import logging
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

#: The per-second or concurrency limit: the node is asking us to slow down.
RATE_LIMITED = 429
#: The daily request or byte quota: the endpoint is over its allowance. Slowing
#: down is very likely the actual fix — see "Whose quota this is" above — but
#: it is a fix for the keeper's scan rate, not something a wrapper can do to a
#: request already in flight.
QUOTA_EXCEEDED = 403
#: The statuses worth asking again about whatever the request was, because the
#: edge declined to pass it on rather than the node answering it.
RETRYABLE_STATUSES = (RATE_LIMITED, QUOTA_EXCEEDED)

#: A 5xx is retried too, but only for a read. See the module docstring: the
#: replay is safe either way, and what is missing is any measurement of how
#: this endpoint behaves when the node rather than the edge is unwell.
SERVER_ERRORS = range(500, 600)
#: Verbs for which asking again cannot change anything, so a 5xx costs a wait
#: and nothing else. POST is absent on purpose and is the whole point of the
#: set existing.
IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

#: Attempts in total, so four retries after the first try.
MAX_ATTEMPTS = 5
#: The first wait, doubling from there. Short on purpose: the refusal is a
#: sampling decision rather than a closed window, so waiting longer buys
#: nothing that asking again does not.
FIRST_WAIT_SECONDS = 0.5
#: Whatever the schedule or a `Retry-After` asks for, never sit this long on a
#: single attempt. Five attempts then cost at most about eight seconds, which
#: a report a human is waiting on can afford and a keeper's scan can too.
MAX_WAIT_SECONDS = 4.0

#: What the endpoint writes in the body when the daily allowance is gone.
#: `IndexerHTTPError` is the reason this exists: it is a bare `Exception`
#: carrying the response body and no status at all, so an indexer refusal has
#: to be recognised by its words. Verified against a real one on
#: testnet-idx.algonode.cloud, which returned "Daily free API quota exceeded."
#: with nothing in it resembling a status code.
QUOTA_BODY_MARKERS = ("quota exceeded", "daily free api")
#: And what a per-second refusal reads as when it arrives the same way.
RATE_LIMIT_BODY_MARKERS = ("too many requests", "rate limit")

#: What algod says when it is handed a transaction it already has. Both
#: wordings mean the same thing to us — the blob we sent is already on its way
#: to a block — and which one comes back depends on whether the first copy is
#: still in the pool or already committed. Matched on the words rather than the
#: 400, because a plain 400 is also what a target's logic error looks like and
#: that one must keep reaching the caller untouched.
DUPLICATE_BODY_MARKERS = (
    "already in ledger",
    "already in the pool",
    "already in pool",
)


def status_of(error: BaseException) -> int | None:
    """The HTTP status behind an SDK error, or None when it did not carry one.

    `AlgodHTTPError` records the code and is the case that matters, because
    the algod client is what every box read goes through. Anything else has to
    fall back to `is_refusal`'s reading of the text.
    """
    code = getattr(error, "code", None)
    return code if isinstance(code, int) else None


def is_refusal(error: BaseException) -> bool:
    """True when the edge declined to pass a request on, rather than answering.

    Deliberately narrow. A 404 from a box that is genuinely gone has to keep
    reaching `keeper_bot.read_upkeep`, which reads it as a cancelled upkeep,
    and a logic error has to keep reaching the caller as a logic error. This
    recognises the node saying "not now", never the node saying "no".
    """
    status = status_of(error)
    if status is not None:
        return status in RETRYABLE_STATUSES
    lowered = str(error).lower()
    return any(
        marker in lowered
        for marker in QUOTA_BODY_MARKERS + RATE_LIMIT_BODY_MARKERS
    )


def is_duplicate_submission(error: BaseException) -> bool:
    """True when algod is saying it already holds the transaction we sent.

    Recognised by its words rather than its status, because the status is a
    bare 400 and so is a target's logic error. The words carry the whole claim:
    algod has this exact transaction id, and an id is a hash of the signed
    blob, so it can only have got it from us.
    """
    lowered = str(error).lower()
    return any(marker in lowered for marker in DUPLICATE_BODY_MARKERS)


def request_method(*args: Any, **kwargs: Any) -> str:
    """The verb of a funnelled request, upper-cased, or "" when there is none.

    Same argument shape `describe_request` reads: both algosdk funnels take
    (method, requrl) first. A caller wrapping something that is not a funnel
    gets "", which reads as "not known to be idempotent" and is the safe way
    round.
    """
    method = args[0] if args else kwargs.get("method", "")
    return method.upper() if isinstance(method, str) else ""


def should_retry(error: BaseException, method: str = "") -> bool:
    """Whether this failure is worth asking about again, given what was asked.

    Two rules, and the second is why the method has to be here at all: an edge
    refusal is retried for anything, and a server error only for a read. The
    module docstring has the reasoning; the short version is that a 5xx is the
    node in trouble rather than the edge shedding, and the one request that
    spends money should not be replayed into that on a guess.
    """
    if is_refusal(error):
        return True
    status = status_of(error)
    if status is None or status not in SERVER_ERRORS:
        return False
    return method.upper() in IDEMPOTENT_METHODS


def retry_after_seconds(error: BaseException) -> float | None:
    """What the node asked us to wait, when it asked.

    Almost always None in practice: the measured 403s carry no `Retry-After`
    at all, and algosdk discards the response headers before raising anyway,
    so the doubling schedule is what actually decides. It is read regardless
    because honouring a stated wait is cheaper and politer than guessing at
    one, and `notifier.post` already does exactly this with Discord's.
    """
    headers = getattr(error, "headers", None)
    if headers is None:
        return None
    try:
        value = headers.get("Retry-After")
    except Exception:  # something header-shaped that is not a mapping
        return None
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        # `Retry-After` may be an HTTP date instead of a count of seconds. We
        # have never seen one from this endpoint, and a date is not worth a
        # parser here, so fall through to the schedule.
        return None


def wait_before(attempt: int, error: BaseException) -> float:
    """Seconds to wait before retry number `attempt`, counting the first as 1."""
    asked = retry_after_seconds(error)
    if asked is not None:
        return min(asked, MAX_WAIT_SECONDS)
    return min(FIRST_WAIT_SECONDS * 2 ** (attempt - 1), MAX_WAIT_SECONDS)


def describe_request(*args: Any, **kwargs: Any) -> str:
    """`GET /v2/applications/123/boxes`, so a warning names what was refused.

    Both algosdk funnels take (method, requrl) first, positionally in practice
    and by those names when they are passed as keywords.
    """
    method = args[0] if args else kwargs.get("method", "")
    url = args[1] if len(args) > 1 else kwargs.get("requrl", "")
    return f"{method} {url}".strip() or "request"


def is_submission(*args: Any, **kwargs: Any) -> bool:
    """True for the one funnelled call that broadcasts a signed blob.

    `send_raw_transaction` is `POST` to `/transactions`, and it is the only
    request this repository makes that could spend anything. Deliberately
    narrow about the path: `/v2/transactions/params` and
    `/v2/transactions/pending/<id>` share the prefix and are ordinary reads.
    """
    if request_method(*args, **kwargs) != "POST":
        return False
    url = args[1] if len(args) > 1 else kwargs.get("requrl", "")
    if not isinstance(url, str):
        return False
    return url.split("?")[0].rstrip("/").endswith("/transactions")


def submitted_txid(*args: Any, **kwargs: Any) -> str | None:
    """The id algod would have answered with, hashed out of the blob we sent.

    This is the same fact the safety argument rests on, used the other way
    round: an Algorand transaction id is a hash of the signed transaction, so
    the bytes in our own hand are enough to name the transaction algod is
    telling us it already has. Reading the id out of algod's prose instead
    would tie us to a message format we do not control, and the whole point of
    the finding that produced this function is that we had already leaned on
    the wrong unverifiable thing once.

    A submission may be a group, in which case the body is several signed
    transactions concatenated, and algod answers with the first one's id. So
    the first msgpack object is unpacked on its own rather than through
    `msgpack_decode`, which rejects the trailing ones as extra data.

    Returns None rather than raising for anything unexpected: this runs while
    an error is already being handled, and failing to name the transaction has
    to leave that error on its way to the caller, not replace it.
    """
    blob = args[3] if len(args) > 3 else kwargs.get("data")
    if not isinstance(blob, (bytes, bytearray)):
        return None
    try:
        # Imported here rather than at module scope so this module stays
        # stdlib-only to import. Everything that installs it has algosdk, but
        # a test that only wants the schedule should not need one.
        import msgpack
        from algosdk import encoding

        unpacker = msgpack.Unpacker(raw=False, strict_map_key=False)
        unpacker.feed(bytes(blob))
        first = next(unpacker)
        signed = encoding.msgpack_decode(
            base64.b64encode(msgpack.packb(first, use_bin_type=True)).decode()
        )
        return str(signed.get_txid())
    except Exception:
        return None


def retrying(
    function: Callable[..., Any],
    *,
    attempts: int = MAX_ATTEMPTS,
    sleep: Callable[[float], Any] = time.sleep,
    describe: Callable[..., str] | None = None,
) -> Callable[..., Any]:
    """Wrap a callable so a refusal is asked again instead of raised.

    Gives up after `attempts` tries and re-raises the refusal it got last,
    which is the honest outcome: an endpoint blocking every request is a real
    failure, and a helper that hid it behind an unbounded loop would turn a
    five second failure into a report that never returns.
    """
    name = describe or (lambda *a, **k: getattr(function, "__name__", "request"))

    @functools.wraps(function)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        attempt = 0
        # Whether *we* are the reason this request has been sent more than
        # once. It gates the duplicate handling below and nothing else, and it
        # has to: a caller whose own first attempt is told "already in ledger"
        # is being told something true about a transaction it sent earlier, and
        # swallowing that would hide a genuine double submission.
        replayed = False
        while True:
            attempt += 1
            try:
                return function(*args, **kwargs)
            except Exception as error:
                if (
                    replayed
                    and is_submission(*args, **kwargs)
                    and is_duplicate_submission(error)
                ):
                    # Our own first copy reached the chain after all, and the
                    # refusal we retried was written over a request that had
                    # already been forwarded. This is a success wearing a 400:
                    # the transaction is in the pool or in the ledger, the fee
                    # was paid once, and the only thing wrong is which word the
                    # caller would use for it. Left to raise, `keeper_bot`
                    # would ask the registry whether it had moved on, find that
                    # it had, and log `race_lost` against the keeper that
                    # actually won. So answer it the way algod would have.
                    txid = submitted_txid(*args, **kwargs)
                    if txid is not None:
                        logger.warning(
                            f"The node already had {name(*args, **kwargs)} when we "
                            f"replayed it ({error}); the first copy landed as {txid}, "
                            f"so this is that submission's answer and not a failure"
                        )
                        return {"txId": txid}
                    # Nothing decoded, so there is no id to answer with and a
                    # made-up one would be worse than the wrong log line. Fall
                    # through: a duplicate is not retryable, so it raises.
                if attempt >= attempts or not should_retry(
                    error, request_method(*args, **kwargs)
                ):
                    raise
                pause = wait_before(attempt, error)
                logger.warning(
                    f"The node refused {name(*args, **kwargs)} ({error}); "
                    f"attempt {attempt} of {attempts}, retrying in {pause:.1f}s"
                )
                replayed = True
                sleep(pause)

    return wrapper


#: The single method every request from an algosdk client goes through.
#: Wrapping here rather than around `application_boxes`, `account_info` and
#: the rest means an endpoint someone reaches for later is covered without
#: their having to remember, and it covers the calls algokit-utils makes
#: through the same client too.
FUNNELS = ("algod_request", "indexer_request")

#: Marks a client as already wrapped. Installing twice would nest the wrappers
#: rather than replace them, turning five attempts into twenty-five and a four
#: second worst case into over a minute.
_INSTALLED = "_arcron_node_retry_installed"


def install(
    client: Any,
    *,
    attempts: int = MAX_ATTEMPTS,
    sleep: Callable[[float], Any] = time.sleep,
) -> Any:
    """Make every request `client` sends survive a refusal; returns `client`.

    Patches the instance rather than the class, so a test or a script holding
    a client of its own is unaffected, and accepts None so callers can pass
    `indexer_if_present` without checking first.
    """
    if client is None or getattr(client, _INSTALLED, False):
        return client
    for funnel in FUNNELS:
        original = getattr(client, funnel, None)
        if original is None:
            continue
        setattr(
            client,
            funnel,
            retrying(original, attempts=attempts, sleep=sleep, describe=describe_request),
        )
        setattr(client, _INSTALLED, True)
    return client
