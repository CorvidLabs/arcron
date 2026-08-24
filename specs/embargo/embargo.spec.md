---
module: embargo
version: 1
status: active
files:
  - smart_contracts/embargo/contract.py

db_tables: []
depends_on: []
---

# Embargo

## Purpose

A timed release: content that becomes official at a round nobody controls.

The author commits content and a release round. From that moment there is no
method — not for the author, not for anyone — that changes the content, moves
the round, or cancels the release. `publish` is a zero-argument NoOp, which is
exactly the call shape an Arcron upkeep can make, so any keeper in the world
can be the one to fire it and be paid for doing so.

This is the flagship demonstration of what a keeper network is *for*: a
scheduled action that happens whether or not the person who scheduled it still
wants it to.

**What it does not do:** keep the content secret beforehand. Box contents are
readable by anyone from the moment they are written, on any public chain. What
is guaranteed is an unstoppable, timestamped publication *event*, not a sealed
envelope. For content that must be unreadable until it opens, store a hash
commitment here and keep the payload off-chain — noting that revealing it later
requires someone to act, which is precisely what a keeper network cannot do for
you.

## Public API

### Exported Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `CONTENT_KEY` | `b"content"` | Box name holding the released content. |
| `BOX_MBR_FIXED` | `2_500 + 400 * 7` (`5_300`) | Box minimum balance less the content; a box costs `BOX_MBR_FIXED + 400 * len(content)` µALGO. |
| `MAX_CONTENT` | `2_048` | Maximum content size in bytes — a statement or any CID, bounded so the MBR stays predictable. |

### Exported Types

| Type | Description |
|------|-------------|
| `Embargo` | ARC-4 contract class; global state `author: Address`, `release_round: uint64`, `published_round: uint64`, `content_length: uint64`; content in the box `"content"`. |
| `Published` | ARC-28 event emitted when the embargo lifts: `release_round: uint64`, `published_round: uint64`, `publisher: Address`. |

#### Embargo Methods

| Method | Parameters | Returns | Description |
|--------|-----------|---------|-------------|
| `schedule` | `mbr_payment: pay, content: byte[], release_round: uint64` | `uint64` | Commit content to a release round. Callable once, ever. Returns the release round. |
| `publish` | — | `uint64` | Permissionless; lifts the embargo at or after the release round and returns the round it happened in. The shape an Arcron upkeep calls. |
| `is_published` | — | `bool` | Readonly. Whether the embargo has lifted. |
| `rounds_remaining` | — | `uint64` | Readonly. Rounds until release, or zero once due. |

## Invariants

1. `schedule` succeeds at most once per app instance; there is no path that alters content, release round or author afterwards.
2. `publish` fails before the release round and succeeds at or after it.
3. `publish` succeeds at most once; `published_round` is set exactly once and never cleared.
4. Publication is permissionless: no method checks the caller's identity, so the author has no privileged position and no veto.
5. A release round must be in the future at scheduling time.
6. The MBR collected at scheduling is exactly what the content box costs the app account.
7. Content is public from the moment it is scheduled; the contract makes no secrecy claim.

## Behavioral Examples

### Scenario: A keeper arrives before the embargo lifts

- **Given** content scheduled for round R and an upkeep due at R−13
- **When** a keeper executes the upkeep
- **Then** the inner call fails, nothing is published, and the keeper pays no fee — Algorand rejects the failing transaction before it reaches a block

### Scenario: Published by someone other than the author

- **Given** the release round has arrived
- **When** any keeper calls `publish` via an Arcron upkeep
- **Then** `published_round` is set, a `Published` event is emitted naming that keeper, and the keeper collects the upkeep's fee

### Scenario: The author changes their mind

- **Given** content scheduled but not yet published
- **When** the author tries to reschedule or replace it
- **Then** the call fails with "Already scheduled" — there is no such lever

## Error Cases

| Condition | Behavior |
|-----------|----------|
| `schedule` called twice | Fails with "Already scheduled" |
| Release round at or before the current round | Fails with "Release round is in the past" |
| Empty or over-2048-byte content | Fails with "Content size out of bounds" |
| MBR payment below the box's cost | Fails with "MBR payment too small" |
| MBR payment to any other receiver | Fails with "MBR payment must fund the app account" |
| `publish` before the release round | Fails with "Embargo has not lifted" |
| `publish` twice | Fails with "Already published" |
| `publish` with nothing scheduled | Fails with "Nothing scheduled" |

Note: when `publish` is reached through an Arcron upkeep, the assert's message
does not survive the app boundary — a failure inside an inner call to another
app is reported as a program counter, because the source map belongs to that
app. Match on the failing app id when asserting against it.

## Dependencies

### Consumes

| Module | What is used |
|--------|-------------|
| `algopy` (Algorand Python / Puya) | ARC-4 framework, `Box`, `GlobalState`, `gtxn`, `arc4.emit` |

### Consumed By

| Module | What is used |
|--------|-------------|
| `scripts/embargo_demo.py` | Schedules a release and lets a real keeper publish it |
| `smart_contracts/keeper/contract.py` | Calls `publish()` as a registered upkeep target |

## Change Log

| Date | Author | Change |
|------|--------|--------|
| 2026-08-24 | CorvidLabs | Initial timed-release demo (issue #18): schedule-once, permissionless publish, no author lever. |
