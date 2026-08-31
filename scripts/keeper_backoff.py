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
"""

import json
import logging
import os
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


def is_lost_race(reason: str) -> bool:
    """True when a failure means another keeper won, not that anything broke.

    Wrong in either direction costs something. Treating a broken target as a
    lost race retries it forever; treating a lost race as a broken target
    backs off an upkeep that is perfectly healthy.

    This reads the error text, which is the only evidence available at the
    moment of failure. The registry itself is better evidence and arrives a
    beat later: see `record_failure`'s `advanced`.
    """
    lowered = reason.lower()
    if INNER_FAILURE_MARKER in lowered:
        # The target's program is what failed, so nothing in this message
        # means "another keeper won", whatever words the target chose.
        return False
    return any(message in lowered for message in RACE_MESSAGES)


@dataclass
class Entry:
    """What we know about one upkeep that keeps failing."""

    failures: int = 0
    next_attempt_round: int = 0
    reason: str = ""
    updated_at: float = field(default_factory=time.time)


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
        """
        if advanced or is_lost_race(reason):
            # Another keeper won. Nothing to punish, and the upkeep's own
            # schedule already keeps us from hammering it.
            return None
        previous = self.entries.get(upkeep_id)
        failures = (previous.failures if previous else 0) + 1
        multiplier = min(2 ** (failures - 1), MAX_INTERVAL_MULTIPLIER)
        wait = min(multiplier * max(interval_rounds, 1), MAX_BACKOFF_ROUNDS)
        entry = Entry(
            failures=failures,
            next_attempt_round=current_round + wait,
            reason=reason.strip()[:200],
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
