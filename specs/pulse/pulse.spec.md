---
module: pulse
version: 1
status: active
files:
  - smart_contracts/pulse/contract.py

db_tables: []
depends_on: []
---

# Pulse

## Purpose

Pulse is a demo upkeep target: a public heartbeat counter. It exists to
prove the Keeper contract end-to-end — `tick` takes no arguments beyond its
method selector, so a registered upkeep can call it on a schedule.
Permissionless by design; it is a demo, not a gate.

## Public API

### Exported Types

| Type | Description |
|------|-------------|
| `Pulse` | ARC-4 contract class; global state `beats: uint64`, `last_beat_round: uint64`. |

#### Pulse Methods

| Method | Parameters | Returns | Description |
|--------|-----------|---------|-------------|
| `tick` | — | `uint64` | Increments `beats`, records the current round, returns the new count. |

## Invariants

1. `beats` increases by exactly 1 per successful `tick` call.
2. `last_beat_round` always equals the round of the most recent `tick`.

## Behavioral Examples

### Scenario: Keeper-driven heartbeat

- **Given** a Keeper upkeep registered against Pulse with `tick`'s selector
- **When** the upkeep is executed
- **Then** `beats` increments by 1 via the keeper's inner app call

## Error Cases

| Condition | Behavior |
|-----------|----------|
| — | No asserted error paths (permissionless demo) |

## Dependencies

### Consumes

| Module | What is used |
|--------|-------------|
| `algopy` (Algorand Python / Puya) | ARC-4 framework, global state |

### Consumed By

| Module | What is used |
|--------|-------------|
| `smart_contracts/keeper/contract.py` | Target of registered upkeeps in tests and the TestNet demo |

## Change Log

| Date | Author | Change |
|------|--------|--------|
| 2026-08-23 | CorvidLabs | Initial demo target: tick heartbeat counter |
