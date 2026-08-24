---
spec: pulse.spec.md
---

## Automated Testing

| Test File | Type | What It Covers |
|-----------|------|----------------|
| `tests/test_keeper.py` | unit | Uses Pulse as the registration target and asserts the inner call's app id and selector |
| `scripts/keeper_testnet_demo.py` | e2e (TestNet) | Asserts `beats` increments after keeper execution |

## Manual Testing

- [ ] Call `tick` via the keeper on TestNet and read `beats` from global state

## Edge Cases & Boundary Conditions

| Scenario | Expected Behavior |
|----------|-------------------|
| Called directly by an account | Increments (permissionless) |
| Called via keeper inner transaction | Increments identically |
