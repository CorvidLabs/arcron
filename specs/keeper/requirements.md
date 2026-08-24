---
spec: keeper.spec.md
---

## User Stories

- As a dApp developer, I want to register a scheduled call with an escrow so my contract gets poked on a cadence without running my own infrastructure.
- As a keeper operator, I want permissionless, profitable executions so I can earn fees by watching the chain and calling `execute` when upkeeps are due.
- As a community, I want the same contract usable by any group without permission, so keeper infrastructure is a public good.

## Acceptance Criteria

### REQ-keeper-001
Anyone SHALL be able to register an upkeep (target app, call data ≤ 1024 bytes, interval ≥ 10 rounds, fee ≥ 4000 µALGO) by paying the box MBR and escrowing at least one execution fee.

### REQ-keeper-002
Any account SHALL be able to execute a due upkeep; the contract SHALL perform exactly the registered inner app call and pay the executor exactly the registered fee from the escrow.

### REQ-keeper-003
The contract SHALL reject executions before the due round and executions with insufficient escrow, leaving all state unchanged.

### REQ-keeper-004
The creator, and only the creator, SHALL be able to cancel an upkeep and reclaim its remaining escrow together with the box MBR that deleting the box releases.

### REQ-keeper-005
Anyone SHALL be able to add funding to an existing upkeep's escrow.

### REQ-keeper-006
The MBR charged at registration SHALL equal the box's true minimum balance, so that the app account's spendable balance always covers the escrow it holds and no upkeep can be left unable to pay its last execution.

## Constraints

- Escrow is plain ALGO in v1 (universal, faucet-friendly); ASA-denominated fees are a possible extension.
- Registered calls are NoOp app calls with exactly one app arg (the stored call data, typically a method selector) and no foreign arrays; targets must accept that shape.
- Keepers are expected to simulate off-chain before executing, but a mistake is cheap: a failing target call fails the whole group, and Algorand rejects the transaction before it reaches a block, so the keeper pays no fee at all (measured in `scripts/keeper_e2e.py` stage 14).

## Out of Scope

- ASA-denominated fees, protocol rake, SLA/slashing mechanics, multi-arg or foreign-array call shapes.

The off-chain keeper bot that watches rounds and submits executions
(`scripts/keeper_bot.py`) is outside the contract's surface, but it ships in
this repo and is exercised by `scripts/keeper_e2e.py`.
