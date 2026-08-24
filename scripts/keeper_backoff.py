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
  less and less of the registry. Never backs off.
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
MAX_BACKOFF_ROUNDS = 1_286  # roughly an hour at 2.8 s/round
# Errors that mean "someone else got there first", not "this upkeep is broken".
RACE_MARKERS = ("not due", "upkeep not found")


def is_lost_race(reason: str) -> bool:
    """True when a failure means another keeper won, not that anything broke."""
    lowered = reason.lower()
    return any(marker in lowered for marker in RACE_MARKERS)


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
    return root / "archon" / f"keeper-backoff-{network}-{app_id}.json"


class Backoff:
    """Which upkeeps to leave alone, and until when."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.entries: dict[int, Entry] = {}
        if path is not None and path.exists():
            self._load()

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
        self, upkeep_id: int, reason: str, current_round: int, interval_rounds: int
    ) -> Entry | None:
        """Back an upkeep off after a failure; returns its entry, or None for a race."""
        if is_lost_race(reason):
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
        except Exception as exc:
            # Corrupt state must never stop a keeper from working.
            logger.warning(f"Ignoring unreadable backoff state {self.path}: {exc}")
            self.entries.clear()
