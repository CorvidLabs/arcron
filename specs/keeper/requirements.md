---
spec: keeper.spec.md
---

## User Stories

- As a dApp developer, I want to register a scheduled call with an escrow so my contract gets poked on a cadence without running my own infrastructure.
- As a keeper operator, I want permissionless, profitable executions so I can earn fees by watching the chain and calling `execute` when upkeeps are due.
- As a community, I want the same contract usable by any group without permission, so keeper infrastructure is a public good.

## Acceptance Criteria

### REQ-keeper-001
Anyone SHALL be able to register an upkeep (target app, call data ≤ 1024 bytes, interval ≥ 10 rounds, fee ≥ 4000 µALGO and ≤ 1,000,000,000 µALGO) by paying the box MBR and escrowing at least one execution fee, choosing a catch-up policy and optionally a fee ceiling.

### REQ-keeper-002
Any account SHALL be able to execute a due upkeep; the contract SHALL perform exactly the registered inner app call and pay the executor exactly the effective fee from the escrow.

### REQ-keeper-007
A creator SHALL choose at registration whether a missed schedule is replayed (`CATCH_UP`, the default and zero value) or dropped (`SKIP_AHEAD`). Under `SKIP_AHEAD` one execution SHALL clear any backlog, landing on the first slot strictly in the future that is still a whole number of intervals from the original schedule.

### REQ-keeper-008
A creator SHALL be able to set a fee ceiling. While an upkeep is late the effective fee SHALL rise linearly from the base fee to that ceiling across one missed interval and then hold, and SHALL never exceed the ceiling. A zero ceiling SHALL mean the fee never changes.

### REQ-keeper-009
Lateness SHALL be measured from the round the upkeep was last serviced, never from the round it was scheduled for. A call that is draining a backlog (`next_execution_round <= last_serviced_round`) SHALL NOT escalate at all, so that no keeper can profit by servicing a neglected upkeep slowly rather than promptly.

### REQ-keeper-011
The effective fee SHALL never exceed the escrow. An upkeep whose balance cannot cover the escalated fee SHALL be charged the base fee and remain executable, so that escalation can never lock an upkeep out of its own escrow.

### REQ-keeper-010
The contract SHALL record the round each execution ran in, so that off-chain readers do not have to derive it from the schedule; the two differ by the whole backlog whenever an upkeep is catching up.

### REQ-keeper-003
The contract SHALL reject executions before the due round and executions whose escrow cannot cover the effective fee, leaving all state unchanged.

### REQ-keeper-004
The creator, and only the creator, SHALL be able to cancel an upkeep and reclaim its remaining escrow together with the box MBR that deleting the box releases.

### REQ-keeper-005
Anyone SHALL be able to add funding to an existing upkeep's escrow.

### REQ-keeper-006
The MBR charged at registration SHALL equal the box's true minimum balance, so that the app account's spendable balance always covers the escrow it holds and no upkeep can be left unable to pay its last execution.

## Constraints

- Escrow is plain ALGO. An upkeep may additionally offer an ASA bonus on top of the ALGO fee (`fee_asset`, `asset_fee`), paid best-effort: it is skipped when the keeper is not opted in, the app does not hold enough, or either holding is frozen, and skipping it never blocks the ALGO. No token is required to use Arcron.
- Creators and keepers may be post-quantum (Falcon-1024) accounts: their addresses are ordinary 32-byte addresses and the contract compares nothing else. `MIN_UPKEEP_FEE` covers a post-quantum keeper only while Algorand's per-byte fee is zero. A Falcon-signed `execute` is about 13× the size of an ed25519 one, and the floor is permanent (measured in `scripts/spike_quantum.py`).
- Escalation does not raise the balance an upkeep needs to stay executable. When the escalated fee exceeds `balance` the fee falls back to `fee_per_execution`, so the dormancy threshold is always the base fee (see Invariant 12). Readers of the registry should still price *runway* at the ceiling, because a late run can consume that much, but an upkeep is not dormant until it cannot afford one run at its base fee.
- Registered calls are NoOp app calls carrying the creator's stored argument list: the method selector first, then up to `MAX_CALL_ARGS - 1` further ARC-4 encoded arguments. The creator fixes the whole list at `register` and a keeper can neither choose nor alter any part of it. Foreign arrays are resolved off-chain by the keeper, not declared on-chain.
- Keepers are expected to simulate off-chain before executing, but a mistake is cheap: a failing target call fails the whole group, and Algorand rejects the transaction before it reaches a block, so the keeper pays no fee at all (measured in `scripts/keeper_e2e.py` stage 14).

## Out of Scope

- Protocol rake, SLA/slashing mechanics, and keeper-supplied call data. Staking (#15) and keeper-supplied data (#22) are closed rather than deferred; see `docs/design/out-of-scope.md`.
- ASA-denominated fees and multi-argument call shapes were listed here until 1.0 shipped both. The ASA path is a bonus on top of the ALGO fee rather than a denomination, and arguments are fixed by the creator rather than supplied by a keeper, which is what kept both inside the guarantee.
- Escalation curves other than linear, and ceilings expressed as a multiple of the base fee rather than an absolute amount.

The off-chain keeper bot that watches rounds and submits executions
(`scripts/keeper_bot.py`) is outside the contract's surface, but it ships in
this repo and is exercised by `scripts/keeper_e2e.py`.
