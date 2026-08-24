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
prove the Keeper contract end-to-end. `tick` takes no arguments beyond its
method selector, which was the only shape a registered upkeep could call
before #8; `tick_with` takes real arguments, which is the shape it can call
now. Permissionless by design; it is a demo, not a gate.

## Public API

### Exported Types

| Type | Description |
|------|-------------|
| `Pulse` | ARC-4 contract class; global state `beats: uint64`, `last_beat_round: uint64`. |

#### Pulse Methods

| Method | Parameters | Returns | Description |
|--------|-----------|---------|-------------|
| `tick` | — | `uint64` | Increments `beats`, records the current round, returns the new count. |
| `tick_with` | `beats: uint64, note: string` | `uint64` | Advances `beats` by the argument rather than by one and records the note. A hook with arguments of its own — unreachable through an upkeep before #8, because an ARC-4 method needs its selector and each argument in an app arg of its own. |

## Invariants

1. `beats` increases by exactly 1 per successful `tick` call, and by the `beats` argument per successful `tick_with` call.
2. `last_beat_round` always equals the round of the most recent call of either.
3. Neither method reads anything but its own arguments, so an upkeep against Pulse proves the call shape and nothing else.

## Behavioral Examples

### Scenario: Keeper-driven heartbeat

- **Given** a Keeper upkeep registered against Pulse with `tick`'s selector
- **When** the upkeep is executed
- **Then** `beats` increments by 1 via the keeper's inner app call

### Scenario: A multi-argument upkeep

- **Given** a Keeper upkeep registered with `tick_with`'s selector, `7` and `"archon"`
- **When** the upkeep is executed
- **Then** `beats` advances by 7 and `last_note` holds the string — every app arg arrived

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
| 2026-08-24 | CorvidLabs | Added `tick_with` for issue #8: the demo target now demonstrates a hook with arguments of its own, which is the shape Archon could not call before. |
