---
spec: keeper.spec.md
---

## Key Decisions

- **Inner app calls, not just incentives**: the contract doesn't merely pay keepers to poke things — it performs the registered call itself as an inner transaction. That makes execution atomic: fee is paid only if the upkeep actually ran.
- **One app arg, no foreign arrays**: registered calls carry a single stored blob (typically a 4-byte selector). Covers the dominant "tick/settle/harvest" hook shape without storing dynamic foreign arrays.
- **ALGO escrow, no protocol rake**: v1 is currency-simple and ownerless. Every µALGO escrowed goes to keepers or back to the creator; the only value the app keeps is box MBR.
- **State before inner calls**: round/balance/times update before the inner app call, so a target calling back can never double-execute a window.
- **Box MBR paid by the registrant, refunded on cancel**: `register` collects exactly what the box costs the app account and `cancel` returns it with the escrow, so the app never holds value that isn't someone's escrow, and an upkeep can always pay its last execution.

## Files to Read First

- `smart_contracts/keeper/contract.py` — the network itself
- `specs/keeper/keeper.spec.md` — the module contract
- `smart_contracts/pulse/contract.py` — the demo target used everywhere
- `tests/test_keeper.py` — expected behavior, including round control via the mock
- `scripts/keeper_e2e.py` — what the mocks can't prove, on a real node

## Current Status

- Implemented and unit-tested: register (+5 validation paths), execute (happy path, not-due, insufficient funding, unknown id), top_up, cancel (+refund, double-cancel).
- The mock AVM records but does not execute inner app calls, and does not enforce minimum balances — both are covered by `scripts/keeper_e2e.py` on LocalNet.
- **Live on TestNet**: Keeper app `769823086`, Pulse app `769823097` (deployer `E5M2OH5XNDMNABJ6VOFOUVR2IKRPCGQH43PVC5P3DWQQ2LV2VJV2FJZQ3E`) — the alpha-1 deployment of the 1.0 contract. The full 20-stage e2e passes against it on-chain, including the box-MBR regression, the losing-keeper measurement, and the escalation-lockout and patient-keeper regressions. Two apps are superseded and must not be used: `769802474` predates the 1.0 struct (its registry is empty), and `769772891` predates the box-MBR fix — its registry has been emptied, and 243,000 µALGO of box MBR is stranded there permanently. The fix exists precisely so that cannot happen again.

## Notes

- Keepers cover inner txn fees via group fee pooling (~2,000 µALGO on top of the outer 1,000), which is why MIN_UPKEEP_FEE is 4,000.
- Public TestNet endpoints are slow: algokit-utils' suggested-params cache can build transactions that expire before simulate/broadcast. Deploy configs and the e2e disable the cache (`set_suggested_params_cache_timeout(0)`); the e2e also pins explicit validity rounds on payment args.
