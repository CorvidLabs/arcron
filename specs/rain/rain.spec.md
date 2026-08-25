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
it cannot reach. That is exactly what a bare Arcron inner call can do.

Everything needing a resource happens in a transaction somebody sends for
themselves. `resolve` inner-calls the randomness beacon, so its *caller*
attaches the beacon reference; a scheduled call could not, because an Arcron
inner call reaches only what the keeper's own transaction makes available.
`claim` pays the winner, who is the sender and therefore always available.

Pull rather than push, for money and for resources alike. A push payout to a
closed account would fail the whole execution and stall the schedule for
everyone.

## Public API

### Exported Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `BEACON_WINDOW` | `1_000` | How long after `commit_round` the beacon still answers. The Foundation beacon retains roughly 1,512 rounds, so a draw nobody resolved inside it can never be resolved. Held short of the real retention so `abandon` cannot race a `resolve` that would still have worked. |
| `BEACON_DELAY` | `8` | Rounds between opening a draw and the beacon value becoming readable, so the outcome is unknowable when the draw opens. |
| `TICKET_PREFIX` | `b"t"` | Box prefix: `b"t" + itob(index)` → the holder's address. |
| `ALLOCATION_PREFIX` | `b"a"` | Box prefix: `b"a" + address` → unclaimed µALGO. |
| `TICKET_MBR` | `2_500 + 400 * 41` (`18_900`) | What one ticket box costs; paid by its buyer. |
| `ALLOCATION_MBR` | `2_500 + 400 * 41` (`18_900`) | What one allocation box costs. Reserved from the pot at draw time and returned on claim when the prize is ALGO; taken from the app account when it is an asset. |
| `ASSET_OPT_IN_MBR` | `100_000` | What holding one asset costs an account, permanently. Paid by whoever calls `opt_in_prize_asset`, who need not be the creator. |

### Exported Types

| Type | Description |
|------|-------------|
| `Rain` | ARC-4 contract class; global state `beacon_app`, `pot`, `tickets`, `draw_id`, `draw_open`, `commit_round`, `prize`, `tickets_snapshot`, `draws_resolved`, `last_winner`. |
| `Drawn` | ARC-28 event when a draw opens: `draw_id`, `commit_round`, `prize`, `tickets`. |
| `Resolved` | ARC-28 event when the beacon has spoken: `draw_id`, `winner`, `prize`, `winning_ticket`. |

#### Rain Methods

| Method | Parameters | Returns | Description |
|--------|-----------|---------|-------------|
| `configure` | `beacon_app: uint64, gate_creator: address, prize_asset: uint64` | `void` | Creator-only, once. Points at the beacon, decides who may enter, and decides what they win. A zero `gate_creator` leaves entry open; a zero `prize_asset` keeps the pot in ALGO. |
| `opt_in_prize_asset` | `prize: asset, mbr_payment: pay` | `uint64` | Opts the app into the prize asset so it can be funded. Anyone may pay for it, once. Refuses an asset with a clawback, freeze or manager address, which is why `prize` is passed: this is the first call that has the asset available and so the first that can read its parameters. |
| `enter` | `mbr_payment: pay, gate_asset: asset` | `uint64` | Buys one ticket for the sender; returns its index. Tickets persist across draws. When gated, `gate_asset` is an asset the sender holds, and the contract checks the collection minted it. Ignored when entry is open. |
| `deposit` | `payment: pay` | `uint64` | Adds ALGO to the pot, from anyone; returns the new pot. Rejected when the prize is an asset. |
| `deposit_asset` | `transfer: axfer` | `uint64` | Adds the prize asset to the pot, from anyone; returns the new pot. Rejected when the prize is ALGO. |
| `draw` | — | `uint64` | Zero-argument, the shape Arcron calls. Opens a draw and returns its id, or `0` when there is nothing to draw for. |
| `resolve` | — | `address` | Permissionless. Reads the beacon for the committed round, picks the winning ticket, credits the allocation. |
| `claim` | — | `uint64` | The winner pulls their prize; returns the amount. |
| `abandon` | — | `uint64` | Reopens a draw whose beacon window has closed, returning the prize and the unused reservation to the pot. Permissionless, and only available once the outcome is unknowable to everyone. |
| `allocation_of` | `who: address` | `uint64` | Readonly. What `who` can claim right now. |

## Invariants

1. `draw` never fails: with no tickets, no pot, a pot no larger than the reservation, or a draw already open, it returns `0` and changes nothing.
2. `draw` moves no funds and makes no inner call, so it cannot fail for want of a resource.
3. A draw's `commit_round` is always in the future when it opens, so at that moment the outcome cannot be known to anyone, including whoever opened it.
4. `resolve` succeeds only after `commit_round` has passed, and only once per draw.
5. The winning ticket is `beacon_value[0:8] mod tickets_snapshot`, taken over the ticket count as it was when the draw opened.
6. Every prize is allocated before it is paid; funds leave only to the account claiming their own allocation.
7. An ALGO draw reserves exactly one `ALLOCATION_MBR` from the pot, and that reservation returns to the pot when the prize is claimed, or immediately if the winner already had an unclaimed allocation. An asset draw reserves nothing from the pot, because the pot is counted in token units and the box is paid for in ALGO: the app account covers it, and the freed minimum balance stays there rather than being recycled into a pot it cannot be added to.
8. The pot is denominated one way or the other and never both. A draw paying an asset refuses ALGO deposits, and a draw paying ALGO refuses asset deposits.
9. Gating checks the asset's creator, not its id. A collection on Algorand is many assets sharing a minter, so holding any one of them is what qualifies, and holding an asset from a different creator does not.
10. A draw can always be reopened. If the beacon window closes before anyone resolves, `abandon` returns the prize and the reservation `draw` took for a box that was never created, so a single unresolved draw cannot lock the pot on a contract that has no update or delete path.
11. `configure` is refused once the pot holds anything or anyone has entered, so the denomination cannot change under people who have already staked on it, and `enter` and `deposit` require configuration first.
12. An asset draw refuses to open unless the app account can cover one allocation box in ALGO, because that draw reserves nothing from the pot and `resolve` would otherwise fail on minimum balance with the draw open.
13. The prize asset cannot be one its issuer can take back or immobilise. An
    asset with a clawback address can be emptied out of the app account at any
    time while `pot` still claims the tokens are there; one with a freeze
    address can be made permanently unclaimable; a manager can set either back.
    All three are refused at `opt_in_prize_asset`, and `deposit_asset` requires
    the opt-in, so an unchecked draw cannot be funded.
14. The prize asset can never buy a ticket, even when the same account minted both it and the collection, which is the natural thing for a project to do.
15. Deposits arriving after a draw opens belong to the next draw.

## Behavioral Examples

### Scenario: A scheduled draw on a quiet week

- **Given** a rain app with tickets but an empty pot
- **When** Arcron calls `draw` on its cadence
- **Then** it returns `0`, changes nothing, and the keeper is paid. A failure here would trip keeper backoff and stop the draw permanently

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
| `claim` of an asset prize without opting in | Fails with "Opt in to the prize asset first" |
| `enter` on a gated draw without holding the asset | Fails with "Hold a token from the collection" |
| `enter` on a gated draw with another creator's asset | Fails with "That asset is not from the collection" |
| `deposit_asset` with the wrong asset | Fails with "Wrong asset" |
| `opt_in_prize_asset` twice, or on an ALGO draw | Fails with "Already opted in" / "Prize is ALGO" |
| `opt_in_prize_asset` naming an asset other than the prize | Fails with "Wrong asset" |
| `opt_in_prize_asset` with a clawback, freeze or manager address set | Fails with "Prize asset has a clawback/freeze/manager address" |

## Dependencies

### Consumes

| Module | What is used |
|--------|-------------|
| `algopy` (Algorand Python / Puya) | ARC-4 framework, `Box`, `GlobalState`, `gtxn`, `itxn`, `arc4.emit`, `op` |
| Randomness beacon (ARC-21) | `must_get(uint64,byte[])byte[]` on TestNet `600011887` and MainNet `1615566206`, verified against the deployed programs |

### Consumed By

| Module | What is used |
|--------|-------------|
| `scripts/rain_demo.py` | Drives draws with a real keeper on LocalNet |
| `smart_contracts/beacon_stub/contract.py` | Stands in for the beacon where none exists |

## Change Log

| Date | Author | Change |
|------|--------|--------|
| 2026-08-24 | CorvidLabs | Initial scheduled draw (issue #25). Two-phase by necessity: `draw` is accounting-only because an Arcron inner call cannot reach the beacon, so `resolve` is sent by a participant who can. |
