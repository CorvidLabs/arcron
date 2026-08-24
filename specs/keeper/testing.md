---
spec: keeper.spec.md
---

## Automated Testing

| Test File | Type | What It Covers |
|-----------|------|----------------|
| `tests/test_keeper.py` | unit (`algorand-python-testing`) | register happy path + id sequencing, low interval/fee/funding/MBR, empty/oversize call data, box MBR charged vs the real encoded box size, execute happy path (inner call args, payment, rescheduling), not-due, insufficient funding, unknown id, top_up, cancel + refund (escrow + box MBR) + double-cancel |
| `scripts/keeper_e2e.py` | e2e (LocalNet, and TestNet with `--network testnet`) | the real-AVM behaviour mocks cannot show: the inner call firing and moving Pulse's state, a *stranger* being paid from escrow atomically, bot decoder parity against real box bytes, the bot executing a due upkeep, top-up, creator-only cancel with refund, not-due / insufficient-funding rejections, app-account solvency, and a fresh base-MBR-only app paying out its last execution |
| `tests/test_keeper_bot.py` | unit | `_decode_upkeep` against recorded box bytes |

Fixtures: `context` (fresh mock context), `keeper`, `pulse` (contract instances); rounds controlled via `patch_global_fields`.

Mocks record inner transactions but never execute them, and they do not enforce
minimum balances — anything that depends on the AVM actually running the inner
call, or on the app account's spendable balance, belongs in `keeper_e2e.py`.

## Manual Testing

- [ ] `fledge lanes run local` — CI plus the LocalNet e2e (needs `algokit localnet start`)
- [ ] `poetry run python -m scripts.keeper_e2e --network localnet` — the e2e on its own
- [ ] `poetry run python -m scripts.keeper_e2e --network testnet` — same flow against TestNet
- [ ] `poetry run python -m scripts.keeper_bot --once --network localnet --app-id <id>` — one bot scan

## Edge Cases & Boundary Conditions

| Scenario | Expected Behavior |
|----------|-------------------|
| Execute in exactly the due round | Allowed (`>=`) |
| Execute twice in one window | Second fails ("Not due") |
| Escrow exactly one fee | One execution, then "Insufficient funding" |
| Target app rejects the call | Group fails; keeper unpaid; state unchanged |
| Cancel an empty escrow | Box deleted, box MBR refunded to the creator |
| App account holding only its base MBR | Every registered upkeep's escrow stays spendable; the last execution still pays |
| Upkeep id that never existed | "Upkeep not found" |
