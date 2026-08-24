# Timed release: publish on a round nobody controls

The scenario: you have something that must become public at a specific moment
— an embargoed statement, a disclosure, a result — and you want it to happen
even if you are asleep, unavailable, or have since changed your mind.

`smart_contracts/embargo/` is a contract that does exactly that, and it is the
clearest demonstration of what Archon is for.

## What it guarantees

- **It cannot go out early.** `publish()` fails before the release round.
- **It cannot be stopped.** Once scheduled there is no method — for anyone,
  the author included — that changes the content, moves the round, or cancels.
- **It does not need you.** Publication is permissionless and paid, so a
  keeper anywhere fires it and collects the fee.

## What it does not guarantee

**Secrecy before release.** Box contents are readable by anyone the moment
they are written; that is how a public chain works. This schedules an
unstoppable, timestamped publication *event* — not a sealed envelope.

If the content must be unreadable until it opens, store a hash commitment here
and keep the payload off-chain. Be clear-eyed about the cost: revealing it
later requires someone to act, and that someone is exactly what a keeper
network cannot be for you. Archon can guarantee that a *call* happens; it
cannot guarantee that a *secret* is produced.

## Doing it

```bash
poetry run python -m scripts.embargo_demo --network localnet
```

The script deploys a fresh embargo app (one release per instance, by design),
schedules content 25 rounds out, and points an Archon upkeep at `publish()`
with a 10-round interval — deliberately, so the keeper comes due *before* the
embargo lifts:

```
── 3. A keeper arrives early and is refused ──
  ✔ rejected: app=5617
  ✔ the early keeper paid = 0
  ✔ published yet = False
── 5. A keeper who is not the author publishes it ──
  Executed upkeep 102 (target app 5617); +4000 µALGO, next due round 3634
  ✔ published = True
  Published at round 3640
── 6. It cannot be undone ──
  ✔ rejected: Already published
  ✔ rejected: Already scheduled
  ✔ content unchanged
```

Two details worth noticing.

The early attempt **cost the keeper nothing**. Algorand rejects a failing
transaction before it reaches a block, so a keeper that fires too early is out
no ALGO at all — it simply tries again next interval.

The publisher was **not the author**. Nothing in the contract checks who calls
`publish()`; the author has no privileged position and no veto.

## Building your own

```python
release_round = algod.status()["last-round"] + 30_857   # ~a day at 2.8 s/round
embargo.send.schedule(
    args=ScheduleArgs(
        mbr_payment=payment(BOX_MBR_FIXED + 400 * len(content)),
        content=content,
        release_round=release_round,
    )
)
```

Then register an upkeep against `publish()` exactly as you would any other
target — `examples/register_upkeep.py` is the template. Two things to get
right:

- **Fund for more than one attempt.** If the upkeep comes due before the
  release round, that execution fails harmlessly; the escrow is untouched, but
  the upkeep needs to still be scheduled when the round arrives. Funding a few
  executions costs 4,000 µALGO each and removes the question.
- **Rounds are not a clock.** "A day" is ~30,857 rounds, and drifts — see the
  liveness notes in [`docs/archon.md`](../docs/archon.md). If the exact moment
  matters more than the guarantee, pick a round you are happy to be early
  against, not the one that maps to a wall-clock time today.
