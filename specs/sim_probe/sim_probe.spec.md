---
module: sim_probe
version: 1
status: active
files:
  - smart_contracts/sim_probe/contract.py

db_tables: []
depends_on: []
---

# Sim Probe

## Purpose

Answers one question and holds the answer still: **can algod `simulate`
honestly predict what a real Arcron `execute` will do, before the upkeep box
`execute` requires even exists?**

The console plan proposes a "Test" button on the registration form that
simulates the call Arcron would make, before a creator escrows anything. This
contract is the fixed set of targets that question is checked against:
methods that isolate the sender check `docs/integrating.md` recommends, an
unconditional revert, a resource need that fits Arcron's documented 6-slot
budget and one that does not, and a budget cost bigger than any real
execution grants. `scripts/spike_simulate_test_button.py` drives it and is
the reproducible record of the answer.

Experimental scaffolding. Nothing in the keeper network depends on it, and it
is not deployed to any public network.

## Public API

### Exported Types

| Type | Description |
|------|-------------|
| `SimProbe` | ARC4 contract class; global state `keeper_app: uint64`, `calls: uint64`, `s0`..`s6: Account` (seven configurable subject accounts). |

#### SimProbe Methods

| Method | Parameters | Returns | Description |
|--------|-----------|---------|-------------|
| `configure` | `keeper_app: uint64` | `void` | Names the keeper app `keeper_only` checks the sender against. |
| `configure_subjects` | `s0..s6: address` | `void` | Names the seven accounts `needs_six`/`needs_seven` read the balance of. |
| `works` | — | `uint64` | No requirements at all. The control target: should pass everywhere, always. |
| `keeper_only` | — | `uint64` | Passes only when `Txn.sender == Application(keeper_app).address` — exactly the check `docs/integrating.md` recommends every integration write. |
| `always_reverts` | — | `uint64` | Fails unconditionally, on every path, every time. |
| `needs_six` | — | `uint64` | Reads the ALGO balance of six accounts named nowhere in the call. Fits the 6 resource slots Arcron's own `execute` leaves a target (`docs/arcron.md`). |
| `needs_seven` | — | `uint64` | Reads seven accounts. Requests 9 total references once Arcron's own 2 (box + target app) are counted — one more than the AVM's 8-reference ceiling. |
| `burns_budget` | — | `uint64` | A fixed 100-iteration `sha256` loop, deliberately costing more opcode budget than any real execution grants (~1,250 through Arcron, per `docs/integrating.md`). Fails wherever it is really run. |

## Invariants

1. Every method takes no arguments beyond its selector, the shape Arcron can call in v1.
2. `keeper_only`'s check is copied verbatim from `docs/integrating.md`'s recommended pattern, so a result about it is a result about the guide, not about a probe-specific idiom.
3. `needs_six` and `needs_seven` differ by exactly one account read, so a pass/fail flip between them isolates the resource-slot ceiling rather than some other difference.
4. `burns_budget`'s loop count (100 `sha256` calls over a growing digest) was chosen to exceed even the larger, Arcron-pooled budget (~1,250), so it fails identically whether called directly or through a real execution — any simulated *pass* is necessarily an artifact of how the simulation was built (e.g. `extra_opcode_budget`), not of the target.
5. Nothing here is called by the keeper network; it is only ever a target.

## Behavioral Examples

### Scenario: The recommended sender check needs a resource hint to simulate

- **Given** a standalone simulated call to `keeper_only`, sender set to the keeper app's own address, no other flags
- **When** simulated with `allow_unnamed_resources=False`
- **Then** it fails with `unavailable App <keeper_app_id>`, because looking up that app's own address costs a reference no transaction in this synthetic group supplies (a real execution gets it free, as the top-level call's own ApplicationID)
- **And** with `allow_unnamed_resources=True` it passes, matching what a real execution does

### Scenario: A resource-needing target can look like it fits when it does not

- **Given** `needs_seven`, simulated standalone (sender = keeper app address, all seven accounts attached directly)
- **When** simulated alone, with no other transaction in the group
- **Then** it passes — a standalone call pays none of the 2 slots a real Arcron `execute` spends on its own box and target app, so it has 8 slots to itself instead of the 6 a real target gets
- **And** a real `execute()` against the same target, with the same seven accounts attached by hand, is rejected outright (9 references requested, 8 available)

### Scenario: extra_opcode_budget can make a simulation lie about budget

- **Given** `burns_budget`, which costs more opcode budget than any real execution ever grants
- **When** simulated with the default `extra_opcode_budget=0`, it fails, matching real execution
- **When** simulated with `extra_opcode_budget=17000`, it passes — a budget no real Arcron execution will ever hand it

## Error Cases

| Condition | Behavior |
|-----------|----------|
| `keeper_only` called by anyone but the keeper app | Fails on the sender assertion (once the app-lookup resource itself is available) |
| `always_reverts` called at all | Fails unconditionally |
| `needs_six`/`needs_seven` with an account unavailable to the call | Fails with `unavailable Account …` |
| `needs_seven` even with every account attached, inside a real Arcron execution | Rejected: 9 total references requested, 8 available |
| `burns_budget` at any budget below ~3,500 | Fails with `dynamic cost budget exceeded` |

## Dependencies

### Consumes

| Module | What is used |
|--------|-------------|
| `algopy` (Algorand Python / Puya) | ARC-4 framework, `Application`, `Account`, `GlobalState`, `op`, `urange` |

### Consumed By

| Module | What is used |
|--------|-------------|
| `scripts/spike_simulate_test_button.py` | Registers each target as an upkeep, simulates the inner call standalone with a keeper-app-address sender, and compares the prediction against a real `execute()` |
| `scripts/reference_boundary.py` | Registers `needs_six`/`needs_seven` as upkeeps and services them through the real `scripts.keeper_bot.main`, pinning that the bot (not just a hand-built call) reaches the six-reference ceiling and no further |

## Change Log

| Date | Author | Change |
|------|--------|--------|
| 2026-08-26 | CorvidLabs | `needs_six`/`needs_seven` now also pin `scripts/keeper_bot.py` itself, not only the AVM: `scripts/reference_boundary.py` runs both through the real bot and asserts the six-reference target is serviced and the seven-reference one is refused by the AVM, not by the bot falling short of the protocol. No contract change; `scripts/keeper_bot.py` stopped leaving references to algokit-utils' four-account-capped populator. |
| 2026-08-26 | CorvidLabs | Created to answer whether the console's proposed pre-registration "Test" button can honestly predict a real `execute()`. Findings recorded in `scripts/spike_simulate_test_button.py`'s own docstring and run output. |
