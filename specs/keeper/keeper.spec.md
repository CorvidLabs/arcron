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

Keeper is the contract behind Archon, a permissionless keeper network, as an
ARC-4 smart contract (Algorand Python / Puya). Contracts can't wake
themselves on Algorand, so anyone registers an **upkeep** — "call this app
with this data every N rounds, paying R µALGO per execution" — escrowing
ALGO in the contract. Any keeper may execute a due upkeep; the contract
performs the registered inner app call and pays the keeper from the escrow.
No owner, no protocol rake, token-agnostic (escrow is plain ALGO).

## Public API

The module exports no top-level functions; its surface is the `Keeper`
contract class, the `Upkeep` struct, and its constants.

### Exported Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `MIN_INTERVAL_ROUNDS` | `10` | Minimum spacing between executions of one upkeep, in rounds. |
| `MIN_UPKEEP_FEE` | `4_000` | Minimum ALGO reward per execution (µALGO); keepers pay ~3,000 µALGO in txn fees, so this keeps executions profitable. |
| `MAX_UPKEEP_FEE` | `1_000_000_000` | Ceiling on both `fee_per_execution` and `fee_cap` (µALGO). Bounds the escalation arithmetic well clear of overflow on a contract that can never be patched. |
| `MAX_CALL_DATA` | `1_024` | Maximum size of the stored call data (first app arg), in bytes. |
| `CATCH_UP` | `0` | Catch-up policy: replay every missed interval, one fee each. The zero value, so it is what an upkeep means by default. |
| `SKIP_AHEAD` | `1` | Catch-up policy: run once and advance to the first slot still in the future, keeping the schedule's phase. |
| `BOX_MBR_FIXED` | `2_500 + 400 * 117` (`49_300`) | Box minimum balance less the call data. A box costs `BOX_MBR_FIXED + 400 * len(call_data)` µALGO: 2,500 per box plus 400 per byte of its 9-byte name and its 108-byte-plus-call-data value (106-byte head, then a 2-byte length prefix on `call_data`). |

### Exported Types

| Type | Description |
|------|-------------|
| `Keeper` | ARC-4 contract class; global state `next_upkeep_id: uint64`; one `Upkeep` struct per box (`"u" \|\| id BE64`, 9-byte names). |
| `Upkeep` | ARC-4 struct: `creator: Address`, `target_app: UInt64`, `call_data: DynamicBytes`, `interval_rounds: UInt64`, `next_execution_round: UInt64`, `fee_per_execution: UInt64`, `balance: UInt64`, `times_executed: UInt64`, `policy: UInt64`, `fee_cap: UInt64`, `last_serviced_round: UInt64`. |

#### Keeper Methods

| Method | Parameters | Returns | Description |
|--------|-----------|---------|-------------|
| `register` | `mbr_payment: pay, funding_payment: pay, target_app: application, call_data: byte[], interval_rounds: uint64, fee_per_execution: uint64, policy: uint64, fee_cap: uint64` | `uint64` | Registers an upkeep, escrowing `funding_payment` and creating its box; returns the upkeep id. `policy` is `CATCH_UP` or `SKIP_AHEAD`; `fee_cap` is the most one execution may ever pay, or `0` for no escalation. |
| `top_up` | `upkeep_id: uint64, funding_payment: pay` | `uint64` | Adds ALGO to an upkeep's escrow; returns the new balance. |
| `cancel` | `upkeep_id: uint64` | `uint64` | Creator-only; deletes the box and refunds the remaining escrow **plus the box MBR** the deletion releases; returns the refunded amount. |
| `execute` | `upkeep_id: uint64` | `uint64` | Permissionless; when due, performs the registered inner app call, pays the caller the **effective fee** from escrow, records the round it ran in, and returns the next due round. |

## Invariants

1. Upkeep ids are assigned sequentially from 0 and never reused.
2. `execute` only succeeds when `Global.round >= next_execution_round` and escrow `balance >= effective fee`; otherwise the group fails and no state changes.
3. State (next round, balance, times) is updated before any inner transaction, so a re-entrant target cannot double-execute the same window.
4. Escrow can only leave the contract as: keeper fees (`execute`), or a refund to the creator (`cancel`).
5. The MBR collected by `register` is exactly what the box costs the app account, so the app's spendable balance always covers the total escrow it holds — every upkeep can pay out its last execution.
6. Only the upkeep's creator can cancel it; cancellation never touches already-paid fees.
7. The contract performs at most one registered inner app call per `execute`, with exactly the stored call data.
8. Registering and then cancelling an upkeep is balance-neutral for the app account: what `register` collects, `cancel` returns.
9. The **effective fee** is `fee_per_execution` when `fee_cap <= fee_per_execution`; otherwise it rises linearly from the fee to the cap across one missed interval and then holds at the cap: `fee + (fee_cap - fee) * min(max(lateness - interval, 0), interval) / interval`, where `lateness = Global.round - last_serviced_round`. It is therefore never above `fee_cap` and never below `fee_per_execution`.
10. Lateness is measured from `last_serviced_round`, never from `next_execution_round`. Only the first execution of a catch-up burst can be escalated; every replay behind it pays the base fee, because the upkeep was serviced moments earlier.
11. `last_serviced_round` is the round `execute` ran in, set to `Global.round` at registration and at every execution. It is the only on-chain record of when an upkeep actually ran; `next_execution_round - interval_rounds` is the round it was *scheduled* for and the two differ by the whole backlog whenever an upkeep is catching up.
12. Under `CATCH_UP`, `next_execution_round += interval_rounds`, so a neglected upkeep stays due until it has replayed every missed interval. Under `SKIP_AHEAD` it advances to the first slot strictly greater than `Global.round` that is still a whole number of intervals from the original schedule, so one execution clears any backlog without the schedule drifting.

## Behavioral Examples

### Scenario: Register and execute an upkeep

- **Given** a Pulse app and a funder who escrows 5× the fee with interval 10
- **When** round R+10 arrives and any account calls `execute`
- **Then** the contract calls Pulse's `tick` via inner transaction, pays the executor R µALGO, and the upkeep is next due at R+20

### Scenario: Cancel with remaining escrow

- **Given** an upkeep with 12,000 µALGO escrowed and a 4-byte selector as call data (41,300 µALGO of box MBR)
- **When** its creator calls `cancel`
- **Then** the box is deleted and 53,300 µALGO — escrow plus the released box MBR — is returned to the creator via inner payment

### Scenario: A missed week, under each policy

- **Given** two otherwise identical upkeeps left unserviced for twenty intervals, one `CATCH_UP` and one `SKIP_AHEAD`
- **When** a keeper starts executing them
- **Then** the `CATCH_UP` upkeep runs once per missed interval until it has caught up, and the `SKIP_AHEAD` upkeep runs exactly once and is next due at a round strictly in the future that is still on its original phase

### Scenario: A neglected upkeep pays more, once

- **Given** an upkeep with a 4,000 µALGO fee, a 12,000 µALGO cap, a 10-round interval and `CATCH_UP`, unserviced for twenty intervals
- **When** one keeper drains the whole backlog
- **Then** the first execution pays 12,000 µALGO and every replay behind it pays 4,000 — measured from the schedule instead, all of them would have paid the cap

### Scenario: Escalation raises the bar an upkeep has to clear

- **Given** an upkeep with a 4,000 µALGO fee, a 12,000 µALGO cap and 8,000 µALGO of escrow
- **When** it falls a whole interval behind its last service
- **Then** `execute` fails with "Insufficient funding": the escrow covers two runs at the fee its creator wrote down but not one at the price it is now offering

### Scenario: An app account funded with only its base MBR

- **Given** a freshly created Keeper app holding exactly the 100,000 µALGO account MBR, and one upkeep registered with the minimum funding (one fee)
- **When** a keeper executes it
- **Then** the payment succeeds: the MBR collected at registration covers the box, so the escrow is spendable rather than locked

## Error Cases

| Condition | Behavior |
|-----------|----------|
| Interval below `MIN_INTERVAL_ROUNDS` | Fails with "Interval below minimum" |
| Fee below `MIN_UPKEEP_FEE` | Fails with "Fee below minimum" |
| Fee or cap above `MAX_UPKEEP_FEE` | Fails with "Fee above maximum" / "Fee cap above maximum" |
| `policy` other than `CATCH_UP` or `SKIP_AHEAD` | Fails with "Unknown catch-up policy" |
| Non-zero `fee_cap` below `fee_per_execution` | Fails with "Fee cap below the fee" |
| Empty or over-1024-byte call data | Fails with "Call data size out of bounds" |
| MBR payment below computed box MBR | Fails with "MBR payment too small" |
| Funding below one execution fee | Fails with "Funding must cover at least one execution" |
| `execute` before the due round | Fails with "Not due" |
| `execute` with escrow below the **effective** fee | Fails with "Insufficient funding" |
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
| `scripts/keeper_e2e.py` | Full end-to-end against a real node (LocalNet or TestNet) |
| `scripts/keeper_bot.py` | Off-chain keeper: scans upkeep boxes, executes due upkeeps |

## Change Log

| Date | Author | Change |
|------|--------|--------|
| 2026-08-23 | CorvidLabs | Initial keeper network: register, top_up, cancel, execute; Upkeep struct in boxes |
| 2026-08-24 | CorvidLabs | #7 and #14: `policy`, `fee_cap` and `last_serviced_round` added to `Upkeep`, and `register` takes `policy` and `fee_cap`. A creator chooses whether a missed schedule is replayed (`CATCH_UP`) or dropped (`SKIP_AHEAD`), and may set a ceiling the fee escalates towards while the upkeep is late. Escalation is measured from `last_serviced_round`, so a catch-up burst pays the ceiling once rather than once per replay. `BOX_MBR_FIXED` is now `2_500 + 400 * 117`; the head grew from 82 to 106 bytes and every decoder moved with it. Design: `docs/design/scheduling-and-fees.md`. |
| 2026-08-24 | CorvidLabs | Fix: `register` undercharged box MBR by 800 µALGO (name and length-prefix bytes were miscounted), which left an upkeep's last execution unpayable on an unsubsidised app account. MBR is now `BOX_MBR_FIXED + 400 * len(call_data)`, exported as a constant. `cancel` now also refunds the box MBR it releases and returns the refunded amount (was `void`), so registration is balance-neutral. |
