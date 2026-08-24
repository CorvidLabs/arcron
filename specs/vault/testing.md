---
spec: vault.spec.md
---

## Automated Testing

| Test File | Type | What It Covers |
|-----------|------|----------------|
| `tests/test_corvid_vault.py` | unit (`algorand-python-testing`) | bootstrap once/underfunded, deposit happy path + wrong asset + wrong receiver, withdraw + overdraw, vault_balance default, total_staked tracking, post happy path + non-member + low/wrong fee + low MBR + oversize, delete_message + missing message, message_count default |
| `scripts/smoke_localnet.py` | e2e (LocalNet) | deploy, opt-in, deposit, withdraw, on-chain balance agreement, AlgoChat-sealed envelope post → box read → decrypt → delete round-trip |

Fixtures: `context` (fresh `algopy_testing_context`), `contract` (fresh `CorvidVault` instance per test).

## Manual Testing

- [x] `algokit project deploy localnet` — deploys and bootstraps (app 1003, ASA 1002)
- [x] `poetry run python -m scripts.smoke_localnet` — full user flow on LocalNet

## Edge Cases & Boundary Conditions

| Scenario | Expected Behavior |
|----------|-------------------|
| Second bootstrap call | Rejected ("Already bootstrapped") |
| Deposit of a different ASA | Rejected ("Wrong asset") |
| Deposit paying an account other than the app | Rejected ("Deposit must go to the app account") |
| Withdraw of exactly the full balance | Allowed; balance becomes 0 |
| Withdraw of balance + 1 | Rejected ("Insufficient balance") |
| Balance read before opt-in | Returns 0 (readonly default) |
| Inner-txn fee not covered by caller | Group fails on-chain (fee pooling); covered in e2e, not unit tests |
| Post to a recipient who never received mail | Counter box + message box both created; MBR covers both |
| Post after prior messages were deleted | Index continues from counter (no reuse) |
| Delete from another user's inbox | Rejected ("Message not found") — key is derived from caller |
| Envelope of exactly 1024 bytes | Accepted; 1025 rejected |
