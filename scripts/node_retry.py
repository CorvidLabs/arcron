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

That is Nodely's free-tier allowance for the endpoint as a whole — 200,000
requests a day, shared by everyone using it — and it was 30,000 requests past
it before this repository sent anything. A health run sends about 40 requests,
so there is nothing here to fix by sending fewer.

The measurement that makes retrying the right answer rather than a hopeful
one: once `block=1` is set the edge does not refuse everything, it sheds a
fraction. Measured against a single edge (`x-and-nl: us4@us_losangeles`) whose
quota counter did not move during the sample, so which request gets shed is
the only variable: 4 refusals in 45 requests, about 9%. One request is very
likely to succeed, and a run of 40 is very likely to contain a failure —
1 - 0.91**40 is 98%, which is exactly why two runs in three died while a
one-off `curl` never did.

So this is not waiting for a window to reopen. It is asking to be sampled
again, and four retries take the odds of losing every one from 9% to about
1 in 15,000.

Only the two statuses the edge itself returns are retried:

    429  the per-second or concurrency limit, per Nodely's published policy
    403  the daily request or byte quota, which is what we hit

Both are refused by the CDN in front of the node rather than by algod. That is
why the body never reaches the exception: it is a plain sentence rather than
algod's JSON, so algosdk's `json.loads` fails, the message falls back to the
bare `HTTP Error 403: Forbidden` in the traceback above, and the status code
is the only thing left to match on. It also means the request provably never
reached the node, so replaying it cannot submit anything twice. That is what
makes retrying a POST safe here, and it is the reason 5xx is deliberately
*not* on the list: a 502 from a node that has already accepted a transaction
is a different question, and this module does not answer it.

Nodely's policy says failed requests still count towards the quota, so
retrying does add to a number that is already blown. Four extra requests
against 230,824 is not what is keeping that endpoint over its limit, and the
alternative is a report that does not run.

Installed once, on the clients `network.connect` hands out, so every script
that talks to a public node gets it without anyone remembering to ask.
"""

import functools
import logging
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

#: The per-second or concurrency limit: the node is asking us to slow down.
RATE_LIMITED = 429
#: The daily request or byte quota: the endpoint is over its allowance, and
#: slowing down does not help because the allowance is not ours alone.
QUOTA_EXCEEDED = 403
#: The only statuses worth asking again about. See the module docstring for
#: why 5xx is not among them.
RETRYABLE_STATUSES = (RATE_LIMITED, QUOTA_EXCEEDED)

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
        while True:
            attempt += 1
            try:
                return function(*args, **kwargs)
            except Exception as error:
                if attempt >= attempts or not is_refusal(error):
                    raise
                pause = wait_before(attempt, error)
                logger.warning(
                    f"The node refused {name(*args, **kwargs)} ({error}); "
                    f"attempt {attempt} of {attempts}, retrying in {pause:.1f}s"
                )
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
