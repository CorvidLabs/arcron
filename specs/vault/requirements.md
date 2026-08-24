---
spec: vault.spec.md
---

## User Stories

- As a CORVID holder, I want to deposit CORVID into the vault so my balance is tracked on-chain and I can withdraw it later.
- As a CORVID holder, I want to withdraw any amount up to my recorded balance at any time, without permission from anyone.
- As a maintainer, I want the CORVID ASA bound exactly once at bootstrap so the vault can never be repurposed to a different asset.

## Acceptance Criteria

### REQ-vault-001
The vault SHALL be bootstrapped exactly once: an MBR payment of at least
200000 µALGO to the app account funds it, the CORVID ASA id is stored in
global state, and the app opts itself into the ASA via inner transaction.
Any later bootstrap attempt SHALL fail.

### REQ-vault-002
The vault SHALL accept deposits only as a grouped asset transfer of the
CORVID ASA whose receiver is the app account, and SHALL credit the sender's
local balance by the transferred amount.

### REQ-vault-003
The vault SHALL track each opted-in user's balance in per-account local
state, initialized to 0 by `opt_in_to_application`.

### REQ-vault-004
The vault SHALL reject any withdrawal exceeding the caller's recorded
balance, and SHALL pay out accepted withdrawals to the caller via inner
asset transfer.

### REQ-vault-005
The vault SHALL expose a read-only `vault_balance` method returning the
caller's balance, defaulting to 0 for non-opted-in callers.

### REQ-vault-006
The vault SHALL act as a message operator: any staked member (balance > 0)
SHALL be able to relay an opaque sealed envelope (1–1024 bytes) to any
recipient's on-chain inbox; non-members SHALL be rejected. The contract
SHALL NOT require, read, or be able to produce message plaintext.

### REQ-vault-007
Each relayed message SHALL charge at least MESSAGE_FEE (100000 base units)
of the vault asset to the app account, and the sender SHALL fund the exact
box MBR created by the message.

### REQ-vault-008
Inboxes SHALL be per-recipient append-only box sequences readable via free
algod queries; only the inbox owner SHALL be able to delete a message,
freeing its box MBR to the app account.

## Constraints

- Built with Algorand Python (Puya) as an ARC-4 contract; compiled artifacts live in `smart_contracts/artifacts/`.
- Methods that submit inner transactions (`bootstrap`, `withdraw`) require the caller to cover the inner fee via fee pooling (1000 µALGO extra).
- Unit tests use `algorand-python-testing`; the full flow is verified on AlgoKit LocalNet.

## Out of Scope

- Yield, rewards, or time-lock logic (potential future change).
- Box-based balance storage (would remove the opt-in requirement).
- TestNet/MainNet deployment parameters (handled via `CORVID_ASSET_ID` env var, not by the contract).
