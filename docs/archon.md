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

## Liveness: who executes, and what happens when nobody does

Archon does not execute itself. There is no on-chain timer on Algorand, so
every execution in this repo is a transaction some account sent and paid for.
The contract is passive: `execute` is an external entry point that anyone may
call once an upkeep is due.

That makes liveness an operational property, not a contract property:

| Question | Answer |
|----------|--------|
| What makes an upkeep run on time? | A keeper watching the registry and calling `execute` when it comes due. |
| What if no keeper is watching? | Nothing runs. The upkeep accrues as "due" and waits, indefinitely. |
| Is anything lost meanwhile? | No. Scheduling counts from the scheduled round, so it catches up one interval per execution. |
| What does a missed window cost the creator? | Nothing directly — escrow is only spent on real executions. |
| Who pays for liveness? | The upkeep's creator, via `fee_per_execution`; keepers self-select on whether that is worth their time. |

**Current coverage:** no always-on keeper runs against the TestNet deployment.
Upkeeps there are executed when someone runs the bot by hand. Do not present
TestNet as a live service until issue #2 is done.

**Detecting a stall from outside:** the registry is public box state, so any
observer can compute liveness without trusting a keeper — scan the boxes and
compare `next_execution_round` against the current round. An upkeep overdue by
more than an interval or two means no keeper is servicing that app.

**Sizing escrow against it:** 100 ALGO at the 4,000 µALGO minimum funds about
25,000 executions — roughly 68 years of a daily upkeep, or 8 months of one
that fires every 100 rounds. Escrow depth is almost never what stops an
upkeep; the absence of a keeper is.

## Operating a bot

```bash
poetry run python -m scripts.keeper_bot            # loop, block-by-block
poetry run python -m scripts.keeper_bot --once     # single scan (cron)
poetry run python -m scripts.keeper_bot --app-id N # other keeper instance
```

- Signs as `KEEPER_MNEMONIC`, else `DEPLOYER_MNEMONIC`; fees are paid to that
  account, and it pays the outer fees — keep it funded.
- Multiple competing bots are safe, and losing a race is **free**. The
  contract re-checks due-ness atomically, and Algorand rejects a failing
  transaction at validation — it never enters a block, so its sender pays no
  fee. Measured, not assumed: `scripts/keeper_e2e.py` stage 14 broadcasts a
  losing `execute` and an `execute` against a rejecting target, and asserts
  the keeper's balance is unchanged in both cases. algod answers
  `TransactionPool.Remember: … logic eval error` and the transaction is
  discarded.
- This is the opposite of EVM chains, where a reverted transaction still burns
  gas. It means the barrier to running a keeper is low: a bot that loses every
  race it enters is out nothing but local compute and a round-trip to algod.
- A failing upkeep (e.g. a target that rejects the call) is still skipped for
  the rest of the bot run, but for latency and pool hygiene rather than cost —
  there is no fee to burn.

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

## Known limitations (v1)

- ALGO escrow only (no ASA-denominated fees yet). First candidate: CORVID,
  CorvidLabs' ASA — mainnet asset
  [`3225439167`](https://explorer.perawallet.app/asset/3225439167) (6 decimals).
- Single-arg NoOp call shape; no multi-arg or foreign-array calls.
- No catch-up clamp: long-missed upkeeps fire once per round until caught up.
- Unaudited. TestNet throwaway deployer — redeploy fresh for mainnet.
