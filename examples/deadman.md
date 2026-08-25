# Dead man's switch: something happens because you stopped

The one demo that needs no blockchain knowledge to land. You check in. If you
stop checking in, the escrow goes to whoever you named, and nobody, including
you, can prevent it.

A cron job on your own server cannot do this, because the scenario *is* that
your server went away.

## Doing it

```bash
poetry run python -m scripts.deadman_demo --network localnet
```

```
── 3. While the owner is present, sweeps do nothing ──
  ✔ fired = False
  Owner checked in; deadline moved to 4596
  ✔ escrow untouched = 1000000
── 4. The owner goes quiet ──
  ✔ rounds remaining = 0     ✔ fired = True
  Fired at round 4600 by a keeper, not by anyone interested
── 5. It cannot be undone ──
  ✔ rejected: Already fired
── 6. The beneficiary pulls the escrow ──
  ✔ claimed = 1000000
```

## Three design points worth copying

**The scheduled call allocates; the beneficiary pulls.** `sweep()` does not pay
anyone. Paying would mean reaching an account the scheduled call cannot reach:
an Arcron inner call sees only what the keeper's own transaction makes
available. So firing records the allocation, and the beneficiary claims it in a
transaction they send themselves, where they are the sender and therefore
always reachable.

**The quiet path is the common path.** Arcron sweeps on every cadence for the
entire life of the switch, and almost every one of those calls should do
nothing. `sweep()` returns `0` cheaply and without failing when it is unarmed,
before the deadline, or already fired. A target that *fails* would trip keeper
backoff, and a switch nobody is watching is not a switch.

**A fired switch goes permanently inert.** Otherwise the upkeep keeps paying
keepers to re-fire something that has already fired. The demo shows this
costing a real fee, then cancels the upkeep and reclaims the rest.

## The failure mode to plan for

**If the upkeep's escrow runs dry before your deadline, nobody is watching.**
Nothing announces it. From the switch's point of view, "no keeper swept" and
"no keeper exists" are identical.

- Fund generously, and price the cadence before you pick it. At 4,000 µALGO
  per sweep and 2.8 s per round:

  | Sweep cadence | Sweeps per year | Cost per year |
  |---|---|---|
  | every 10 rounds (28 s) | 1,126,000 | 4,505 ALGO |
  | every 1,286 rounds (1 h) | 8,760 | 35 ALGO |
  | every 30,857 rounds (1 day) | 365 | 1.5 ALGO |

  The demo sweeps every 10 rounds so it finishes while you watch. That is a
  test cadence, not a deployment one. A switch guarding something measured in
  days should sweep in hours, not seconds: the sweep only has to be frequent
  enough to catch the deadline, and every sweep in between is a fee paid to
  learn nothing.
- Monitor it: `poetry run python -m scripts.keeper_bot --check` reports an
  upkeep whose escrow has fallen below one fee as **starved** and exits
  non-zero, which drops straight into cron.
- Anyone can top up an upkeep. The beneficiary has every incentive to keep it
  funded, and needs no permission to do so.

## Choosing an interval

The minimum is 30 rounds. That floor exists because Arcron's own minimum
cadence is 10 rounds: a switch that expires faster than it can be swept would
fire on a keeper's ordinary lateness rather than on your absence.

In practice pick something far longer, and remember rounds are not a clock:
"weekly" is ~216,000 rounds and drifts against the calendar by hours per cycle
(see the liveness notes in [`docs/arcron.md`](../docs/arcron.md)). Leave
yourself margin you would be comfortable with on a bad week.
