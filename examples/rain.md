# Daily rain: a scheduled draw nobody runs

A pot. Tickets. A draw on a cadence. No operator who can pick the winner,
stall the draw, or walk off with the money.

`smart_contracts/rain/` is also the reference for the technique that makes
real applications work on Arcron's v1 call shape, so it is worth reading even
if you never want a prize draw.

## The shape

```
enter()    a ticket, recorded in a box; the buyer pays its MBR
deposit()  anyone adds to the pot
draw()     ZERO ARGS, the call Arcron makes. Locks the prize, snapshots the
           ticket count, fixes a future beacon round. Moves nothing.
resolve()  a participant calls this after that round, attaching the beacon
           reference, and the winner falls out
claim()    the winner pulls their prize
```

The scheduled call does accounting only. That is not an aesthetic choice:

- An Arcron inner call reaches only what the keeper's own transaction makes
  available, and nothing tells a keeper to attach a randomness beacon. A
  `draw()` that read the beacon would simply fail. (Measured; see the
  resource table in [`docs/arcron.md`](../docs/arcron.md).)
- Even if it worked, it would put a third-party app in the path of every
  scheduled execution. A beacon outage would stall the draw for everybody.
- The same argument applies to paying the winner directly: a push to a closed
  account fails the whole execution.

So money and resources are both **pulled** by the party who wants them.

## Fairness

The winner comes from Algorand's VRF randomness beacon, not from block state
that a proposer could grind. The beacon answers only for rounds that have
already passed, so `draw()` commits to a round eight ahead. At the moment the
draw opens, the outcome is unknowable to everyone, including whoever opened it.

LocalNet has no beacon, so `smart_contracts/beacon_stub/` stands in. It is
deliberately **not** random (`sha256(itob(round))`), which is what lets the
demo predict the winner independently and assert the contract agrees:

```python
digest = hashlib.sha256(commit_round.to_bytes(8, "big")).digest()
expected_ticket = int.from_bytes(digest[:8], "big") % tickets
```

Never point a real deployment at the stub.

## Running it

```bash
poetry run python -m scripts.rain_demo --network localnet
```

```
── 3. The keeper opens a draw, accounting only ──
  ✔ draw id = 1        ✔ prize locked = 981100     ✔ pot emptied into the prize = 0
  Draw 1 open; the beacon decides it at round 4165
── 4. A participant resolves it, supplying the beacon ──
  ✔ winning ticket (predicted independently) = …
── 5. The winner pulls the prize ──
  ✔ claimed = 981100   ✔ the reservation returned to the pot = 18900
── 6. A quiet cadence is uneventful ──
  ✔ still one draw = 1   ✔ the keeper was still paid = 2
```

## Two details worth stealing

**The quiet path must be a no-op, not a failure.** Arcron calls `draw()` on
every cadence whether or not there is anything to draw for. A failing target
trips keeper backoff, and a demo that stops drawing because nobody deposited
last week is worse than no demo. `draw()` returns `0` and changes nothing when
there are no tickets, no pot, or a draw already open.

**Reserve the MBR when you lock the prize.** The winner's allocation lives in a
box, and that box costs minimum balance. If the prize were the whole pot,
resolving could fail for want of it. `draw()` sets the prize to the pot less
exactly one `ALLOCATION_MBR`; claiming deletes the box and returns that
reservation to the pot for the next winner. The accounting closes.
