---
spec: vault.spec.md
---

## Key Decisions

- **Local state over boxes**: balances live in per-account local state, so users must opt in before depositing. Chosen for v1 simplicity; boxes (no opt-in UX, but MBR-funding complexity) are a possible later change.
- **Bootstrap pattern**: the app is created bare and configured exactly once via `bootstrap`, which atomically verifies the MBR payment, stores the ASA id, and opts the app into the ASA. This avoids a create-time chicken-and-egg with funding the app account.
- **Fee pooling for inner transactions**: inner transactions carry fee 0; callers of `bootstrap`/`withdraw` add 1000 µALGO extra fee. Keeps the app account from draining on fees.
- **Boxes as inboxes, not note fields**: relayed envelopes live in per-message boxes (`"m"‖recipient‖index`) plus a per-recipient counter box (`"i"‖recipient`). Box reads are free algod queries (no transaction, no fee) and boxes are deletable by the recipient to reclaim MBR — soft-delete, unlike immutable note-field memos. Indices are append-only so deletions never shift numbering.
- **Sender funds box MBR; freed MBR stays with the app**: `post` requires an MBR payment covering exactly the boxes it creates; `delete_message` releases MBR to the app account, not the original payer. Combined with MESSAGE_FEE this makes the operator self-funding.
- **Fees accumulate as surplus**: `total_staked` tracks the sum of stakes; app holdings above it are fees/MBR surplus. A future owner `skim` method could collect it — deliberately not built yet.
- **Contract is crypto-agnostic**: envelopes are opaque bytes; sealing (AlgoChat X25519/ChaCha20-Poly1305) happens client-side. The contract can never read message contents — by design, not by limitation.
- **Mock CORVID on LocalNet**: deploy creates a mock ASA unless `CORVID_ASSET_ID` is set; the deploy returns the ASA id actually bound in global state so repeat deploys stay consistent.

## Files to Read First

- `smart_contracts/corvid_vault/contract.py` — the contract itself
- `specs/vault/vault.spec.md` — the module contract this file companions
- `smart_contracts/corvid_vault/deploy_config.py` — deploy + bootstrap orchestration
- `tests/test_corvid_vault.py` — unit-test expectations of the ABI surface

## Current Status

- Implemented and verified: vault (bootstrap, opt-in, deposit, withdraw, vault_balance) and operator relay (post, message_count, delete_message).
- 18 unit tests pass (`algorand-python-testing`); LocalNet smoke passes end-to-end, including a real AlgoChat envelope round-trip (seal → post → box read → decrypt → delete).
- Toolchain aligned: puyapy 5.9 with algorand-python 3.5. `py-algochat` is a local path dependency (`../../py-algochat`) — it is not yet published on PyPI.
- No known blockers. Not yet deployed to TestNet.

## Notes

- Typed client is generated from `CorvidVault.arc56.json` by `python -m smart_contracts build`; regenerate after any ABI change.
