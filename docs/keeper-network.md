# Keeper network — technical reference

Hand-off document for the permissionless keeper network. For the quick
overview see `../README.md`; for runnable flows see `../examples/`.

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
| `cancel(upkeep_id)` | creator only | Delete the upkeep; refunds remaining escrow. |

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

- Creator costs, per upkeep: box MBR `2_500 + 400 × (91 + len(call_data))`
  µALGO (locked in the app account, not refunded on cancel in v1) + escrowed
  `funding` (refundable via `cancel`).
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
  atomically; the loser just loses its 1,000 µALGO outer fee.
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
- Specs (`specs/keeper/`, `specs/pulse/`, `specs/vault/`) are enforced by
  `specsync check --strict`; update them with any contract surface change.

## Known limitations (v1)

- ALGO escrow only (no ASA-denominated fees yet).
- Single-arg NoOp call shape; no multi-arg or foreign-array calls.
- Box MBR is not refunded to the creator on cancel (stays in the app account).
- No catch-up clamp: long-missed upkeeps fire once per round until caught up.
- Unaudited. TestNet throwaway deployer — redeploy fresh for mainnet.
