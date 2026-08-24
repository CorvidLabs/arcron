---
spec: pulse.spec.md
---

## Key Decisions

- Zero-arg `tick` (selector-only call): matches the Keeper contract's single-app-arg call shape exactly.

## Files to Read First

- `smart_contracts/pulse/contract.py`
- `smart_contracts/keeper/contract.py` (the consumer)

## Current Status

- Implemented; unit-tested indirectly via keeper tests; on-chain increment verified on TestNet via the keeper demo.

## Notes

- `beats` is readable for free from global state by any client.
