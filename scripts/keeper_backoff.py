"""Backoff state for a keeper bot, persisted across restarts.

The original rationale for this was "retrying would burn the outer fee every
round". That premise turned out to be false: a failed execution costs the
keeper **nothing**, because Algorand rejects a failing transaction at
validation and it never reaches a block. Measured in `scripts/keeper_e2e.py`
stage 14, on both LocalNet and TestNet.

So this is not about money. It is about not wasting the bot's own scan time
and not crowding the transaction pool with calls that are going to be thrown
away — which justifies a **gentler** schedule than a cost-driven design would
want, and makes two things important:

* **Losing a race is not a failure.** Another keeper executed the upkeep first,
  so ours came back "Not due". In a healthy multi-keeper network that is the
  common case, it is free, and backing off would actively reduce coverage: a
  keeper that stopped trying every upkeep it lost a race for would service
  less and less of the registry. Never backs off. Two signals say a race was
  lost, and they are not equally good: the error text, which a target has
  some say in, and the registry having moved on, which only an execution can
  do. `record_failure` takes both.
* **A broken target is worth retrying, just not constantly.** The wait grows
  exponentially in the upkeep's own intervals but is capped in *rounds*, so a
  daily upkeep is retried hourly rather than in eight days. Retrying is cheap;
  being slow to notice a fix is the only real cost.


Why a target refusal is on a different schedule from everything else
--------------------------------------------------------------------

The schedule above was one schedule for every failure, and that was wrong for
the failure a keeper sees most: **the target said no.**

`docs/reviews/2026-09-01-opus-5-audit-verification.md` §3 is the record.
Grok 4.6 found it: a target that refuses *conditionally* — an oracle rejecting
a stale update, a rebalancer that runs once an epoch, a claim that pays once a
period — is indistinguishable here from one that is permanently broken, so a
single blocked attempt sent this keeper away for `1 x interval` rounds, capped
at `MAX_BACKOFF_ROUNDS`, about an hour. Two separate harms, and the second is
the larger:

1. An attacker who can make a target revert removed every honest keeper for an
   hour at the cost of one application call, which is what made the bought-
   lateness attack in that section cheap. Every profit figure there is a lower
   bound because of this behaviour, and they stay true as measured.
2. The same silence applied to upkeeps with **escalation off**, where there is
   no attacker and nothing to gain. For a liquidation, an oracle or a
   keep-alive, an hour of a keeper not looking can be worth more than every fee
   in that document, and nothing metered it.

So the schedule now branches on **where the failure happened**, which is the
one thing about a failure that a target cannot choose:

    the target's own program refused    1, 2, 4 … rounds, capped at
    ("inner tx N failed", the node's    TARGET_REFUSAL_BACKOFF_ROUNDS and at
    own attribution)                    the upkeep's interval — under 3 minutes

    anything else                       1, 2, 4 … of the upkeep's own
    (a keeper-side refusal, our own     intervals, capped at
    references wrong, a fee below       MAX_BACKOFF_ROUNDS — about an hour,
    minimum, a node that timed out)     unchanged

The line is drawn there because a target refusal is *conditional by
construction*. To reach the target at all a call has already passed `execute`'s
own asserts — the upkeep exists, it is due, the escrow covers the fee — so
whatever made the target say no is state that was not in the registry, and
state that was not in the registry is state that can change in the next round.
A keeper-side refusal is the opposite: the same call will fail the same way
until an operator changes something, and waiting an hour costs nothing.

**What was considered and rejected.**

* *Read the assert message and look for a cooldown.* There is no message to
  read. `assert x, "cooldown not elapsed"` puts that string in the source map,
  not on chain; algod returns `assert failed pc=N` and a disassembly, which is
  the same thing `registry_health.classify_failure` had to be written around.
  And any words a target *can* get in front of a keeper are words a hostile
  target can choose, which is why `is_lost_race` already refuses to believe
  them.
* *Use the program counter as the classifier — same pc means broken, a
  different one means conditional.* It does not separate the two cases. A
  target on a cooldown refuses at the **same** assert every time, exactly as a
  broken one does; the difference between them is only whether the refusal will
  ever stop, and no single failure says. Worse, making the schedule depend on a
  number the target picks hands the target a lever: a hostile one could rotate
  its failing pc to hold this keeper at a one-round retry and spend its request
  budget for it. The site is therefore **recorded and reported and never
  scheduled on** — it is what lets an operator see "the same assert, four
  hundred times" and cancel the upkeep.
* *Give up and remove the backoff.* A genuinely dead target — upkeep 87's, the
  one its author reconfigured to revert — would then be simulated on every scan
  for ever, and that traffic is exactly what
  `docs/reviews/2026-09-01-opus-5-audit-verification.md` §5 is about.
* *Escalate to the hour after enough consecutive refusals.* This is the one
  that sounds most reasonable and is most wrong. A liquidation target refuses
  hundreds of times in a row — there is nothing to liquidate — and then once it
  matters. An escalation rule cannot tell that apart from a dead target either,
  and it fails in the expensive direction on precisely the upkeeps whose
  lateness is worth the most.

**What it costs and why that is the right purchase.** Nothing is broadcast for
a refused attempt: `scripts/keeper_bot.py` simulates first, algokit-utils
raises on a failed group, and the send never happens. So a retry is the wake it
needs plus one `simulate` and one box read — **measured at 3.99 requests, 484
retries and 1,929 requests a day** for one permanently dead target
(`tests/test_keeper_bot.py::test_what_one_permanently_refusing_target_costs`),
against 48 a day at the old ceiling.

That is paid for out of §5's other half, in the same branch: the bot now costs
**about 3,000 requests a day** against the 211,000 that section measured, so a
refusing target roughly doubles a quiet day and is still four hundredths of
what one bot used to spend. What it buys is never being more than
`TARGET_REFUSAL_BACKOFF_ROUNDS` away from a window that matters.
`KEEPER_TARGET_REFUSAL_BACKOFF` raises it for an operator who knows a target is
dead and would rather not pay; `--retry-now` is the other direction, and the
`blackout` line the bot emits for every due upkeep it is skipping is how they
find out there is a decision to make.

**What an attacker can still do.** All of this narrows the blackout; none of it
closes the hole, and the honest list is:

* They can still buy this keeper's absence. **The number that matters is not
  the cost of an hour, it is how long a window stays shut**, because an
  attacker who is being paid does not want the hour — they want the keeper
  gone at the moment the target reopens. That was up to 1,286 rounds and is now
  at most `TARGET_REFUSAL_BACKOFF_ROUNDS`, under three minutes, at the top of
  the ramp. Holding the keeper away *continuously* now costs about twenty
  arranged refusals an hour instead of one, and less than that via the
  sibling-upkeep variant in §3, where the block is a `fee_cap = 0` upkeep the
  attacker executes and is paid for. Both numbers are improvements; neither is
  a closure.
* And a target that fails on **cost rather than logic** still buys the whole
  hour, because `is_target_refusal` cannot see a failure that happens before
  the inner program starts. See its docstring; that hole is deliberate.
* They can still win the reopening round. A keeper that is back within a few
  rounds is in the race; one watching every block is still ahead of it.
* Nothing here touches the case that needs no attacker at all: a creator whose
  target's cooldown is longer than the cadence they chose blocks their own
  upkeep, and the fee escalates for a lateness nobody manufactured. That is a
  contract-side question, and §3's answer to it is a creator-signed
  `raise_fee`, not anything a keeper can do.
* A hostile target can still hold this keeper at the top of the ramp for as
  long as it keeps refusing, because the ramp never gives up and never
  escalates past the cap. That is deliberate, and the paragraph above says what
  it costs.
"""

import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Wait 1, 2, 4 … intervals after consecutive failures, up to this many.
MAX_INTERVAL_MULTIPLIER = 8
# And never wait longer than this in absolute terms, whatever the interval.
# Retrying costs nothing, so a slow upkeep should not mean a slow recovery:
# without this, a daily upkeep at 8x would go unretried for over a week.
MAX_BACKOFF_ROUNDS = 1_286  # roughly an hour at the measured 2.752 s/round


def _env_rounds(name: str, default: int) -> int:
    """A positive round count from the environment, or the default.

    Unset, unparseable and non-positive are the same answer: a backoff ceiling
    nobody chose is worse than the one measured here, and a zero would turn the
    ramp into a busy loop against the node.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Ignoring %s=%r: not an integer.", name, raw)
        return default
    if value <= 0:
        logger.warning("Ignoring %s=%r: must be positive.", name, raw)
        return default
    return value


# The ceiling for a target that refused, in rounds. 64 is under three minutes
# at the measured 2.752 s/round, and it is a power of two so the 1, 2, 4 … ramp
# lands exactly on it after seven consecutive refusals rather than overshooting
# and being clipped. Everything about why this is 64 and not 1,286 is in the
# module docstring; the short version is that a missed liquidation is worth
# more than the thousand requests a day this spends on a dead target.
TARGET_REFUSAL_BACKOFF_ROUNDS = _env_rounds("KEEPER_TARGET_REFUSAL_BACKOFF", 64)
# Doublings are computed as 2**(failures-1); clamp the exponent so a long-lived
# entry cannot build an absurd integer before `min` throws it away.
MAX_DOUBLINGS = 20
# The keeper contract's own reasons for refusing: another keeper got there
# first, or the upkeep is gone.
RACE_MESSAGES = ("not due", "upkeep not found")
# What algod writes when the failure happened inside the call the upkeep
# registered, as "inner tx 0 failed: logic eval error: …". Everything after
# that marker is the *target's* program failing, and a target chooses its own
# text: one asserting "cooldown not due" would otherwise read as another
# keeper having won, and be retried forever. The target cannot suppress the
# marker, because the node writes it, and a keeper-side refusal never carries
# one, because `execute` checks the schedule before it calls anything.
#
# This replaces an earlier check for "executing Keeper" in the message. That
# string is not written by algod at all: `algokit-utils` renders it from the
# *caller's* own app spec (`applications/app_client.py`), so it says "Keeper"
# for every error the bot will ever see, whichever app actually failed, and
# the check it was making was always true.
INNER_FAILURE_MARKER = "inner tx"

# `app=770082145, pc=249` out of the `Details:` clause algod appends. The first
# such clause after the inner-transaction marker is the innermost frame, which
# is the target's; the ones after it are the keeper contract and are the same
# for every upkeep, so they say nothing.
_SITE = re.compile(r"app=(\d+),\s*pc=(\d+)")


def is_target_refusal(reason: str) -> bool:
    """True when the failure happened inside the call the upkeep registered.

    The single definition of that question. `is_lost_race` needs it to know
    that no words in the message can be trusted; `record_failure` needs it to
    pick a schedule; `registry_health.classify_failure` needs it to say TARGET
    REVERTS rather than guessing at a funding problem. Three copies of a
    substring is how two of them drift apart.

    The marker is `inner tx N failed`, which **algod** writes, not the target
    and not the client. A target cannot suppress it, and a keeper-side refusal
    never carries it, because `execute` checks the schedule and the escrow
    before it calls anything.

    **This is a narrower split than "the target's fault", and the difference
    matters.** What it catches is a refusal by the target's own *program
    logic*, once that program is running: an assert, an `err`, a budget it
    exhausts itself (measured across burns of 100 to 5,000 on LocalNet, all
    ten attributed to `inner tx 0`). What it does not catch is a failure that
    happens **before the inner program starts**, because there is no inner
    transaction to attribute it to yet. Two real ones, both seen in this
    repository's own runs:

        dynamic cost budget exceeded ... at PC 1487   (the keeper's program)
        tx references exceed MaxAppTotalTxnReferences = 8

    A target whose cost or resource appetite is state-dependent can therefore
    still put itself on the far side of this line and buy the full hour. That
    is a cost-shaped shutter rather than a logic-shaped one, and it is
    deliberately left on the long schedule: those two failures usually mean
    the target needs more than a keeper can bring at all, which does not
    improve by retrying in a round. Grok 4.6 and Fable 5.1 both found it on
    2026-09-01; it is written down rather than closed because closing it
    means guessing which resource bombs are temporary.
    """
    return INNER_FAILURE_MARKER in reason.lower()


def failure_site(reason: str) -> str:
    """Where in the target the refusal happened, as `app=<id> pc=<n>`.

    Evidence, never a schedule. A target that refuses at one assert four
    hundred times looks broken and a target that moves around looks
    conditional, but neither reading is safe enough to act on — a cooldown
    refuses at the same assert as a break does — and scheduling on a number the
    target picks would let a hostile one choose how often this keeper retries
    it. So this ends up in the log and in the entry, for a person to read.

    Empty when the message carries no attribution, which is most keeper-side
    failures and every node error.
    """
    lowered = reason.lower()
    if INNER_FAILURE_MARKER not in lowered:
        return ""
    found = _SITE.search(reason, lowered.index(INNER_FAILURE_MARKER))
    return f"app={found.group(1)} pc={found.group(2)}" if found else ""


def is_lost_race(reason: str) -> bool:
    """True when a failure means another keeper won, not that anything broke.

    Wrong in either direction costs something. Treating a broken target as a
    lost race retries it forever; treating a lost race as a broken target
    backs off an upkeep that is perfectly healthy.

    This reads the error text, which is the only evidence available at the
    moment of failure. The registry itself is better evidence and arrives a
    beat later: see `record_failure`'s `advanced`.
    """
    if is_target_refusal(reason):
        # The target's program is what failed, so nothing in this message
        # means "another keeper won", whatever words the target chose.
        return False
    lowered = reason.lower()
    return any(message in lowered for message in RACE_MESSAGES)


@dataclass
class Entry:
    """What we know about one upkeep that keeps failing.

    Every field added here needs a default, because `_load` reconstructs this
    from a JSON file an older build wrote and a missing key must not throw a
    keeper's whole history away.
    """

    failures: int = 0
    next_attempt_round: int = 0
    reason: str = ""
    updated_at: float = field(default_factory=time.time)
    #: True when the target's own program refused, which is the short schedule.
    target_refusal: bool = False
    #: `app=<id> pc=<n>` of the target's failing instruction, when the message
    #: carried one. Reported, never scheduled on: see `failure_site`.
    site: str = ""
    #: The round of the first failure in this streak, so the bot can say how
    #: long an upkeep has been going unserviced rather than only that it is.
    since_round: int = 0


def default_state_path(network: str, app_id: int) -> Path:
    """Per-network, per-app, and outside the repo so it is never committed."""
    base = os.environ.get("XDG_STATE_HOME")
    root = Path(base) if base else Path.home() / ".local" / "state"
    return root / "arcron" / f"keeper-backoff-{network}-{app_id}.json"


class Backoff:
    """Which upkeeps to leave alone, and until when."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.entries: dict[int, Entry] = {}
        #: Unix time of the last successful sweep, or None if there has never
        #: been one. It lives here because this is the bot's only durable
        #: per-(network, app) state, and a sweep period measured from process
        #: start is not a period: launchd restarts the keeper on every crash
        #: and every login, and `time.monotonic` does not advance while a
        #: laptop sleeps. Either resets the clock, so "every 86400s" quietly
        #: becomes "every 86400 seconds of awake, uninterrupted uptime".
        self.last_sweep: float | None = None
        if path is not None and path.exists():
            self._load()

    def record_sweep(self, when: float) -> None:
        """Remember a sweep across restarts."""
        self.last_sweep = when
        self.save()

    # -- queries ---------------------------------------------------------
    def blocked(self, upkeep_id: int, current_round: int) -> bool:
        entry = self.entries.get(upkeep_id)
        return entry is not None and current_round < entry.next_attempt_round

    def entry(self, upkeep_id: int) -> Entry | None:
        return self.entries.get(upkeep_id)

    def blocked_ids(self, current_round: int) -> list[int]:
        return sorted(
            upkeep_id
            for upkeep_id in self.entries
            if self.blocked(upkeep_id, current_round)
        )

    def next_attempt_round(self, upkeep_id: int) -> int:
        """The round this upkeep is worth touching again; 0 when it never failed.

        The bot's box cache and its scan clock both ask this. An upkeep it is
        not going to attempt is an upkeep whose box it does not need to read
        and a round it does not need to wake up for, and answering that in one
        place is what keeps the two from disagreeing about when a keeper is
        asleep.
        """
        entry = self.entries.get(upkeep_id)
        return entry.next_attempt_round if entry else 0

    # -- updates ---------------------------------------------------------
    def record_failure(
        self,
        upkeep_id: int,
        reason: str,
        current_round: int,
        interval_rounds: int,
        advanced: bool | None = None,
    ) -> Entry | None:
        """Back an upkeep off after a failure; returns its entry, or None for a race.

        `advanced` is what the registry says: True when the upkeep moved on
        between the scan that picked it and the call that failed, which means
        somebody executed it and we lost, whatever the error text says. It is
        the trustworthy half of the answer, because a keeper reads it from the
        contract's own boxes rather than from a string a target had a hand in.

        It is only ever evidence *for* a race, never against one: a winner
        whose transaction is still in the pool has not moved the box yet, so
        False means "no news", not "nothing happened". Pass None when the
        registry could not be read, and the message is all there is.

        Which schedule the wait comes off is decided by `is_target_refusal`;
        the module docstring is the argument for the split and for the two
        ceilings.
        """
        if advanced:
            # The box moved, so somebody executed this upkeep. That is proof
            # the target works *now*, which outranks any streak of refusals
            # this keeper had accumulated against it: leaving the streak in
            # place would keep backing off an upkeep the network has just
            # demonstrated is healthy, and the wait would go on doubling from
            # wherever it had got to. Nothing to punish, and a clean slate.
            self.record_success(upkeep_id)
            return None
        if is_lost_race(reason):
            # Another keeper won, on the message alone. The message is the
            # weaker of the two signals, so this does not clear a streak the
            # way the registry does; it only declines to add to it.
            return None
        previous = self.entries.get(upkeep_id)
        failures = (previous.failures if previous else 0) + 1
        doublings = 2 ** min(failures - 1, MAX_DOUBLINGS)
        refusal = is_target_refusal(reason)
        if refusal:
            # Rounds, not intervals, and capped low. Also never longer than the
            # upkeep's own interval: on the 20-round cadences live on TestNet a
            # 64-round wait would cost whole windows, and the point of this
            # branch is that it never costs one.
            wait = min(doublings, TARGET_REFUSAL_BACKOFF_ROUNDS, max(interval_rounds, 1))
        else:
            # Unchanged. The same call will fail the same way until an operator
            # changes something, so a slow retry costs nothing here.
            multiplier = min(doublings, MAX_INTERVAL_MULTIPLIER)
            wait = min(multiplier * max(interval_rounds, 1), MAX_BACKOFF_ROUNDS)
        entry = Entry(
            failures=failures,
            next_attempt_round=current_round + wait,
            reason=reason.strip()[:200],
            target_refusal=refusal,
            site=failure_site(reason),
            # The streak's start, kept across failures, so "unserviced since
            # round N" survives every retry in between.
            since_round=previous.since_round if previous and previous.since_round else current_round,
        )
        self.entries[upkeep_id] = entry
        self.save()
        return entry

    def record_success(self, upkeep_id: int) -> None:
        """A working upkeep starts from a clean slate."""
        if self.entries.pop(upkeep_id, None) is not None:
            self.save()

    def clear(self, upkeep_id: int | None = None) -> int:
        """Forget one upkeep's backoff, or all of them. Returns how many cleared."""
        if upkeep_id is None:
            cleared = len(self.entries)
            self.entries.clear()
        else:
            cleared = 1 if self.entries.pop(upkeep_id, None) is not None else 0
        if cleared:
            self.save()
        return cleared

    # -- persistence -----------------------------------------------------
    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "entries": {str(k): asdict(v) for k, v in self.entries.items()},
            "last_sweep": self.last_sweep,
        }
        # Write-then-rename so a killed bot cannot leave a half-written file.
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, indent=2))
        temporary.replace(self.path)

    def _load(self) -> None:
        assert self.path is not None
        try:
            payload = json.loads(self.path.read_text())
            for key, value in payload.get("entries", {}).items():
                self.entries[int(key)] = Entry(**value)
            recorded = payload.get("last_sweep")
            self.last_sweep = float(recorded) if recorded is not None else None
        except Exception as exc:
            # Corrupt state must never stop a keeper from working.
            logger.warning(f"Ignoring unreadable backoff state {self.path}: {exc}")
            self.entries.clear()
            self.last_sweep = None
