---
module: subscription
version: 1
status: active
files:
  - smart_contracts/subscription/contract.py

db_tables: []
depends_on: []
---

# Subscription

## Purpose

Recurring billing where the schedule is not a server somebody has to keep
running. A provider prices a period, subscribers deposit, and a keeper advances
the billing period on a cadence.

This contract exists partly as a worked argument for the shape
`docs/integrating.md` recommends. The obvious design bills every subscriber
inside the scheduled call: walk the subscriber boxes, debit each, pay the
provider. That design cannot work, for two independent reasons:

1. an Arcron inner call reaches only what the keeper's own transaction made
   available, and nothing on-chain tells a keeper which subscriber boxes exist,
   so the hook cannot open them; and
2. one closed or hostile account would fail the whole execution, wedging
   billing for every other subscriber.

So `charge` does the smallest thing that can be done without naming anybody. It
increments a counter. Everything else is arithmetic against that counter,
performed in transactions the interested party sends themselves, where their
own box is available by construction.

## Public API

### Exported Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `BOX_PREFIX` | `b"s"` | Prefix for subscriber boxes, keyed by address. |
| `MAX_ROUNDS_PER_PERIOD` | `1_000_000_000` | Ceiling on `min_rounds_per_period`, matching the keeper's own interval ceiling. An unbounded cadence overflows the comparison in `charge` and freezes billing permanently. |
| `SUBSCRIBER_BOX_MBR` | `2_500 + 400 * (33 + 16)` | Minimum balance one subscriber box locks in the app account. Charged to the subscriber's first deposit and refunded on withdrawal. |

### Exported Types

| Type | Description |
|------|-------------|
| `Subscription` | ARC-4 contract class; global state `keeper_app`, `provider`, `price_per_period`, `period`, `last_charged_round`, `provider_accrued`. |
| `Subscriber` | ARC-4 struct: `balance: UInt64`, `paid_through_period: UInt64`. One box per subscriber. |

#### Subscription Methods

| Method | Parameters | Returns | Description |
|--------|-----------|---------|-------------|
| `create` | `provider: address, price_per_period: uint64, min_rounds_per_period: uint64` | — | Creation only. Fixes the provider, the price, and the shortest a period may be. |
| `set_keeper` | `keeper_app: uint64` | — | Creator only, once. Names the keeper app allowed to advance billing. |
| `charge` | — | `uint64` | Zero-argument, the shape Arcron calls. Advances the period and returns it. Touches no boxes and moves no money. |
| `subscribe` | `deposit: pay` | `uint64` | Opens or tops up a subscription; returns the resulting balance. |
| `settle` | `subscriber: address` | `uint64` | Bills one subscriber for elapsed periods; returns the number actually paid for. |
| `withdraw` | — | `uint64` | Settles as far as the balance reaches, then closes the subscription and refunds what is left plus box MBR. |
| `claim` | — | `uint64` | Provider only. Collects what settlement has credited. |

## Invariants

1. `charge` cannot fail for a keeper calling on the cadence the upkeep was
   registered with. It opens no box, submits no inner transaction, and its one
   rejection path cannot be reached by an honest schedule. A hook that fails
   trips keeper backoff, and billing that silently stops is worse than billing
   that is late.
2. A period cannot be billed faster than `min_rounds_per_period`. The keeper
   sender check authenticates the messenger, not the schedule: registering an
   upkeep is permissionless, so anyone may point one at `charge` on the
   shortest interval the keeper allows and pay for it themselves. Without this
   a provider could fabricate periods for roughly two minimum fees each and
   settle a subscriber's whole balance to itself. A call that arrives too soon
   returns rather than rejecting, so a `CATCH_UP` replay cannot trip keeper
   backoff.
3. Settlement never multiplies a period count it cannot afford. `periods *
   price` computed before capping by balance overflows for a large price, and
   the AVM panics rather than saturating, which would make the box
   undeletable.
4. `charge` moves no money. Every transfer happens in a transaction sent by
   the party it concerns, or naming them explicitly.
5. A subscriber is billed only for periods that began after they subscribed.
   `paid_through_period` starts at the current period.
6. A subscriber who cannot cover every elapsed period pays for as many whole
   periods as their balance allows and remains owing the rest. Partial payment
   never forgives a period.
7. `provider_accrued` only ever increases by amounts debited from a subscriber
   balance in the same call, so the contract can always pay what it has
   credited.
8. A lapsed subscriber cannot block billing, settlement, or withdrawal for
   anybody else.
9. `withdraw` requires settlement first, so a subscriber cannot outrun the
   schedule by leaving while periods are owed.
10. Box MBR is charged to the subscriber's first deposit and returned on
   withdrawal, so the app account never subsidises a subscription.
11. `subscribe`'s deposit is checked for `rekey_to` and `close_remainder_to`,
   both of which must be the zero address. Neither harms the contract, since
   a rekey or a close only ever harms the sender; this protects a subscriber
   whose front end slipped either into the group they signed.

## Why billing is split from charging

Because the two halves have different failure tolerances.

Advancing the period must never fail, because a failing hook stops being
serviced and the schedule is the only part nobody can reconstruct afterwards.
Moving money is allowed to fail, because it can be retried by whoever cares,
and because failure there concerns one subscriber rather than all of them.

Splitting them puts each half where its failure is survivable. It also means
the provider does not depend on subscribers doing anything: `settle` names its
subject, so the provider can run it for anybody.

## Behavioral Examples

### Scenario: A period with nobody to bill

- **Given** a subscription app with no subscribers
- **When** a keeper calls `charge`
- **Then** the period advances, nothing else changes, and the keeper is paid

### Scenario: A subscriber who can afford everything

- **Given** a subscriber paid through period 0, holding 300,000 µALGO, at a price of 50,000
- **When** four periods have passed and `settle` is called
- **Then** they are billed for 4 periods, 200,000 µALGO is credited to the provider, and 100,000 remains

### Scenario: A subscriber who runs out

- **Given** a subscriber holding 100,000 µALGO at a price of 50,000
- **When** four periods have passed and `settle` is called
- **Then** they are billed for 2 periods, still owe 2, and the schedule is unaffected

### Scenario: Leaving

- **Given** a settled subscriber with 100,000 µALGO remaining
- **When** they call `withdraw`
- **Then** they receive 100,000 plus `SUBSCRIBER_BOX_MBR` and the box is deleted

## Error Cases

| Condition | Behavior |
|-----------|----------|
| `create` with a zero price | Fails with "Price must be positive" |
| `set_keeper` by a non-creator, or twice | Fails with "Only the creator can set the keeper" / "Keeper already set" |
| `charge` from anything but the keeper app account | Fails with "Only the keeper app may advance billing" |
| `charge` again before `min_rounds_per_period` has passed | Returns the current period unchanged. It does not reject: under `CATCH_UP` a keeper draining a backlog replays immediately, and rejecting would trip keeper backoff and stop billing altogether |
| `create` with a zero `min_rounds_per_period` | Fails with "A period must span some rounds" |
| `create` with a cadence above `MAX_ROUNDS_PER_PERIOD` | Fails with "Cadence too long" |
| `create` with the zero address as provider | Fails with "Provider required" |
| `subscribe` paying another receiver | Fails with "Pay this app" |
| `subscribe` where the deposit is not from the caller | Fails with "Deposit must come from the caller" |
| A first deposit not covering box MBR | Fails with "First deposit must cover the box" |
| `settle` for an address with no box | Fails with "No such subscriber" |
| `withdraw` without a subscription | Fails with "Not subscribed" |
| `claim` by anyone but the provider | Fails with "Only the provider may claim" |
| `claim` with nothing accrued | Fails with "Nothing accrued" |
| `subscribe`'s deposit carries a rekey | Fails with "Deposit must not rekey" |
| `subscribe`'s deposit carries a close-remainder-to | Fails with "Deposit must not close" |

## Dependencies

### Consumes

| Module | What is used |
|--------|-------------|
| `algopy` (Algorand Python / Puya) | ARC-4 framework, `Box`, `GlobalState`, `gtxn`, `itxn`, `op.concat` |

### Provides

| Consumer | What is used |
|----------|-------------|
| `smart_contracts/keeper` | `charge()uint64`, the zero-argument hook an upkeep targets |
| `scripts/subscription_demo.py` | The whole surface, exercised on LocalNet |

## Testing

`scripts/subscription_demo.py` runs the whole cycle on LocalNet against a real
keeper: two subscribers funded differently, four scheduled periods, settlement
that bills one in full and one partially, a further period after one has
lapsed, and the provider's claim. `fledge run smoke-subscription`.

## Deployment requirement: fund the app account's base minimum balance

This contract pays out by inner payment, and every Algorand account must hold
the base account minimum balance (100,000 microalgo) before it can send
anything. Nothing in the contract reserves it, so the deployer must send it
once, before the first payout is owed.

Skipping it does not fail at deploy or at deposit. It fails at the moment the
last party tries to leave, after the contract has already booked what it owes
them, and it fails as a reverted inner payment rather than as anything that
names the cause.

`deadman` reserves this out of its deposit instead, because it has exactly one
depositor. A contract with many cannot do that without deciding which one pays
for the app and never gets it back, so this one asks the deployer.

## Change Log

| Version | Change |
|---------|--------|
| 1 | Initial contract, demo and spec. |
| 1 | #102: `subscribe` now asserts `rekey_to` and `close_remainder_to` are the zero address on its deposit. Not a struct change; a mechanical hygiene sweep across every contract that accepts a gtxn. |
