---
module: beacon_stub
version: 1
status: active
files:
  - smart_contracts/beacon_stub/contract.py

db_tables: []
depends_on: []
---

# Beacon Stub

## Purpose

Stands in for Algorand's randomness beacon on LocalNet, which has none.

It implements the same `must_get(uint64,byte[])byte[]` interface as the real
beacon. That was verified against the deployed programs on TestNet
(`600011887`) and MainNet (`1615566206`) by searching them for the ARC-21
method selector.

**It is not random.** The value is `sha256(itob(round) || user_data)`, so it is
deterministic and anyone can compute it in advance. That is precisely what
makes it useful in a test, where `scripts/rain_demo.py` predicts the winner
independently and asserts the contract agrees, and precisely why it must never
be pointed at by a real deployment.

## Public API

### Exported Types

| Type | Description |
|------|-------------|
| `BeaconStub` | ARC-4 contract class; no state. |

#### BeaconStub Methods

| Method | Parameters | Returns | Description |
|--------|-----------|---------|-------------|
| `must_get` | `round_number: uint64, user_data: byte[]` | `byte[]` | 32 deterministic bytes for a round that has already passed. |

## Invariants

1. `must_get` fails for a round that has not passed, which is the property a commit-then-resolve scheme depends on.
2. The same round and user data always produce the same value, so tests can predict it.
3. No state, no funds, no authority. It answers questions and nothing else.

## Behavioral Examples

### Scenario: A caller asks about a past round

- **Given** the current round is 1,050
- **When** `must_get(1_000, "")` is called
- **Then** it returns `sha256(itob(1000))`, 32 bytes

### Scenario: A caller asks about the future

- **Given** the current round is 1,000
- **When** `must_get(1_000, "")` is called
- **Then** it fails with "Round has not passed yet"

## Error Cases

| Condition | Behavior |
|-----------|----------|
| `round_number >= Global.round` | Fails with "Round has not passed yet" |
| Called without being referenced by the sender | Fails: an inner call cannot reach an unavailable app |

## Dependencies

### Consumes

| Module | What is used |
|--------|-------------|
| `algopy` (Algorand Python / Puya) | ARC-4 framework, `op.sha256`, `op.itob` |

### Consumed By

| Module | What is used |
|--------|-------------|
| `scripts/rain_demo.py` | Supplies randomness where LocalNet has no beacon |
| `smart_contracts/rain/contract.py` | Calls it through the configured beacon app id |

## Change Log

| Date | Author | Change |
|------|--------|--------|
| 2026-08-24 | CorvidLabs | Created for issue #25: LocalNet has no randomness beacon, so the rain draw could not otherwise be tested locally. |
