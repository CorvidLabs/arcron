---
module: resource_probe
version: 1
status: active
files:
  - smart_contracts/resource_probe/contract.py

db_tables: []
depends_on: []
---

# Resource Probe

## Purpose

Answers one question and holds the answer still: **what resources can an
Archon-triggered inner call actually reach?**

Archon's `execute` submits an inner app call with no foreign arrays. This
contract is a deliberate target for that call: each method reaches for exactly
one kind of resource that no argument names, so a failure identifies the rule
rather than a tangle of them. `scripts/spike_resources.py` drives it and
prints the results table reproduced in `docs/archon.md`.

Experimental scaffolding. Nothing in the keeper network depends on it, and it
is not deployed to any public network.

## Public API

### Exported Types

| Type | Description |
|------|-------------|
| `ResourceProbe` | ARC-4 contract class; global state `subject: Address`, `subject_asset: uint64`, `subject_app: uint64`, `probes_run: uint64`, `last_reading: uint64`, `last_number: uint64`, `last_text: string`. |

#### ResourceProbe Methods

| Method | Parameters | Returns | Description |
|--------|-----------|---------|-------------|
| `configure` | `subject: address, asset: uint64, app: uint64` | `void` | Point the probes at an account, an asset and an app. |
| `opt_in_to_asset` | — | `void` | Hold the configured asset, so a transfer probe fails on availability alone. |
| `probe_payment` | — | `uint64` | Inner payment to the configured account. |
| `probe_asset_transfer` | — | `uint64` | Inner asset transfer to the configured account. |
| `probe_read_balance` | — | `uint64` | Read the configured account's ALGO balance. |
| `probe_read_holding` | — | `uint64` | Read the configured account's holding of the asset. |
| `probe_app_call` | — | `uint64` | Inner app call to the configured app. |
| `report_budget` | — | `uint64` | Records the opcode budget available to this call — directly, and through an Archon upkeep, which is how the figures in `docs/integrating.md` were measured. |
| `absorb` | `number: uint64, text: string` | `uint64` | A hook with arguments of its own — the shape Archon cannot call today. Records the budget it was handed and both arguments, so a call that loses one is distinguishable from a call that works. |

## Invariants

1. Every probe method except `absorb` takes no arguments beyond its selector, so Archon can call it in the v1 shape. `absorb` is deliberately the opposite: it exists to be unreachable in that shape.
2. `report_budget` and `absorb` measure rather than assert: an Archon-triggered call sees a *larger* budget than a direct one, because opcode budget pools across the app calls in a group. Both read the budget before doing anything else, so the two figures are comparable.
3. A probe touches exactly one resource kind, so a failure names one rule.
4. Nothing here is called by the keeper network; it is only ever a target.

## Behavioral Examples

### Scenario: A bare execution cannot reach an unreferenced account

- **Given** a probe configured with an account named in no argument
- **When** Archon executes `probe_payment` with no references attached
- **Then** the transaction fails with `unavailable Account …` and nothing is spent

### Scenario: The keeper supplies availability

- **Given** the same upkeep
- **When** the keeper attaches that account to its own `execute` transaction
- **Then** the probe's inner payment succeeds — availability flows two levels down

### Scenario: A hook with arguments needs an app arg per argument

- **Given** an upkeep registered against `absorb(uint64,string)`
- **When** a keeper variant that stores `byte[][]` executes it
- **Then** `last_number` and `last_text` hold both arguments, and `last_reading` shows the budget the target was handed

## Error Cases

| Condition | Behavior |
|-----------|----------|
| Resource not referenced anywhere in the group | Fails with `unavailable Account`/`unavailable App` |
| More than 8 total references on the keeper's transaction | Rejected with `exceed MaxAppTotalTxnReferences = 8` |
| Asset transfer to an account not opted in | Fails for opt-in reasons, not availability — the spike opts the subject in first to keep the cases apart |

## Dependencies

### Consumes

| Module | What is used |
|--------|-------------|
| `algopy` (Algorand Python / Puya) | ARC-4 framework, `itxn`, `Account`, `Asset`, `Application`, `GlobalState` |

### Consumed By

| Module | What is used |
|--------|-------------|
| `scripts/spike_resources.py` | Registers each probe as an upkeep and records what succeeds |
| `scripts/spike_multiarg.py` | Calls `report_budget` and `absorb` through today's keeper and a multi-arg variant, and compares |

## Change Log

| Date | Author | Change |
|------|--------|--------|
| 2026-08-24 | CorvidLabs | Created to answer issue #24: keeper-supplied references reach two levels down, with a budget of 8 references per transaction. |
| 2026-08-24 | CorvidLabs | Added `report_budget` for issue #26: an Archon-triggered call has ~1.8× the opcode budget of a direct one (1,250 vs 684), because budget pools across the group. |
| 2026-08-24 | CorvidLabs | Added `absorb` for issue #8: a hook taking real arguments, used to price a multi-arg call shape (1,216 budget for one argument, 1,139 for three). |
