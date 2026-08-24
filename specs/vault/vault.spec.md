---
module: vault
version: 2
status: active
files:
  - smart_contracts/corvid_vault/contract.py

db_tables: []
depends_on: []
---

# Vault

## Purpose

CorvidVault is an ARC-4 smart contract (Algorand Python / Puya) with two roles:

1. **Vault**: custodies the CORVID ASA. Users opt in, deposit CORVID via a
   grouped asset transfer, and withdraw up to their recorded balance anytime.
2. **Operator**: relays sealed envelopes (ciphertext encrypted off-chain,
   e.g. AlgoChat) from staked members to any recipient's on-chain inbox. The
   contract never reads message contents; it enforces membership, charges a
   CORVID fee per message, and stores envelopes in boxes keyed by recipient.

The contract is token-agnostic: any group can deploy it bound to their own
ASA, making it a reusable "stake-to-post encrypted relay" for any community.

## Public API

The module exports no top-level functions; its surface is the `CorvidVault`
contract class and its constants.

### Exported Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `BOOTSTRAP_MBR` | `200_000` | µALGO the bootstrap payment must cover: app account MBR (0.1 ALGO) + one ASA opt-in MBR (0.1 ALGO). |
| `MESSAGE_FEE` | `100_000` | Vault-asset base units (0.1 CORVID at 6 decimals) charged per relayed message; paid to the app account. |
| `MAX_MESSAGE_SIZE` | `1024` | Maximum sealed-envelope size accepted by `post`, in bytes. |

### Exported Types

| Type | Description |
|------|-------------|
| `CorvidVault` | ARC-4 contract class; global state `asset_id: uint64`, `total_staked: uint64`; local state `balance: uint64` per account; boxes for inboxes (see below). |

#### CorvidVault Methods

| Method | Parameters | Returns | Description |
|--------|-----------|---------|-------------|
| `bootstrap` | `mbr_payment: pay, asset: uint64` | `void` | One-time setup: verifies the MBR payment funds the app account, stores the CORVID ASA id, and opts the app into the ASA via inner transaction. |
| `opt_in_to_application` | — | `void` | Opts the caller into the app, initializing their vault balance to 0. |
| `deposit` | `axfer: axfer` | `uint64` | Credits the caller by the amount of a grouped CORVID transfer to the app account; returns the new balance. |
| `withdraw` | `amount: uint64` | `uint64` | Sends CORVID back to the caller via inner transaction; returns the remaining balance. |
| `vault_balance` | — | `uint64` | Read-only view of the caller's current vault balance (0 if not opted in). |
| `post` | `mbr_payment: pay, fee_payment: axfer, recipient: address, ciphertext: byte[]` | `uint64` | Relays a sealed envelope into the recipient's inbox; caller must be staked, fee must be ≥ `MESSAGE_FEE` in the vault asset, MBR payment must cover new boxes; returns the inbox index. |
| `message_count` | `recipient: address` | `uint64` | Read-only count of envelopes relayed to a recipient's inbox. |
| `delete_message` | `index: uint64` | `void` | Deletes the envelope at `index` from the caller's own inbox, freeing its box MBR to the app account. |

#### CorvidVault Boxes

| Box | Key | Value |
|-----|-----|-------|
| Inbox counter | `"i" \|\| recipient address (33 bytes)` | `uint64` count of messages received |
| Message | `"m" \|\| recipient address \|\| index BE64 (41 bytes)` | raw sealed-envelope bytes |

## Invariants

1. `asset_id` is written exactly once, by `bootstrap`; every subsequent bootstrap attempt fails.
2. A caller's local `balance` never decreases by more than itself — withdrawals over the recorded balance always fail.
3. `total_staked` always equals the sum of all local balances; app holdings above `total_staked` are accumulated message fees and box MBR.
4. Only accounts that executed `opt_in_to_application` have a balance entry; reads for non-opted-in accounts return 0.
5. Every accepted deposit is an asset transfer of the CORVID ASA whose receiver is the app account, grouped with the deposit call.
6. Only staked members (`balance > 0`) can `post`; the contract holds no message plaintext and cannot decrypt envelopes.
7. Every accepted `post` pays at least `MESSAGE_FEE` of the vault asset to the app account and funds the exact box MBR it creates.
8. Inbox indices per recipient are append-only and never reused, even after deletions.
9. Only the inbox owner can delete their messages; deletion frees the box MBR to the app account.

## Behavioral Examples

### Scenario: Deposit CORVID

- **Given** the vault is bootstrapped and the caller is opted in with balance 0
- **When** the caller submits a group of a 1000-unit CORVID transfer to the app account plus a `deposit` call
- **Then** the caller's balance is 1000 and the method returns 1000

### Scenario: Withdraw part of the balance

- **Given** the caller has a vault balance of 1000
- **When** the caller invokes `withdraw` with amount 400
- **Then** the app sends 400 CORVID units to the caller via inner transaction, the balance becomes 600, and the method returns 600

### Scenario: Relay a sealed envelope

- **Given** the caller is a staked member and recipient R has never received a message
- **When** the caller posts a 189-byte envelope to R with the CORVID fee and box MBR
- **Then** the envelope is stored in box `"m"‖R‖0`, R's inbox counter becomes 1, and the method returns 0

### Scenario: Read balance without opting in

- **Given** a caller that never opted into the app
- **When** the caller invokes `vault_balance`
- **Then** the method returns 0

## Error Cases

| Condition | Behavior |
|-----------|----------|
| `bootstrap` called twice | Fails with "Already bootstrapped" |
| `bootstrap` MBR payment below 200000 µALGO or not paying the app account | Fails with payment assertion |
| `deposit` before bootstrap | Fails with "Not bootstrapped" |
| `deposit` with a transfer of any other ASA | Fails with "Wrong asset" |
| `deposit` with a transfer whose receiver is not the app account | Fails with "Deposit must go to the app account" |
| `deposit` or `withdraw` with amount 0 | Fails with "Amount must be positive" |
| `withdraw` over the caller's balance | Fails with "Insufficient balance" |
| `deposit` or `withdraw` from a non-opted-in account | Fails (no local state) |
| `post` from an account with zero stake | Fails with "Only staked members can post" |
| `post` fee in the wrong asset / wrong receiver / below `MESSAGE_FEE` | Fails with fee assertions |
| `post` MBR payment below the computed box MBR | Fails with "MBR payment too small" |
| `post` with empty or over-1024-byte ciphertext | Fails with "Ciphertext size out of bounds" |
| `delete_message` for a missing index or someone else's inbox | Fails with "Message not found" |

## Dependencies

### Consumes

| Module | What is used |
|--------|-------------|
| `algopy` (Algorand Python / Puya) | ARC-4 framework, global/local state, `Box`, `gtxn`, `itxn`, `op` primitives |
| `py-algochat` (client-side only, path dep `../../py-algochat`) | X25519/ChaCha20-Poly1305 sealing of envelopes before posting; decryption after reading |

### Consumed By

| Module | What is used |
|--------|-------------|
| `smart_contracts/corvid_vault/deploy_config.py` | Deploys the app, funds MBR, calls `bootstrap` |
| `scripts/smoke_localnet.py` | End-to-end LocalNet flow over the typed client, including a sealed-envelope round-trip |

## Change Log

| Date | Author | Change |
|------|--------|--------|
| 2026-08-23 | CorvidLabs | Initial vault: bootstrap, opt-in, deposit, withdraw, vault_balance |
| 2026-08-23 | CorvidLabs | Operator relay: post, message_count, delete_message; inbox boxes; MESSAGE_FEE, MAX_MESSAGE_SIZE; total_staked tracking |
