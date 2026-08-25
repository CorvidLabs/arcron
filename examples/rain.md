# Daily rain: a scheduled draw nobody runs

A pot. Tickets. A draw on a cadence. No operator who can pick the winner,
stall the draw, or walk off with the money.

`smart_contracts/rain/` is also the reference for the technique that makes
real applications work on Arcron's v1 call shape, so it is worth reading even
if you never want a prize draw.

Running one for a project rather than reading about the technique? The same
contract gates entry to an NFT collection and pays in your own token:
[a draw for your holders](community-rain.md).

## The shape

```
enter()    a ticket, recorded in a box; the buyer pays its MBR
deposit()  anyone adds to the pot
draw()     ZERO ARGS, the call Arcron makes. Locks the prize, snapshots the
           ticket count, fixes a future beacon round. Moves nothing.
resolve()  a participant calls this after that round, attaching the beacon
           reference, and the winner falls out
claim()    the winner pulls their prize
abandon()  anyone reopens a draw that nobody resolved in time: the prize goes
           back to the pot and the next draw commits to a fresh round
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

## A draw that nobody resolves

`resolve()` works only while the beacon still remembers the committed round.
The Foundation's beacon retains roughly 1,512 rounds, and `BEACON_WINDOW` is
set to **1,000** rounds, a bit under an hour, deliberately short of that
retention so abandoning cannot race a `resolve` that would still have worked.
Past the window `resolve()` refuses, because the underlying `must_get` would
panic anyway.

Without a way out that would be fatal rather than inconvenient. `draw()`
refuses to open another while one is open, so the pot would sit locked in
`prize` forever with no way to reach it. That is what `abandon()` is for: it
is permissionless, it returns the prize to the pot intact along with the
allocation-box reservation `draw()` set aside, and it clears the draw so the
next cadence opens a new one against a fresh beacon round.

Nobody can profit by calling it. It only becomes available once the outcome has
become unknowable to everyone, and it moves the money nowhere except back where
it came from.

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
