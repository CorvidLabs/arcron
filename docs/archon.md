# Archon — keeper network technical reference

Hand-off document for Archon, the permissionless keeper network. For the
quick overview see `../README.md`; for runnable flows see `../examples/`.

## TestNet deployment

| Item | Value |
|------|-------|
| Keeper app | [`769772891`](https://testnet.explorer.perawallet.app/application/769772891) |
| Pulse demo target | [`769772906`](https://testnet.explorer.perawallet.app/application/769772906) |
| Reference bot | `scripts/keeper_bot.py` |
| Proof | `Pulse.beats` incremented by permissionless executions at rounds 66610411 (demo) and 66611741 (bot) |

## Architecture

```
creator                keeper app (769772891)               target app
   | register + escrow ALGO  |                                    |
   |------------------------>|  box "u"+id: Upkeep struct         |
   |                         |                                    |
 keeper bot                  |                                    |
   | execute(upkeep_id) ---->|  inner app call ------------------>|
   |                         |  inner payment (fee) --> keeper    |
```

- One box per upkeep, name `b"u" + itob(upkeep_id)` (9 bytes).
- The registry is fully on-chain and readable with free algod box queries —
  no indexer required (the bot only uses algod).
- `execute` is atomic: the target call and the keeper payment are inner
  transactions of the same call, so a fee is only ever paid alongside a real
  execution.

## Public API

All methods are ARC-4 ABI methods on the keeper app
(`smart_contracts/keeper/contract.py`).

| Method | Callers | Purpose |
|--------|---------|---------|
| `register(mbr_payment, funding_payment, target_app, call_data, interval_rounds, fee_per_execution) → uint64` | anyone | Create an upkeep; returns its id. Two payment args fund the box MBR and the escrow. |
| `execute(upkeep_id) → uint64` | anyone (permissionless) | Fire a due, funded upkeep; pays the caller. Returns the next due round. |
| `top_up(upkeep_id, funding_payment) → uint64` | anyone | Add escrow; returns new balance. |
| `cancel(upkeep_id) → uint64` | creator only | Delete the upkeep; refunds remaining escrow **plus the box MBR** the deletion releases. Returns the refunded amount. |

Constraints (asserted on-chain):

- `interval_rounds ≥ 10`, `fee_per_execution ≥ 4_000` µALGO, call data
  `0 < len ≤ 1_024` bytes.
- Executions are NoOp inner app calls with exactly one app arg (the stored
  call data — typically the target method's 4-byte selector) and no foreign
  arrays.
- Scheduling is interval-based from the *scheduled* round:
  `next_due += interval` on each execution. An upkeep missed for many
  intervals stays due until it has caught up — there is no wall-clock clamp.

## Box encoding (Upkeep struct)

ARC-4 head/tail tuple encoding of:

```
creator: Address | target_app: uint64 | call_data: DynamicBytes
interval_rounds: uint64 | next_execution_round: uint64
fee_per_execution: uint64 | balance: uint64 | times_executed: uint64
```

| Bytes | Field |
|-------|-------|
| `[0:32]` | creator address |
| `[32:40]` | target app id |
| `[40:42]` | offset to the call_data tail (currently 82) |
| `[42:50]` | interval_rounds |
| `[50:58]` | next_execution_round |
| `[58:66]` | fee_per_execution |
| `[66:74]` | balance |
| `[74:82]` | times_executed |
| `[82:]` | tail: `uint16 length` + call data |

Reference decoder: `scripts/keeper_bot.py::_decode_upkeep`; regression vector:
`tests/test_keeper_bot.py`.

## Economics

- Creator costs, per upkeep: box MBR `2_500 + 400 × (93 + len(call_data))`
  µALGO (41,300 for a 4-byte selector) + escrowed `funding`. Both come back
  on `cancel`, so registering an upkeep costs only transaction fees in the
  end.
- Keeper costs, per execution: 1,000 µALGO outer fee + 2,000 µALGO
  `extra_fee` covering the two inner transactions (fee pooling). Paid fee is
  `fee_per_execution` (≥ 4,000), so net ≥ 1,000 µALGO per execution.
- An upkeep is executable while `balance ≥ fee_per_execution`; it goes
  dormant when underfunded and resumes after a `top_up`.

## Operating a bot

```bash
poetry run python -m scripts.keeper_bot            # loop, block-by-block
poetry run python -m scripts.keeper_bot --once     # single scan (cron)
poetry run python -m scripts.keeper_bot --app-id N # other keeper instance
```

- Signs as `KEEPER_MNEMONIC`, else `DEPLOYER_MNEMONIC`; fees are paid to that
  account, and it pays the outer fees — keep it funded.
- Multiple competing bots are safe: the contract re-checks due-ness
  atomically, so exactly one keeper is paid per due round. What the *loser*
  pays is not yet established — Algorand rejects failing transactions at
  validation rather than committing them, which suggests a lost race costs
  nothing on-chain, unlike an EVM revert. Unverified; see issue #13.
- A failing upkeep (e.g. target rejects the call) is skipped for the rest of
  the bot run to avoid burning fees every round.

## Testing and CI

```bash
fledge lanes run ci       # build → unit tests → spec-sync strict check
fledge lanes run local    # ci + LocalNet e2e smoke
```

- Unit tests (`tests/`) use `algorand-python-testing` mocks. Note the mocks
  record but do not *execute* inner app calls — the Pulse counter increment
  was proven with the TestNet e2e (`scripts/keeper_testnet_demo.py`).
- Specs (`specs/keeper/`, `specs/pulse/`) are enforced by
  `specsync check --strict`; update them with any contract surface change.

## Capability boundary

Archon is the clock, not the eyes. It schedules on-chain calls; it cannot
observe the world.

- No off-chain access. Contracts have no network access, and `execute` fires an
  inner app call to another app on the same chain.
- No keeper discretion. `call_data` is fixed at registration, so a keeper
  replays exactly what the creator specified. This is what makes keepers
  trustless, and it is also why no keeper can inject fresh data.

**Oracle pairing** is the supported answer for data-driven automation: a
reporter pushes values into an oracle contract, an Archon upkeep triggers
`settle()` on a cadence, and settlement reads the stored value. Archon supplies
the timing guarantee — that settlement cannot be stalled, delayed or
selectively timed by an interested party — not the data.

One case needs no oracle trust at all: a **staleness check** that compares the
feed's last-updated round against the current round and flags the feed if it
has gone quiet. Comparing timestamps cannot be lied to.

## Building on Archon

The v1 hook shape is a NoOp ABI method taking no arguments of its own, called
with just its selector. Two properties matter when writing one:

- **It is called on every cadence**, whether or not there is work to do. The
  no-op path must be cheap.
- **A hook that fails trips keeper backoff** and stops being serviced. Fail
  soft — record the condition in state rather than throwing.

**The pull-payment pattern.** Scheduled calls should do accounting only and let
counterparties claim in transactions they send themselves:

```
scheduled_call()  # zero-arg, Archon calls this. Records allocations, moves nothing.
claim()           # the recipient calls this and pulls their funds.
```

This sidesteps resource availability — `Txn.sender` is always available to a
contract, whereas an arbitrary payout address may not be — and it isolates
failure: a payout to a closed or hostile account fails that claim alone instead
of failing the whole execution and disrupting the schedule. Most applications
that look like they need multi-arg calls do not, once payouts are pull-based.

## Known limitations (v1)

- ALGO escrow only (no ASA-denominated fees yet). First candidate: CORVID,
  CorvidLabs' ASA — mainnet asset
  [`3225439167`](https://explorer.perawallet.app/asset/3225439167) (6 decimals).
- Single-arg NoOp call shape; no multi-arg or foreign-array calls.
- No catch-up clamp: long-missed upkeeps fire once per round until caught up.
- Unaudited. TestNet throwaway deployer — redeploy fresh for mainnet.
