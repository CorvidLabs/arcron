# AGENTS.md

Guidance for AI agents working in this repo. Also see `README.md`.

## What this is

Algorand smart contracts (Algorand Python / Puya + AlgoKit). Main project:
`smart_contracts/keeper/` — a permissionless keeper network, live on TestNet
(app 769772891). `pulse/` is its demo target. `corvid_vault/` is a parked
experiment (don't extend it without being asked).

## Commands

- Everything: `fledge lanes run ci` (build + unit tests + spec check)
- Build: `poetry run python -m smart_contracts build` (rebuilds artifacts and typed clients — always rebuild after contract changes)
- Test: `poetry run pytest tests/ -q`
- Specs: `specsync check --strict`
- Keeper bot: `poetry run python -m scripts.keeper_bot [--once]` (signs as KEEPER_MNEMONIC, else DEPLOYER_MNEMONIC)
- Python env: Poetry (`.venv` in project). Python 3.13 — do NOT use 3.14 (coincurve has no wheels).

## Conventions

- Every contract has a spec-sync spec in `specs/<name>/` (strict mode). Update the spec's Public API tables, requirements, testing.md and Change Log whenever the contract's surface changes; `specsync check --strict` must stay green.
- Tests use `algorand-python-testing` mocks. Two mock limits to know: it records but does not *execute* inner app calls (prove those on TestNet/LocalNet instead), and its `UInt64()` accepts only plain `int`.
- Contracts requiring inner-txn fees rely on group fee pooling — callers add extra fee (see smoke/demo scripts).
- On TestNet, disable the suggested-params cache (`set_suggested_params_cache_timeout(0)`) and fund the app account's base MBR (0.1 ALGO) before it can escrow or hold boxes.
- Upkeep box values are ARC-4 head/tail encoded (32-byte creator, static fields inline, dynamic `call_data` in the tail via the offset at bytes [40:42]). `scripts/keeper_bot.py` has the reference decoder.

## Secrets

- `.env.*` files are gitignored and must stay that way. The TestNet deployer mnemonic in `.env.testnet` is a throwaway — never commit real mnemonics, never reuse it on mainnet.
