---
spec: vault.spec.md
---

## Tasks

- [ ] Deploy to TestNet against the real CORVID ASA (`CORVID_ASSET_ID` + `DEPLOYER_MNEMONIC`)
- [ ] Owner `skim` method to collect fee/MBR surplus above `total_staked`

## Gaps

- No unit test for the fee-pooling requirement on inner transactions (mock AVM does not enforce fees); covered by LocalNet e2e only.
- No multi-account unit tests (second user depositing/withdrawing independently).
- Inbox enumeration (listing a recipient's boxes) is client-side only; no on-chain or client helper yet.
- Group key distribution for multi-member group messaging is out of scope for the contract; pairwise AlgoChat only.

## Review Sign-offs

- **Product**: pending
- **QA**: pending
- **Design**: n/a
- **Dev**: pending
