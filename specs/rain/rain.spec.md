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
| `APP_BASE_MBR` | `100_000` | The app account's own minimum balance, collected once at `configure` and never credited to the pot. This contract pays out by inner payment, and an account cannot send itself below its own floor, so without this the last winner to claim could not: `resolve` would already have booked the allocation. |

### Exported Types

| Type | Description |
|------|-------------|
| `Rain` | ARC-4 contract class; global state `beacon_app`, `pot`, `tickets`, `draw_id`, `draw_open`, `commit_round`, `prize`, `tickets_snapshot`, `draws_resolved`, `last_winner`. |
| `Drawn` | ARC-28 event when a draw opens: `draw_id`, `commit_round`, `prize`, `tickets`. |
| `Resolved` | ARC-28 event when the beacon has spoken: `draw_id`, `winner`, `prize`, `winning_ticket`. |

#### Rain Methods

| Method | Parameters | Returns | Description |
|--------|-----------|---------|-------------|
| `configure` | `mbr_payment: pay, beacon_app: uint64, gate_creator: address, gate_unit_prefix: byte[], prize_asset: uint64` | `void` | Creator-only, once. Points at the beacon, decides who may enter, and decides what they win. A zero `gate_creator` leaves entry open; an empty `gate_unit_prefix` accepts anything that creator minted; a zero `prize_asset` keeps the pot in ALGO. `mbr_payment` funds the app account's own base minimum balance, held aside and never credited to the pot. |
| `opt_in_prize_asset` | `prize: asset, mbr_payment: pay` | `uint64` | Opts the app into the prize asset so it can be funded. Anyone may pay for it, once. Refuses an asset with a clawback, freeze or manager address, or one that is frozen by default, which is why `prize` is passed: this is the first call that has the asset available and so the first that can read its parameters. |
| `enter` | `mbr_payment: pay, gate_asset: asset` | `uint64` | Buys one ticket for the sender; returns its index. Tickets persist across draws. When gated, `gate_asset` is an asset the sender holds, and the contract checks the collection minted it. Ignored when entry is open. |
| `deposit` | `payment: pay` | `uint64` | Adds ALGO to the pot, from anyone; returns the new pot. Rejected when the prize is an asset. |
| `deposit_asset` | `transfer: axfer` | `uint64` | Adds the prize asset to the pot, from anyone; returns the new pot. Rejected when the prize is ALGO. |
| `draw` | — | `uint64` | Zero-argument, the shape Arcron calls. Opens a draw and returns its id, or `0` when there is nothing to draw for. |
| `resolve` | — | `address` | Permissionless. Reads the beacon for the committed round, picks the winning ticket, credits the allocation. |
| `claim` | `gate_asset: asset` | `uint64` | The winner pulls their prize; returns the amount. On a gated draw the winner must still hold a token from the collection, checked exactly as `enter` checks it. Ignored when `gate_creator` is the zero address. |
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
11. When `gate_unit_prefix` is set, a gate token's unit name must start with
    those bytes as well as being minted by `gate_creator`. The comparison is on
    bytes and so is case-sensitive, and a unit name shorter than the prefix is
    refused rather than reaching a substring past its end. A minting account is
    usually a working wallet: the collection this was built for shares its
    creator with sixteen unrelated test assets, so the creator alone is not the
    collection.
12. `configure` is refused once the pot holds anything or anyone has entered, so the denomination cannot change under people who have already staked on it, and `enter` and `deposit` require configuration first.
13. An asset draw refuses to open unless the app account can cover one allocation box in ALGO, because that draw reserves nothing from the pot and `resolve` would otherwise fail on minimum balance with the draw open.
14. The prize asset cannot be one its issuer can take back or immobilise. An
    asset with a clawback address can be emptied out of the app account at any
    time while `pot` still claims the tokens are there; one with a freeze
    address can be made permanently unclaimable; a manager can set either back.
    All three are refused at `opt_in_prize_asset`, and `deposit_asset` requires
    the opt-in, so an unchecked draw cannot be funded.
15. The prize asset can never buy a ticket, even when the same account minted both it and the collection, which is the natural thing for a project to do.
16. Deposits arriving after a draw opens belong to the next draw.
17. Every payment and asset transfer the contract accepts (`opt_in_prize_asset`,
    `enter`, `deposit`, `deposit_asset`) is checked for `rekey_to`,
    `close_remainder_to` and `asset_close_to`, all of which must be the zero
    address. Neither harms the contract, since a rekey or a close only ever
    harms the sender; this protects an entrant or depositor whose front end
    slipped either into the group they signed.
18. `configure` requires a payment covering `APP_BASE_MBR`, funding the app
    account's own minimum balance before anyone can enter or deposit. The
    payment is held aside and never credited to the pot, so the whole pot can
    still be won: without it, the last winner's `claim` would drop the account
    below its floor and revert after `resolve` had already booked the
    allocation. The payment must come from the caller, checked the same way
    `keeper.register`'s theft path was closed: receiver, amount, rekey and
    close all check out on a payment signed by someone other than the caller,
    so the sender is checked explicitly.

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
| A gate token whose unit name does not start with `gate_unit_prefix`, or is shorter than it | Fails with "Wrong collection" |
| `configure`'s MBR payment below `APP_BASE_MBR` | Fails with "MBR payment too small" |
| `configure`'s MBR payment not from the caller, or carrying a rekey or close-remainder-to | Fails with "MBR payment must come from the caller" / "MBR payment must not rekey" / "MBR payment must not close" |
| `enter` with an MBR payment below the ticket box cost | Fails with "MBR payment too small" |
| `enter` or `deposit` paying anyone but the app account | Fails with "must fund the app account" / "must go to the app account" |
| `deposit` of zero | Fails with "Amount must be positive" |
| Any accepted payment carries a rekey | Fails with "... must not rekey" |
| Any accepted payment carries a close-remainder-to | Fails with "... must not close" |
| `deposit_asset` carries a rekey | Fails with "Deposit must not rekey" |
| `deposit_asset` carries an asset-close-to | Fails with "Deposit must not close the asset" |
| `resolve` with no draw open | Fails with "No draw is open" |
| `resolve` at or before the committed round | Fails with "Beacon round has not passed" |
| `resolve` without the beacon app referenced by the caller | Fails: the inner call cannot reach an unavailable app |
| `claim` by an account with no allocation | Fails with "Nothing allocated to you" |
| `claim` of an asset prize without opting in | Fails with "Opt in to the prize asset first" |
| `claim` on a gated draw by a winner who no longer holds a collection token | Fails with "Hold a token from the collection". The allocation stays in place, so it becomes collectable again if they reacquire one. This does **not** neutralise tickets bought by walking one token through several accounts: the walker holds both the accounts and the token, so a winning walked ticket is collected by moving the token there first, at the cost of one transfer. What it closes is the account that no longer holds a collection token at all. It also means an honest winner who sells before claiming forfeits, which the contract cannot distinguish. |
| `claim` on a gated draw using the prize asset as the gate token | Fails with "The prize is not a ticket", mirroring `enter`. A project usually mints its prize from the same account as its collection, so without this a past winner holding only prize tokens would satisfy the gate while holding no collection token. |
| `enter` on a gated draw without holding the asset | Fails with "Hold a token from the collection" |
| `enter` on a gated draw with another creator's asset | Fails with "That asset is not from the collection" |
| `deposit_asset` with the wrong asset | Fails with "Wrong asset" |
| `opt_in_prize_asset` twice, or on an ALGO draw | Fails with "Already opted in" / "Prize is ALGO" |
| `opt_in_prize_asset` naming an asset other than the prize | Fails with "Wrong asset" |
| `opt_in_prize_asset` with a clawback, freeze or manager address set | Fails with "Prize asset has a clawback/freeze/manager address" |
| `opt_in_prize_asset` with an asset created `default_frozen` | Fails with "Prize asset is frozen by default". A frozen holding can receive but never send, and `default_frozen` is fixed at creation, so an asset that starts frozen with its freeze address already renounced would pass every other check here and still trap the prize forever. |

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

## Deployment requirement: fund the app account's base minimum balance

This contract pays out by inner payment, and every Algorand account must hold
the base account minimum balance (100,000 microalgo) before it can send
anything. `configure` now requires an `mbr_payment` argument covering
`APP_BASE_MBR`, so this is enforced by the contract rather than left to
whoever deploys to remember: `configure` cannot succeed without it, and
`enter` and `deposit` cannot run before `configure` has.

Before this, nothing reserved it, and skipping it did not fail at deploy or at
deposit: it failed at the moment the last winner tried to claim, after
`resolve` had already booked the allocation, and it failed as a reverted inner
payment rather than as anything that named the cause. `deadman` had exactly
this bug, and `rain` had the same shape without the same fix until now.

`deadman` reserves this out of its one deposit instead of taking a separate
payment, because it has exactly one depositor. A contract like this one, where
the pot is refilled by anyone, cannot do that without deciding which deposit
pays for the app and never gets it back, so `configure` asks for it once,
separately, up front.

## Change Log

| Date | Author | Change |
|------|--------|--------|
| 2026-08-27 | CorvidLabs | `configure` takes a `gate_unit_prefix`, and a gated `enter` requires the gate token's unit name to start with it. The creator alone turned out not to be the collection: the account this was built for holds 31 live assets on TestNet of which 15 are the collection, the rest being working-wallet leftovers called things like `asdf` and `Test`, every one of which bought a ticket. The comparison is on bytes and so case-sensitive, and a unit name shorter than the prefix is refused rather than reaching a substring past its end. An ABI change on a contract whose deployment is a demo, so it takes a new app id. |
| 2026-08-26 | CorvidLabs | #105: `configure` now takes an `mbr_payment` argument covering `APP_BASE_MBR`, funding the app account's own minimum balance before anyone can enter or deposit. Closes the same class of bug `deadman` was fixed for: the last winner's `claim` could drop the account below its floor and revert after `resolve` had already booked the allocation. Not a struct change; an ABI change on a contract nobody has deployed. |
| 2026-08-25 | CorvidLabs | #102: `opt_in_prize_asset`, `enter`, `deposit` and `deposit_asset` now assert `rekey_to`, `close_remainder_to` and `asset_close_to` are the zero address on every payment or asset transfer they accept. Not a struct change; a mechanical hygiene sweep across every contract that accepts a gtxn. |
| 2026-08-24 | CorvidLabs | Initial scheduled draw (issue #25). Two-phase by necessity: `draw` is accounting-only because an Arcron inner call cannot reach the beacon, so `resolve` is sent by a participant who can. |
