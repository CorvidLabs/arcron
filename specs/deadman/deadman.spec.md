---
module: deadman
version: 1
status: active
files:
  - smart_contracts/deadman/contract.py

db_tables: []
depends_on: []
---

# DeadMan

## Purpose

A dead man's switch: an owner checks in on a cadence. If the check-ins stop,
for whatever reason, the escrow becomes the beneficiary's. Nobody can prevent
it, because there is no operator to lean on and the firing is done by whichever
keeper happens to be watching.

This is the demonstration of what a permissionless keeper network gives you
that a cron job cannot: the scenario *is* that your infrastructure went away,
so a scheduler you run yourself is worth nothing.

`sweep` does not pay anyone. Paying the beneficiary from a scheduled call
would mean reaching an account that call cannot reach. An Arcron inner call
sees only what the keeper's own transaction makes available. Firing therefore
*allocates*, and the beneficiary *pulls*. See "pull the resource" in
`docs/arcron.md`.

## Public API

### Exported Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `MIN_INTERVAL_ROUNDS` | `30` | Shortest check-in interval. Arcron's own minimum cadence is 10 rounds, so a shorter interval could fire on a keeper's ordinary lateness rather than on the owner's absence. |
| `APP_BASE_MBR` | `100_000` | The app account's own minimum balance, held back out of the deposit at `arm` and never escrowed. `claim` pays the escrow by inner payment, and an account cannot send itself below its floor, so anything booked above this line would be promised and then unpayable. There is no delete path, so it stays locked for the life of the app. |

### Exported Types

| Type | Description |
|------|-------------|
| `DeadMan` | ARC-4 contract class; global state `owner`, `beneficiary`, `interval_rounds`, `deadline`, `escrow`, `allocated`, `fired_round`, `check_ins`. |
| `Fired` | ARC-28 event emitted once: `fired_round`, `deadline`, `beneficiary`, `amount`. |

#### DeadMan Methods

| Method | Parameters | Returns | Description |
|--------|-----------|---------|-------------|
| `arm` | `deposit: pay, beneficiary: address, interval_rounds: uint64` | `uint64` | Owner only, once. Deposits the escrow and returns the first deadline. |
| `check_in` | — | `uint64` | Owner only. Pushes the deadline out; returns the new one. |
| `sweep` | — | `uint64` | Permissionless and zero-argument, which is Arcron's shape. Returns the round it fired in, or `0` when there is nothing to do. |
| `claim` | — | `uint64` | The beneficiary pulls what was released. |
| `rounds_remaining` | — | `uint64` | Readonly. Rounds until firing, or zero once due or fired. |
| `has_fired` | — | `bool` | Readonly. |

## Invariants

1. `sweep` never fails: unarmed, already fired, or before the deadline, it returns `0` and changes nothing. A failing target would trip keeper backoff and stop the switch being watched at all. That is the one outcome that must not happen.
2. Once fired, the switch is permanently inert: `fired_round` is set exactly once and never cleared, and further sweeps do nothing.
3. After firing, the owner cannot check in, cannot re-arm, and cannot recover the escrow. Going quiet is irreversible.
4. Only the owner can check in; only the beneficiary can claim; anyone at all can sweep.
5. Escrow leaves only to the beneficiary, only after firing, and only into a transaction they sent themselves.
6. The beneficiary cannot be the owner, and the interval cannot be shorter than `MIN_INTERVAL_ROUNDS`.

## Behavioral Examples

### Scenario: The owner is present

- **Given** an armed switch with a deadline 30 rounds out
- **When** Arcron sweeps it every 10 rounds and the owner checks in before the deadline
- **Then** every sweep returns `0`, the escrow is untouched, and the deadline moves out

### Scenario: The owner goes quiet

- **Given** an armed switch whose deadline has passed
- **When** any keeper sweeps it
- **Then** it fires, the escrow is allocated to the beneficiary, and the owner can no longer check in

### Scenario: The beneficiary collects

- **Given** a fired switch
- **When** the beneficiary calls `claim`
- **Then** they receive the escrow, and a second claim finds nothing left

## Error Cases

| Condition | Behavior |
|-----------|----------|
| `arm` by a non-owner, or twice | Fails with "Only the owner can arm it" / "Already armed" |
| `arm` with an interval below the minimum | Fails with "Interval below minimum" |
| `arm` with the owner as beneficiary | Fails with "Beneficiary must not be the owner" |
| `arm` with a deposit that does not exceed the app minimum balance | Fails with "Deposit must cover the app minimum balance and leave something to release". `arm` reserves `APP_BASE_MBR` out of the deposit and escrows only the remainder, because `claim` pays the escrow out by inner payment and an account cannot send itself below its own floor. Booking the whole deposit would let the switch fire, promise the beneficiary the full amount, and then fail every claim forever, on a contract with no update or delete path. |
| `check_in` by anyone but the owner | Fails with "Only the owner can check in" |
| `check_in` after firing | Fails with "Already fired" |
| `claim` before firing | Fails with "Switch has not fired" |
| `claim` by anyone but the beneficiary | Fails with "Only the beneficiary can claim" |
| `claim` twice | Fails with "Nothing left to claim" |

## Known failure mode: the upkeep runs dry

If the *upkeep's* escrow is exhausted before the deadline, the switch silently
stops being watched. Nothing on-chain announces this, and the contract cannot
detect it: from the switch's point of view, nobody sweeping and nobody
existing look identical.

This is the failure that would matter most to a real user, so:

- fund the upkeep for far longer than the check-in interval; at 4,000 µALGO
  per sweep, a year of 10-round sweeps is roughly 12 ALGO;
- `poetry run python -m scripts.keeper_bot --check` reports an upkeep whose
  escrow has fallen below one fee as starved, which is exactly this
  condition, and exits non-zero;
- any account can top it up, because funding an upkeep is permissionless, so a
  beneficiary with an interest in the switch being watched can pay for it
  themselves.

Once the switch has fired, its upkeep should be cancelled: a fired switch is
inert, so every further sweep pays a keeper to do nothing.

## Dependencies

### Consumes

| Module | What is used |
|--------|-------------|
| `algopy` (Algorand Python / Puya) | ARC-4 framework, `GlobalState`, `gtxn`, `itxn`, `arc4.emit` |

### Consumed By

| Module | What is used |
|--------|-------------|
| `scripts/deadman_demo.py` | Arms a switch, checks in, goes quiet, and lets a keeper fire it |

## Change Log

| Date | Author | Change |
|------|--------|--------|
| 2026-08-24 | CorvidLabs | Initial dead man's switch (issue #20). Fires by allocation rather than payment, because a scheduled call cannot reach the beneficiary's account. |
