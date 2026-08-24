---
spec: keeper.spec.md
---

## Automated Testing

| Test File | Type | What It Covers |
|-----------|------|----------------|
| `tests/test_keeper.py` | unit (`algorand-python-testing`) | register happy path + id sequencing, low interval/fee/funding, empty/oversize call data, execute happy path (inner call args, payment, rescheduling), not-due, insufficient funding, unknown id, top_up, cancel + refund + double-cancel |
| `scripts/keeper_testnet_demo.py` | e2e (TestNet) | deploy, register, wait for due round, execute, verify Pulse beats incremented on-chain |

Fixtures: `context` (fresh mock context), `keeper`, `pulse` (contract instances); rounds controlled via `patch_global_fields`.

## Manual Testing

- [ ] `poetry run python -m scripts.keeper_testnet_demo` — full TestNet flow with dispenser funding

## Edge Cases & Boundary Conditions

| Scenario | Expected Behavior |
|----------|-------------------|
| Execute in exactly the due round | Allowed (`>=`) |
| Execute twice in one window | Second fails ("Not due") |
| Escrow exactly one fee | One execution, then "Insufficient funding" |
| Target app rejects the call | Group fails; keeper unpaid; state unchanged |
| Cancel an empty escrow | Box deleted, no refund payment issued |
| Upkeep id that never existed | "Upkeep not found" |
