---
module: treasury
version: 1
status: active
files:
  - smart_contracts/treasury/contract.py

db_tables: []
depends_on: []
---

# Treasury

## Purpose

A treasury that receives deposits from any source and divides them on a
schedule nobody controls.

The property worth having is governance rather than convenience: **no one can
delay a distribution to a convenient moment, front-run it, or quietly skip
one.** The schedule is credible precisely because nobody is running it.

`distribute` allocates; it never pays. Recipients pull their own money, for the
reason every demo here does: a scheduled call cannot reach an account it was
not handed, and a push to a closed or hostile account would fail the whole
execution and stall the schedule for everyone else.

## Public API

### Exported Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `TOTAL_SHARE_BPS` | `10_000` | Shares are basis points and must total exactly this. |
| `MAX_RECIPIENTS` | `8` | `distribute` walks every recipient on every scheduled call, and that call must never fail; the bound keeps it cheap. |
| `RECIPIENTS_KEY` | `b"recipients"` | Box holding the recipient list. |
| `BOX_MBR_FIXED` | `2_500 + 400 * 10` (`6_500`) | Box minimum balance less the recipient list. A box costs `BOX_MBR_FIXED + 400 * len(encoded recipients)` µALGO: 2,500 per box plus 400 per byte of the 10-byte `RECIPIENTS_KEY` and the ARC-4 encoded value, which carries its own length prefix. |

### Exported Types

| Type | Description |
|------|-------------|
| `Treasury` | ARC-4 contract class; global state `balance`, `allocated_total`, `distributions`, `configured`; recipients in a box. |
| `Recipient` | ARC-4 struct: `who: Address`, `share_bps: UInt64`, `owed: UInt64`. |
| `Distributed` | ARC-28 event per distribution: `distribution`, `round_number`, `snapshot`, `allocated`. |

#### Treasury Methods

| Method | Parameters | Returns | Description |
|--------|-----------|---------|-------------|
| `configure` | `mbr_payment: pay, recipients: (address,uint64,uint64)[]` | `uint64` | Creator only, once, forever. Fixes the recipients and shares; returns the count. |
| `deposit` | `payment: pay` | `uint64` | Contribute from anywhere; returns the balance awaiting distribution. |
| `distribute` | — | `uint64` | Zero-argument, the shape Arcron calls. Allocates the accumulated balance and returns the amount, or `0`. |
| `claim` | — | `uint64` | A recipient pulls everything owed to them. |
| `owed_to` | `who: address` | `uint64` | Readonly. |

## Invariants

1. `distribute` never fails: unconfigured or with an empty balance it returns `0` and changes nothing. A failing hook trips keeper backoff and would end the schedule.
2. Allocated never exceeds the snapshot. The remainder from integer division stays in the treasury for the next distribution, never stranded and never over-allocated.
3. Shares total exactly `TOTAL_SHARE_BPS` and every share is positive.
4. Recipients are distinct and none is the zero address. `claim` stops at the first entry matching the sender, so a duplicate's share could never be pulled, and an address nobody can send from could never pull anything. Both strand money permanently on a contract configured once and never updated.
5. `distribute` cannot overflow. It divides before multiplying and adds back the remainder's share, because `snapshot * share_bps` overflows uint64 above about 1.84 million ALGO and the AVM panics rather than saturating, which would fail every later distribution and strand the balance.
6. Recipients and shares are immutable after configuration. There is no method to add, remove, redirect or reweight.
7. Funds leave only to a recipient claiming their own allocation.
8. Deposits arriving after a snapshot belong to the next distribution.
9. An unclaimed allocation never blocks a later distribution.
10. `configure`'s MBR payment must cover what the recipient box actually
    costs, checked the same way every sibling contract checks its own box
    MBR. An underfunded payment fails at `configure` rather than reaching the
    box write with a confusing revert.
11. `deposit` requires `configured`. Before `configure` runs there is no
    recipient to credit and no withdraw path, so an uncredited deposit would
    strand into a pooled `balance` nobody could ever pull back out if
    `configure` were never called at all.
12. `configure`'s MBR payment and `deposit`'s payment are both checked for
    `rekey_to` and `close_remainder_to`, which must be the zero address.
    Neither harms the contract, since a rekey or a close only ever harms the
    sender; this protects a depositor whose front end slipped either into
    the group they signed.

## Why the recipient set is immutable

A mutable set needs somebody with authority to redirect the money, which is
exactly the position this design exists to eliminate. It would also quietly
undo the governance property, since an owner who can change recipients before a
distribution can do everything a discretionary treasurer can. To change a
split, deploy another treasury and point deposits at it; the old one's history
stays intact and auditable.

## Behavioral Examples

### Scenario: A quiet period

- **Given** a configured treasury with nothing deposited
- **When** Arcron calls `distribute`
- **Then** it returns `0`, changes nothing, and the keeper is paid

### Scenario: A distribution nobody could move

- **Given** 1,000,000 µALGO deposited and a 50/30/20 split
- **When** a keeper calls `distribute` at the scheduled round
- **Then** 500,000 / 300,000 / 200,000 are credited, and the balance is zero

### Scenario: Dust

- **Given** 7 µALGO and a 50/30/20 split
- **When** it is distributed
- **Then** 3 + 2 + 1 are allocated and 1 remains in the treasury for next time

## Error Cases

| Condition | Behavior |
|-----------|----------|
| `configure` by a non-creator, or twice | Fails with "Only the creator can configure" / "Already configured" |
| Shares not totalling 10,000 | Fails with "Shares must total 10,000 basis points" |
| A zero share, or a non-zero starting `owed` | Fails with "A share must be positive" / "Owed must start at zero" |
| More than `MAX_RECIPIENTS`, or none | Fails with "Recipient count out of bounds" |
| `configure`'s MBR payment below the box's cost | Fails with "MBR payment too small" |
| `configure`'s or `deposit`'s payment carries a rekey | Fails with "... must not rekey" |
| `configure`'s or `deposit`'s payment carries a close-remainder-to | Fails with "... must not close" |
| `deposit` before `configure` | Fails with "Not configured" |
| `deposit` of zero, or to another receiver | Fails with "Amount must be positive" / "must go to the app account" |
| `claim` by a non-recipient | Fails with "Not a recipient" |
| `claim` with nothing owed | Fails with "Nothing owed to you" |

## Deferred: the buy step

The issue this came from also describes swapping accumulated ALGO for an ASA on
a DEX and distributing the proceeds. That half is not built here, and the
reason has changed since the issue was written.

It was assumed to need multi-argument calls and foreign arrays. The
resource-availability spike (issue #24, results in `docs/arcron.md`) measured
otherwise: an Arcron-triggered inner call *can* reach an unreferenced app,
account or asset, provided the keeper attaches the reference to its own
transaction. So the DEX interaction is reachable today in principle.

What is missing is *discovery*: nothing on-chain tells a keeper which
references an upkeep needs, so no keeper would know to attach a DEX's app id.
That is the reframed issue #8. Until it exists, a scheduled swap would depend
on a keeper who happens to know, and that is not a property to build a treasury
on.

## Dependencies

### Consumes

| Module | What is used |
|--------|-------------|
| `algopy` (Algorand Python / Puya) | ARC-4 framework, `Box`, `arc4.DynamicArray`, `GlobalState`, `gtxn`, `itxn`, `arc4.emit` |

### Consumed By

| Module | What is used |
|--------|-------------|
| `scripts/treasury_demo.py` | Drives consecutive scheduled distributions with a real keeper |

## Change Log

| Date | Author | Change |
|------|--------|--------|
| 2026-08-25 | CorvidLabs | #96: `configure` now checks its MBR payment's amount, matching every sibling contract, and `deposit` now requires `configured`, closing the path where deposits could land before `configure` is ever called and strand permanently. `BOX_MBR_FIXED` exported. #102 in the same pass: `configure` and `deposit` assert `rekey_to` and `close_remainder_to` are the zero address on their payments. Neither is a struct change. |
| 2026-08-24 | CorvidLabs | Initial accumulate-and-distribute treasury (issue #28). Buy step deferred: reachable in principle per the #24 spike, blocked in practice on reference discovery (#8). |
