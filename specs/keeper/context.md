---
spec: keeper.spec.md
---

## Key Decisions

- **Inner app calls, not just incentives**: the contract doesn't merely pay keepers to poke things — it performs the registered call itself as an inner transaction. That makes execution atomic: fee is paid only if the upkeep actually ran.
- **One app arg, no foreign arrays**: registered calls carry a single stored blob (typically a 4-byte selector). Covers the dominant "tick/settle/harvest" hook shape without storing dynamic foreign arrays.
- **ALGO escrow, no protocol rake**: v1 is currency-simple and ownerless. Every µALGO escrowed goes to keepers or back to the creator; the only value the app keeps is box MBR.
- **State before inner calls**: round/balance/times update before the inner app call, so a target calling back can never double-execute a window.
- **Box MBR paid by registrant, stays with app on cancel**: consistent with the vault inbox design; the escrow refund covers only the tracked balance.

## Files to Read First

- `smart_contracts/keeper/contract.py` — the network itself
- `specs/keeper/keeper.spec.md` — the module contract
- `smart_contracts/pulse/contract.py` — the demo target used everywhere
- `tests/test_keeper.py` — expected behavior, including round control via the mock

## Current Status

- Implemented and unit-tested: register (+5 validation paths), execute (happy path, not-due, insufficient funding, unknown id), top_up, cancel (+refund, double-cancel).
- The mock AVM records but does not execute inner app calls — Pulse counter increments are verified on TestNet instead.
- **Live on TestNet**: Keeper app `769772891`, Pulse app `769772906` (deployer `E5M2OH5XNDMNABJ6VOFOUVR2IKRPCGQH43PVC5P3DWQQ2LV2VJV2FJZQ3E`). Demo executed upkeep id 4 against Pulse: `Pulse.beats = 1` verified on-chain (round 66610411). Leftover demo upkeeps 0–3 from failed iterations still hold escrow (~0.08 ALGO) and can be cancelled by the deployer to reclaim it.

## Notes

- Keepers cover inner txn fees via group fee pooling (~2,000 µALGO on top of the outer 1,000), which is why MIN_UPKEEP_FEE is 4,000.
- Public TestNet endpoints are slow: algokit-utils' suggested-params cache can build transactions that expire before simulate/broadcast. Both deploy configs and the demo disable the cache (`set_suggested_params_cache_timeout(0)`); the demo also pins explicit validity rounds on payment args.
