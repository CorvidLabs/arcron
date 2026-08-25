---
spec: keeper.spec.md
---

## Automated Testing

| Test File | Type | What It Covers |
|-----------|------|----------------|
| `tests/test_keeper.py` | unit (`algorand-python-testing`) | register happy path + id sequencing, low interval/fee/funding/MBR, fee and cap above maximum, unknown policy, cap below fee, empty/oversize call data, box MBR charged vs the real encoded box size, execute happy path (inner call args, payment, rescheduling), not-due, insufficient funding, unknown id, top_up, cancel + refund (escrow + box MBR) + double-cancel; `CATCH_UP` replaying every missed interval and `SKIP_AHEAD` running once on the schedule's phase; the escalation curve at six points; a twenty-interval burst paying the ceiling once and base thereafter; escalation raising the dormancy threshold; `last_serviced_round` recording the round it ran in |
| `scripts/keeper_e2e.py` | e2e (LocalNet, and TestNet with `--network testnet`) | 20 stages of real-AVM behaviour mocks cannot show: the inner call firing and moving Pulse's state, a *stranger* being paid from escrow atomically, bot decoder parity against real box bytes, the bot executing a due upkeep, top-up, creator-only cancel with refund, not-due / insufficient-funding rejections, app-account solvency, a fresh base-MBR-only app paying out its last execution, `CATCH_UP` replaying a missed window and `SKIP_AHEAD` clearing one in a single run on the schedule's phase, an escalated execution paying the ceiling with the replay behind it paying base (with the bot's `effective_fee` checked against every fee the contract actually charged), and the bot ranking a neglected minimum-fee upkeep above a richer one |
| `tests/test_keeper_bot.py` | unit | `_decode_upkeep` against recorded box bytes, the recorded box's length against the MBR formula, and `effective_fee` against the contract's curve |
| `js/test/upkeep.test.ts` | unit (`bun test`) | the TypeScript decoder and `effectiveFee` against the *same* recorded box bytes, so the two decoders cannot drift |

Fixtures: `context` (fresh mock context), `keeper`, `pulse` (contract instances); rounds controlled via `patch_global_fields`.

Mocks record inner transactions but never execute them, and they do not enforce
minimum balances. Anything that depends on the AVM actually running the inner
call, or on the app account's spendable balance, belongs in `keeper_e2e.py`.

## Manual Testing

- [ ] `fledge lanes run local` (CI plus the LocalNet e2e; needs `algokit localnet start`)
- [ ] `poetry run python -m scripts.keeper_e2e --network localnet` (the e2e on its own)
- [ ] `poetry run python -m scripts.keeper_e2e --network testnet` (same flow against TestNet)
- [ ] `poetry run python -m scripts.keeper_bot --once --network localnet --app-id <id>` (one bot scan)

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
| A `SKIP_AHEAD` upkeep executed exactly on its due round | Behaves like `CATCH_UP`: nothing was missed, so the next slot is one interval on |
| Escalation on an upkeep executed the moment it became due | No escalation: one interval since the last service is on time, not late |
| A ceiling equal to the base fee | No escalation; the same as a zero ceiling |
| A ceiling set, escrow between the base fee and the ceiling | Executable while on time, dormant once late |
