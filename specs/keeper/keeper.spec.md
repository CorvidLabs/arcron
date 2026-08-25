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

Keeper is the contract behind Arcron, a permissionless keeper network, as an
ARC-4 smart contract (Algorand Python / Puya). Contracts can't wake
themselves on Algorand, so anyone registers an **upkeep** ("call this app
with this data every N rounds, paying R µALGO per execution") and escrows
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
| `MAX_INTERVAL_ROUNDS` | `1_000_000_000` | Maximum spacing, in rounds, which comes to about 90 years. It forbids nothing anyone wants, and it makes the escalation multiply provably safe without appealing to how old the chain is. |
| `MIN_UPKEEP_FEE` | `4_000` | Minimum ALGO reward per execution (µALGO); keepers pay ~3,000 µALGO in txn fees, so this keeps executions profitable. |
| `MAX_UPKEEP_FEE` | `1_000_000_000` | Ceiling on both `fee_per_execution` and `fee_cap` (µALGO). Bounds the escalation arithmetic well clear of overflow on a contract that can never be patched. |
| `MAX_CALL_DATA` | `1_024` | Maximum size of the stored argument list, in bytes. It covers the whole ARC-4 encoding, not one argument. The AVM's own cap on an app call's arguments is 2,048. |
| `MAX_CALL_ARGS` | `3` | How many app args an execution may carry, counting the selector. Every count needs its own branch in `execute`, so this is what keeps the contract inside one 2,048-byte program page. |
| `ASSET_OPT_IN_MBR` | `100_000` | What the app account's minimum balance rises by per asset it can hold. |
| `CATCH_UP` | `0` | Catch-up policy: replay every missed interval, one fee each. The zero value, so it is what an upkeep means by default. |
| `SKIP_AHEAD` | `1` | Catch-up policy: run once and advance to the first slot still in the future, keeping the schedule's phase. |
| `BOX_MBR_FIXED` | `2_500 + 400 * 139` (`58_100`) | Box minimum balance less the argument list. A box costs `BOX_MBR_FIXED + 400 * len(encoded call_args)` µALGO: 2,500 per box plus 400 per byte of its 9-byte name and its 130-byte head. Unlike a `byte[]`, a `byte[][]` carries its own count inside the encoding, so the whole tail is `call_args.bytes`. |

### Exported Types

| Type | Description |
|------|-------------|
| `Keeper` | ARC-4 contract class; global state `next_upkeep_id: uint64`; one `Upkeep` struct per box (`"u" \|\| id BE64`, 9-byte names). |
| `Upkeep` | ARC-4 struct: `creator: Address`, `target_app: UInt64`, `call_args: DynamicArray[DynamicBytes]`, `interval_rounds: UInt64`, `next_execution_round: UInt64`, `fee_per_execution: UInt64`, `balance: UInt64`, `times_executed: UInt64`, `policy: UInt64`, `fee_cap: UInt64`, `last_serviced_round: UInt64`, `fee_asset: UInt64`, `asset_fee: UInt64`, `asset_balance: UInt64`. |

#### Keeper Methods

| Method | Parameters | Returns | Description |
|--------|-----------|---------|-------------|
| `update` | — | — | `UpdateApplication` only. Creator only, and refused once `frozen` is 1. Replaces the programs. |
| `freeze` | — | — | Creator only, one way. Sets `frozen` to 1, after which `update` can never succeed again. |
| `register` | `mbr_payment: pay, funding_payment: pay, target_app: application, call_args: byte[][], interval_rounds: uint64, fee_per_execution: uint64, policy: uint64, fee_cap: uint64, fee_asset: uint64, asset_fee: uint64` | `uint64` | Registers an upkeep, escrowing `funding_payment` and creating its box; returns the upkeep id. `call_args` is every app arg of the call, in order. `policy` is `CATCH_UP` or `SKIP_AHEAD`; `fee_cap` is the most one execution may ever pay in ALGO, or `0` for no escalation. `fee_asset` and `asset_fee` add an ASA bonus, or `0` for ALGO only. |
| `top_up` | `upkeep_id: uint64, funding_payment: pay` | `uint64` | Adds ALGO to an upkeep's escrow; returns the new balance. |
| `cancel` | `upkeep_id: uint64` | `uint64` | Creator-only; deletes the box and refunds the remaining escrow together with the box MBR the deletion releases; returns the refunded amount. |
| `execute` | `upkeep_id: uint64` | `uint64` | Permissionless; when due, performs the registered inner app call with every stored app arg, pays the caller the **effective fee** from escrow, pays the ASA bonus if there is one the caller can receive, records the round it ran in, and returns the next due round. |
| `opt_in_asset` | `mbr_payment: pay, upkeep_id: uint64, asset: uint64` | `uint64` | Permissionless; lets the app account hold `asset` so an upkeep can escrow a bonus in it. Requires an upkeep that names the asset. The deposit is not refundable, because there is no opt-out. |
| `top_up_asset` | `upkeep_id: uint64, asset_funding: axfer` | `uint64` | Adds ASA to an upkeep's bonus escrow; returns the new asset balance. Separate from `register` because an asset transfer cannot be an optional member of a transaction group. |

## Invariants

1. Upkeep ids are assigned sequentially from 0 and never reused.
2. An ASA bonus is best effort and the ALGO is not. `cancel` pays out no more bonus than the app actually holds, and skips it entirely when the creator is not opted in, so the ALGO refund and the box deletion cannot be blocked by somebody else's asset settings. `execute` likewise skips a bonus the app cannot send. An asset with a clawback address can be taken back out of the app and a frozen one cannot be sent, and both share a transaction with the ALGO, so trusting the recorded balance would strand escrow permanently on a contract with no delete path.
3. `execute` only succeeds when `Global.round >= next_execution_round` and escrow `balance >= effective fee`; otherwise the group fails and no state changes.
4. State (next round, balance, times) is updated before any inner transaction, so a re-entrant target cannot double-execute the same window.
5. Escrow can only leave the contract as: keeper fees (`execute`), or a refund to the creator (`cancel`).
6. The MBR collected by `register` is exactly what the box costs the app account. The app's spendable balance therefore always covers the total escrow it holds, and every upkeep can pay out its last execution.
7. Only the upkeep's creator can cancel it; cancellation never touches already-paid fees.
8. The contract performs at most one registered inner app call per `execute`, with exactly the stored call data.
9. Registering and then cancelling an upkeep is balance-neutral for the app account: what `register` collects, `cancel` returns.
10. The **effective fee** is `fee_per_execution` unless *all* of: `fee_cap > fee_per_execution`, `next_execution_round > last_serviced_round`, and the escrow can cover the escalated amount. When it escalates it rises linearly from the fee to the cap across one missed interval and then holds: `fee + ((fee_cap - fee) * min(max(lateness - interval, 0), interval)) // interval`, where `lateness = Global.round - last_serviced_round`. Multiply first, then floor-divide. It is therefore never above `fee_cap` and never below `fee_per_execution`.
11. **A replay never escalates.** `next_execution_round <= last_serviced_round` means the upkeep was already behind the last time it ran, so the call is draining a backlog rather than clearing a market, and it pays the base fee. This is what makes escalation and `CATCH_UP` safe together: measuring lateness from the last service alone is not enough, because a `CATCH_UP` replay advances the schedule by only one interval, so a keeper that waits two intervals between replays would be late again by its own measure and collect the ceiling every time while the backlog grew without bound.
12. **An upkeep never bids more than it holds.** When the escalated fee exceeds `balance`, the fee falls back to `fee_per_execution`. Without this the escalated fee is a one-way door: lateness only grows, so an escrow that once fell below the escalated price could never reach it again, and the upkeep would hold up to a full `fee_cap` of escrow that no keeper could spend.
13. `last_serviced_round` is the round `execute` ran in, and it is set again at every execution. Registration seeds it with `Global.round`; nothing ran then, but seeding it that way means the first execution is measured from a real round. It is the only on-chain record of when an upkeep actually ran; `next_execution_round - interval_rounds` is the round it was *scheduled* for, and the two differ by the whole backlog whenever an upkeep is catching up.
14. `register` requires funding for one execution at the highest price the upkeep can be charged, meaning `fee_cap` when one is set. Escalation pins the fee at the cap once an upkeep is a whole interval late and lateness only grows, so an upkeep funded only for a base-fee run but carrying a higher cap would be unexecutable by anyone from the first time it fell behind.
15. Re-entrancy is impossible: the AVM refuses to re-enter an application from inside its own execution (`attempt to re-enter <app>`), so a target cannot call `execute` back. The contract's own ordering (state written before any inner transaction) is a second line rather than the only one. Measured in `scripts/spike_reentrancy.py`.
16. `execute` sends every stored app arg, in order, as the inner call's app args. The selector and each ARC-4 argument travel in an app arg of their own, which is what an ARC-4 method requires. `register` bounds the count at `MAX_CALL_ARGS`, so `execute`'s fan-out is exhaustive.
17. **The ALGO fee is never replaced.** An ASA bonus is paid *on top*, and only when there is one, the asset escrow covers it, and the caller is opted in to the asset. A keeper that cannot receive the bonus is not a failed execution: it takes the full ALGO fee and the bonus stays in escrow for the creator. This is what keeps the profitability floor enforceable on-chain without anyone pricing the asset.
18. `cancel` returns the unspent asset balance along with the ALGO and the box MBR. If the creator cannot receive the asset it refuses, before refunding anything.
19. Under `CATCH_UP`, `next_execution_round += interval_rounds`, so a neglected upkeep stays due until it has replayed every missed interval. Under `SKIP_AHEAD` it advances to the first slot strictly greater than `Global.round` that is still a whole number of intervals from the original schedule, so one execution clears any backlog without the schedule drifting.

## Behavioral Examples

### Scenario: Register and execute an upkeep

- **Given** a Pulse app and a funder who escrows 5× the fee with interval 10
- **When** round R+10 arrives and any account calls `execute`
- **Then** the contract calls Pulse's `tick` via inner transaction, pays the executor R µALGO, and the upkeep is next due at R+20

### Scenario: Cancel with remaining escrow

- **Given** an upkeep with 12,000 µALGO escrowed and a bare 4-byte selector as its call data, whose 10-byte encoded argument list costs 62,100 µALGO of box MBR
- **When** its creator calls `cancel`
- **Then** the box is deleted and 74,100 µALGO (escrow plus the released box MBR) is returned to the creator via inner payment

### Scenario: A missed week, under each policy

- **Given** two otherwise identical upkeeps left unserviced for twenty intervals, one `CATCH_UP` and one `SKIP_AHEAD`
- **When** a keeper starts executing them
- **Then** the `CATCH_UP` upkeep runs once per missed interval until it has caught up, and the `SKIP_AHEAD` upkeep runs exactly once and is next due at a round strictly in the future that is still on its original phase

### Scenario: A neglected upkeep pays more, once

- **Given** an upkeep with a 4,000 µALGO fee, a 12,000 µALGO cap, a 10-round interval and `CATCH_UP`, unserviced for twenty intervals
- **When** one keeper drains the whole backlog
- **Then** the first execution pays 12,000 µALGO and every replay behind it pays 4,000. Measured from the schedule instead, all of them would have paid the cap

### Scenario: An escrow that cannot reach the ceiling

- **Given** an upkeep with a 4,000 µALGO fee, a 12,000 µALGO cap and 8,000 µALGO of escrow
- **When** it falls a whole interval behind its last service
- **Then** it pays the base fee and stays executable, because an upkeep bids up to its ceiling but never more than it holds, so the escalated price cannot lock it out of its own escrow

### Scenario: A target method with arguments of its own

- **Given** an upkeep registered against `tick_with(uint64,string)` with the arguments `7` and `"arcron"`
- **When** a keeper executes it
- **Then** the target's counter advances by 7 rather than by 1, and it holds the string, because the selector and both arguments each arrived in an app arg of their own

### Scenario: A keeper that cannot receive the bonus

- **Given** an upkeep with an ASA bonus and a funded asset escrow
- **When** a keeper that has never opted in to that asset executes it
- **Then** the execution succeeds, the keeper is paid the full ALGO fee, and the bonus stays in escrow. Reverting instead would quietly shrink the keeper set for exactly the upkeeps paying extra

### Scenario: A patient keeper cannot farm the ceiling off a backlog

- **Given** a neglected `CATCH_UP` upkeep with a backlog and a ceiling
- **When** a keeper waits two intervals between each replay, so that each one is "late" by lateness alone
- **Then** only the execution that ended the genuine neglect is escalated; every replay pays base, because a replay is draining a backlog rather than clearing a market

### Scenario: An app account funded with only its base MBR

- **Given** a freshly created Keeper app holding exactly the 100,000 µALGO account MBR, and one upkeep registered with the minimum funding (one fee)
- **When** a keeper executes it
- **Then** the payment succeeds: the MBR collected at registration covers the box, so the escrow is spendable rather than locked

## Error Cases

| Condition | Behavior |
|-----------|----------|
| Interval below `MIN_INTERVAL_ROUNDS` | Fails with "Interval below minimum" |
| Interval above `MAX_INTERVAL_ROUNDS` | Fails with "Interval above maximum" |
| Fee below `MIN_UPKEEP_FEE` | Fails with "Fee below minimum" |
| Fee or cap above `MAX_UPKEEP_FEE` | Fails with "Fee above maximum" / "Fee cap above maximum" |
| `policy` other than `CATCH_UP` or `SKIP_AHEAD` | Fails with "Unknown catch-up policy" |
| Non-zero `fee_cap` below `fee_per_execution` | Fails with "Fee cap below the fee" |
| Zero arguments, or more than `MAX_CALL_ARGS` | Fails with "Argument count out of bounds" |
| Encoded argument list over `MAX_CALL_DATA` bytes | Fails with "Argument list too large" |
| `fee_asset` set with `asset_fee` of zero | Fails with "Asset fee must be positive" |
| `top_up_asset` with an asset the upkeep does not use | Fails with "Wrong asset for this upkeep" |
| `opt_in_asset` naming an upkeep that does not use the asset | Fails with "That upkeep does not use this asset" |
| `cancel` with an unspent bonus, by a creator not opted in | Fails with "Opt in to the fee asset before cancelling" |
| MBR payment below computed box MBR | Fails with "MBR payment too small" |
| Funding below one execution at the effective worst case (`fee_cap` when set, else `fee_per_execution`) | Fails with "Funding must cover at least one execution" |
| `execute` before the due round | Fails with "Not due" |
| `execute` with escrow below the effective fee | Fails with "Insufficient funding" |
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
| 2026-08-24 | CorvidLabs | #8 and #9: `call_data: byte[]` becomes `call_args: byte[][]`, so an execution carries the selector and up to two ARC-4 arguments. Before this only zero-argument hooks were reachable. `fee_asset`, `asset_fee` and `asset_balance` add an optional ASA bonus paid on top of the ALGO fee, never instead of it, with `opt_in_asset` and `top_up_asset` to fund it. The fan-out ceiling is 3, chosen because it is what keeps the whole batch inside one program page. `BOX_MBR_FIXED` is now `2_500 + 400 * 139`; the head grew from 106 to 130 bytes and every decoder moved with it. Designs: `docs/design/call-shapes.md`, `docs/design/asa-fees.md`. |
| 2026-08-24 | CorvidLabs | Review hardening for #7/#14: a replay never escalates (`next_execution_round <= last_serviced_round`), because measuring lateness from the last service alone let a patient keeper collect the ceiling on every `CATCH_UP` replay while the backlog grew without bound, measured at 100% of a 400,000 µALGO escrow across 34 runs. An upkeep also never bids more than it holds, so an escrow below the escalated fee falls back to base instead of being locked out permanently. `MAX_INTERVAL_ROUNDS` added so the escalation multiply is bounded by the inputs rather than by the age of the chain. |
| 2026-08-24 | CorvidLabs | #7 and #14: `policy`, `fee_cap` and `last_serviced_round` added to `Upkeep`, and `register` takes `policy` and `fee_cap`. A creator chooses whether a missed schedule is replayed (`CATCH_UP`) or dropped (`SKIP_AHEAD`), and may set a ceiling the fee escalates towards while the upkeep is late. Escalation is measured from `last_serviced_round`, so a catch-up burst pays the ceiling once rather than once per replay. `BOX_MBR_FIXED` is now `2_500 + 400 * 117`; the head grew from 82 to 106 bytes and every decoder moved with it. Design: `docs/design/scheduling-and-fees.md`. |
| 2026-08-24 | CorvidLabs | Fix: `register` undercharged box MBR by 800 µALGO (name and length-prefix bytes were miscounted), which left an upkeep's last execution unpayable on an unsubsidised app account. MBR is now `BOX_MBR_FIXED + 400 * len(call_data)`, exported as a constant. `cancel` now also refunds the box MBR it releases and returns the refunded amount (was `void`), so registration is balance-neutral. |
