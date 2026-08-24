---
module: rain
version: 1
status: active
files:
  - smart_contracts/rain/contract.py

db_tables: []
depends_on: []
---

# Rain

## Purpose

A pot that pays a random ticket holder on a schedule, run by nobody.

It is the reference implementation of the technique that makes real
applications buildable on Arcron's v1 call shape: **the scheduled call does
accounting only.** `draw` locks a prize, snapshots the ticket count and fixes a
future beacon round. It moves no money, calls no other app, and touches nothing
it cannot reach — which is exactly what a bare Arcron inner call can do.

Everything needing a resource happens in a transaction somebody sends for
themselves. `resolve` inner-calls the randomness beacon, so its *caller*
attaches the beacon reference; a scheduled call could not, because an Arcron
inner call reaches only what the keeper's own transaction makes available.
`claim` pays the winner, who is the sender and therefore always available.

Pull, not push — for money and for resources alike. A push payout to a closed
account would fail the whole execution and stall the schedule for everyone.

## Public API

### Exported Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `BEACON_DELAY` | `8` | Rounds between opening a draw and the beacon value becoming readable, so the outcome is unknowable when the draw opens. |
| `TICKET_PREFIX` | `b"t"` | Box prefix: `b"t" + itob(index)` → the holder's address. |
| `ALLOCATION_PREFIX` | `b"a"` | Box prefix: `b"a" + address` → unclaimed µALGO. |
| `TICKET_MBR` | `2_500 + 400 * 41` (`18_900`) | What one ticket box costs; paid by its buyer. |
| `ALLOCATION_MBR` | `2_500 + 400 * 41` (`18_900`) | What one allocation box costs; reserved from the pot at draw time and returned on claim. |

### Exported Types

| Type | Description |
|------|-------------|
| `Rain` | ARC-4 contract class; global state `beacon_app`, `pot`, `tickets`, `draw_id`, `draw_open`, `commit_round`, `prize`, `tickets_snapshot`, `draws_resolved`, `last_winner`. |
| `Drawn` | ARC-28 event when a draw opens: `draw_id`, `commit_round`, `prize`, `tickets`. |
| `Resolved` | ARC-28 event when the beacon has spoken: `draw_id`, `winner`, `prize`, `winning_ticket`. |

#### Rain Methods

| Method | Parameters | Returns | Description |
|--------|-----------|---------|-------------|
| `configure` | `beacon_app: uint64` | `void` | Creator-only, once. Points at the randomness beacon for this network. |
| `enter` | `mbr_payment: pay` | `uint64` | Buys one ticket for the sender; returns its index. Tickets persist across draws. |
| `deposit` | `payment: pay` | `uint64` | Adds to the pot, from anyone; returns the new pot. |
| `draw` | — | `uint64` | Zero-argument, the shape Arcron calls. Opens a draw and returns its id, or `0` when there is nothing to draw for. |
| `resolve` | — | `address` | Permissionless. Reads the beacon for the committed round, picks the winning ticket, credits the allocation. |
| `claim` | — | `uint64` | The winner pulls their prize; returns the amount. |
| `allocation_of` | `who: address` | `uint64` | Readonly. What `who` can claim right now. |

## Invariants

1. `draw` never fails: with no tickets, no pot, a pot no larger than the reservation, or a draw already open, it returns `0` and changes nothing.
2. `draw` moves no funds and makes no inner call, so it cannot fail for want of a resource.
3. A draw's `commit_round` is always in the future when it opens, so the outcome cannot be known to anyone — including whoever opened it — at that moment.
4. `resolve` succeeds only after `commit_round` has passed, and only once per draw.
5. The winning ticket is `beacon_value[0:8] mod tickets_snapshot`, taken over the ticket count as it was when the draw opened.
6. Every prize is allocated before it is paid; funds leave only to the account claiming their own allocation.
7. Each draw reserves exactly one `ALLOCATION_MBR` from the pot, and that reservation returns to the pot when the prize is claimed — or immediately, if the winner already had an unclaimed allocation.
8. Deposits arriving after a draw opens belong to the next draw.

## Behavioral Examples

### Scenario: A scheduled draw on a quiet week

- **Given** a rain app with tickets but an empty pot
- **When** Arcron calls `draw` on its cadence
- **Then** it returns `0`, changes nothing, and the keeper is paid — a failure here would trip keeper backoff and stop the draw permanently

### Scenario: A draw nobody can predict

- **Given** a pot and three tickets, at round R
- **When** `draw` opens draw 1
- **Then** the outcome depends on the beacon at round R+8, which has not happened

### Scenario: A participant resolves and the winner pulls

- **Given** an open draw whose committed round has passed
- **When** any participant calls `resolve`, attaching the beacon app reference
- **Then** the winning ticket's holder is credited, and only that holder can `claim` it

## Error Cases

| Condition | Behavior |
|-----------|----------|
| `configure` by a non-creator, or twice | Fails with "Only the creator can configure" / "Already configured" |
| `enter` with an MBR payment below the ticket box cost | Fails with "MBR payment too small" |
| `enter` or `deposit` paying anyone but the app account | Fails with "must fund the app account" / "must go to the app account" |
| `deposit` of zero | Fails with "Amount must be positive" |
| `resolve` with no draw open | Fails with "No draw is open" |
| `resolve` at or before the committed round | Fails with "Beacon round has not passed" |
| `resolve` without the beacon app referenced by the caller | Fails: the inner call cannot reach an unavailable app |
| `claim` by an account with no allocation | Fails with "Nothing allocated to you" |

## Dependencies

### Consumes

| Module | What is used |
|--------|-------------|
| `algopy` (Algorand Python / Puya) | ARC-4 framework, `Box`, `GlobalState`, `gtxn`, `itxn`, `arc4.emit`, `op` |
| Randomness beacon (ARC-21) | `must_get(uint64,byte[])byte[]` — TestNet `600011887`, MainNet `1615566206`, verified against the deployed programs |

### Consumed By

| Module | What is used |
|--------|-------------|
| `scripts/rain_demo.py` | Drives draws with a real keeper on LocalNet |
| `smart_contracts/beacon_stub/contract.py` | Stands in for the beacon where none exists |

## Change Log

| Date | Author | Change |
|------|--------|--------|
| 2026-08-24 | CorvidLabs | Initial scheduled draw (issue #25). Two-phase by necessity: `draw` is accounting-only because an Arcron inner call cannot reach the beacon, so `resolve` is sent by a participant who can. |
