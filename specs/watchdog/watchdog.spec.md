---
module: watchdog
version: 1
status: active
files:
  - smart_contracts/watchdog/contract.py

db_tables: []
depends_on: []
---

# Watchdog

## Purpose

A feed that notices when its own data stops arriving.

Detecting that data stopped requires someone to be watching, and that someone
cannot be the data provider: a provider that goes down has no incentive to
announce it and usually no ability to. Arcron supplies a watcher whose payment
does not depend on the provider's cooperation.

The contract only compares rounds. It never inspects the reported value, so it
cannot be fed a wrong price. That makes it an honest demonstration of how
Arcron composes with data systems without pretending Arcron can supply data. It
is the one oracle-adjacent pattern that requires no oracle trust at all.

## Public API

### Exported Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `MIN_THRESHOLD_ROUNDS` | `30` | Shortest silence that may be called stale. Arcron's minimum cadence is 10 rounds, so a feed must be allowed to be at least a couple of sweeps late before anyone flags it. |

### Exported Types

| Type | Description |
|------|-------------|
| `Watchdog` | ARC-4 contract class; global state `reporter`, `threshold_rounds`, `value`, `updated_round`, `stale`, `stale_since`, `stale_episodes`, `last_recovery_round`, `checks`. |
| `WentStale` | ARC-28 event when silence is first observed: `flagged_round`, `last_update_round`, `rounds_silent`, `episode`. |
| `Recovered` | ARC-28 event when data resumes: `recovered_round`, `silent_for`, `episode`. |

#### Watchdog Methods

| Method | Parameters | Returns | Description |
|--------|-----------|---------|-------------|
| `configure` | `reporter: address, threshold_rounds: uint64` | `uint64` | Creator only, once. Names the reporter, sets the tolerance, and starts the clock. |
| `update` | `value: uint64` | `uint64` | Reporter only. Records a value and clears any stale flag; returns the round. |
| `check_freshness` | — | `uint64` | Zero-argument and permissionless, the shape Arcron calls. Returns the round it flagged in, or `0`. |
| `is_stale` | — | `bool` | Readonly. The flag as of the last check. |
| `rounds_since_update` | — | `uint64` | Readonly. |
| `reading` | — | `uint64` | Readonly. The reported value, which the watchdog itself never inspects. |

## Invariants

1. `check_freshness` never fails: unconfigured, fresh, or already flagged, it returns `0` and does almost nothing. A failing target would trip keeper backoff and leave nobody watching, exactly the outcome this contract exists to prevent.
2. Freshness depends only on rounds. The reported value never affects whether the feed is flagged.
3. A feed is flagged only when silence *exceeds* the threshold; silence of exactly the threshold is still fresh.
4. Each outage is flagged once. Repeated checks during an episode change nothing and emit nothing.
5. `configure` starts the clock, so a feed that never reports at all is still caught.
6. Only the reporter can update; anyone at all can check.
7. Episodes are never forgotten: `stale_episodes` only increases, and `last_recovery_round` records the most recent resumption.

## Recovery policy, and why

**The flag clears automatically on the next update.** The alternative is
one-way flagging until something explicitly clears it. It was rejected for two
reasons:

1. The flag answers a factual question, "has an update landed within the
   threshold?", which has a correct answer at every moment. A sticky flag
   answers a different question, "did it ever go quiet", which consumers who
   care about that can get from `stale_episodes` and `last_recovery_round`.
2. One-way flagging needs someone with authority to clear it. That means
   either the reporter or an admin. The reporter is the party whose outage
   caused the flag, so trusting them is no safer; an admin reintroduces
   exactly the operator this design removes.

So the contract records and the consumer decides: a cautious consumer can
refuse to act for N rounds after `last_recovery_round` without needing anyone's
permission or cooperation.

## Behavioral Examples

### Scenario: A healthy feed

- **Given** a feed updated 10 rounds ago with a 30-round threshold
- **When** Arcron calls `check_freshness`
- **Then** it returns `0`, nothing changes, and the keeper is paid

### Scenario: The reporter goes down

- **Given** a feed silent for more rounds than its threshold
- **When** any keeper calls `check_freshness`
- **Then** the feed is flagged, a `WentStale` event is emitted, and further checks during the same outage change nothing

### Scenario: Data resumes

- **Given** a flagged feed
- **When** the reporter calls `update`
- **Then** the flag clears, `Recovered` is emitted, and the episode remains counted

## Error Cases

| Condition | Behavior |
|-----------|----------|
| `configure` by a non-creator, or twice | Fails with "Only the creator can configure" / "Already configured" |
| `configure` with a threshold below the minimum | Fails with "Threshold below minimum" |
| `update` by anyone but the reporter | Fails with "Only the reporter can update" |
| `update` before configuration | Fails with "Not configured" |
| `check_freshness` in any state | Never fails; returns `0` when there is nothing to do |

## Known limitation: the flag is only as fresh as the last sweep

`is_stale` reports what the last check found. Between checks a feed can go
quiet without the flag being set. Someone has to observe it, which is the whole
premise. A consumer able to read `rounds_since_update` should do that
arithmetic itself; the flag's value is that the observation is recorded
on-chain by a party with no stake in hiding it, and that it emits an event an
off-chain monitor can alert on.

## Dependencies

### Consumes

| Module | What is used |
|--------|-------------|
| `algopy` (Algorand Python / Puya) | ARC-4 framework, `GlobalState`, `arc4.emit` |

### Consumed By

| Module | What is used |
|--------|-------------|
| `scripts/watchdog_demo.py` | Drives fresh → stale → recovered with a real keeper |

## Change Log

| Date | Author | Change |
|------|--------|--------|
| 2026-08-24 | CorvidLabs | Initial staleness watchdog (issue #21). Auto-clearing flag with recorded episodes, chosen over one-way flagging because one-way needs an authority to clear it. |
