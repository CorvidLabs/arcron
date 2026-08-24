---
module: keeper
version: 1
status: active
files:
  - smart_contracts/keeper/contract.py

db_tables: []
depends_on: []
---

# Keeper

## Purpose

Keeper is a permissionless keeper network as an ARC-4 smart contract
(Algorand Python / Puya). Contracts can't wake themselves on Algorand, so
anyone registers an **upkeep** — "call this app with this data every N
rounds, paying R µALGO per execution" — escrowing ALGO in the contract.
Any keeper may execute a due upkeep; the contract performs the registered
inner app call and pays the keeper from the escrow. No owner, no protocol
rake, token-agnostic (escrow is plain ALGO).

## Public API

The module exports no top-level functions; its surface is the `Keeper`
contract class, the `Upkeep` struct, and its constants.

### Exported Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `MIN_INTERVAL_ROUNDS` | `10` | Minimum spacing between executions of one upkeep, in rounds. |
| `MIN_UPKEEP_FEE` | `4_000` | Minimum ALGO reward per execution (µALGO); keepers pay ~3,000 µALGO in txn fees, so this keeps executions profitable. |
| `MAX_CALL_DATA` | `1_024` | Maximum size of the stored call data (first app arg), in bytes. |

### Exported Types

| Type | Description |
|------|-------------|
| `Keeper` | ARC-4 contract class; global state `next_upkeep_id: uint64`; one `Upkeep` struct per box (`"u" \|\| id BE64`, 9-byte names). |
| `Upkeep` | ARC-4 struct: `creator: Address`, `target_app: UInt64`, `call_data: DynamicBytes`, `interval_rounds: UInt64`, `next_execution_round: UInt64`, `fee_per_execution: UInt64`, `balance: UInt64`, `times_executed: UInt64`. |

#### Keeper Methods

| Method | Parameters | Returns | Description |
|--------|-----------|---------|-------------|
| `register` | `mbr_payment: pay, funding_payment: pay, target_app: application, call_data: byte[], interval_rounds: uint64, fee_per_execution: uint64` | `uint64` | Registers an upkeep, escrowing `funding_payment` and creating its box; returns the upkeep id. |
| `top_up` | `upkeep_id: uint64, funding_payment: pay` | `uint64` | Adds ALGO to an upkeep's escrow; returns the new balance. |
| `cancel` | `upkeep_id: uint64` | `void` | Creator-only; deletes the box and refunds the remaining escrow. Box MBR stays with the app. |
| `execute` | `upkeep_id: uint64` | `uint64` | Permissionless; when due, performs the registered inner app call, pays the caller its fee from escrow, and returns the next due round. |

## Invariants

1. Upkeep ids are assigned sequentially from 0 and never reused.
2. `execute` only succeeds when `Global.round >= next_execution_round` and escrow `balance >= fee_per_execution`; otherwise the group fails and no state changes.
3. State (next round, balance, times) is updated before any inner transaction, so a re-entrant target cannot double-execute the same window.
4. Escrow can only leave the contract as: keeper fees (`execute`), or a refund to the creator (`cancel`).
5. Only the upkeep's creator can cancel it; cancellation never touches already-paid fees.
6. The contract performs at most one registered inner app call per `execute`, with exactly the stored call data.

## Behavioral Examples

### Scenario: Register and execute an upkeep

- **Given** a Pulse app and a funder who escrows 5× the fee with interval 10
- **When** round R+10 arrives and any account calls `execute`
- **Then** the contract calls Pulse's `tick` via inner transaction, pays the executor R µALGO, and the upkeep is next due at R+20

### Scenario: Cancel with remaining escrow

- **Given** an upkeep with 12,000 µALGO escrowed
- **When** its creator calls `cancel`
- **Then** the box is deleted and 12,000 µALGO is returned to the creator via inner payment

## Error Cases

| Condition | Behavior |
|-----------|----------|
| Interval below `MIN_INTERVAL_ROUNDS` | Fails with "Interval below minimum" |
| Fee below `MIN_UPKEEP_FEE` | Fails with "Fee below minimum" |
| Empty or over-1024-byte call data | Fails with "Call data size out of bounds" |
| MBR payment below computed box MBR | Fails with "MBR payment too small" |
| Funding below one execution fee | Fails with "Funding must cover at least one execution" |
| `execute` before the due round | Fails with "Not due" |
| `execute` with escrow below the fee | Fails with "Insufficient funding" |
| `execute`/`cancel`/`top_up` for a missing id | Fails with "Upkeep not found" |
| `cancel` by a non-creator | Fails with "Only the creator can cancel" |
| Registered target app call fails | Whole group fails; no fee paid, no state change |

## Dependencies

### Consumes

| Module | What is used |
|--------|-------------|
| `algopy` (Algorand Python / Puya) | ARC-4 framework, `arc4.Struct`, `Box`, `gtxn`, `itxn` (inner app call + payment), `op` |

### Consumed By

| Module | What is used |
|--------|-------------|
| `smart_contracts/pulse/contract.py` | Demo upkeep target (heartbeat counter) used in tests and the TestNet demo |
| `scripts/keeper_testnet_demo.py` | Deploy, register, execute against live TestNet apps |

## Change Log

| Date | Author | Change |
|------|--------|--------|
| 2026-08-23 | CorvidLabs | Initial keeper network: register, top_up, cancel, execute; Upkeep struct in boxes |
