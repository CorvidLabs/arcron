# AGENTS.md

Guidance for AI agents working in this repo. Also see `README.md`.

## What this is

Archon — Algorand smart contracts (Algorand Python / Puya + AlgoKit). Main
project: `smart_contracts/keeper/` — a permissionless keeper network, live on
TestNet (app 769772891 — note it predates the box-MBR fix; see the Change Log
in `specs/keeper/keeper.spec.md`). `pulse/` is its demo target.

## Commands

- Everything: `fledge lanes run ci` (build + unit tests + spec check)
- Everything, on a real chain: `fledge lanes run local` (ci + the keeper e2e; needs `algokit localnet start`)
- Build: `poetry run python -m smart_contracts build` (rebuilds artifacts and typed clients — always rebuild after contract changes)
- Test: `poetry run pytest tests/ -q`
- Specs: `specsync check --strict`
- End-to-end: `poetry run python -m scripts.keeper_e2e --network localnet|testnet`
- Keeper bot: `poetry run python -m scripts.keeper_bot [--once] [--network N] [--app-id N]` (signs as KEEPER_MNEMONIC, else DEPLOYER_MNEMONIC)
- Python env: Poetry (`.venv` in project). Python 3.13 — do NOT use 3.14 (coincurve has no wheels).

## Conventions

- Every contract has a spec-sync spec in `specs/<name>/` (strict mode). Update the spec's Public API tables, requirements, testing.md and Change Log whenever the contract's surface changes; `specsync check --strict` must stay green.
- Every script picks its network with `--network` / `ARCHON_NETWORK` (`scripts/network.py`), which loads `.env.<network>` and then verifies the node's genesis id. LocalNet needs no mnemonics — accounts come from KMD.
- Tests use `algorand-python-testing` mocks. Three mock limits to know: it records but does not *execute* inner app calls, it does not enforce minimum balances (an app that cannot pay looks fine), and its `UInt64()` accepts only plain `int`. Anything depending on those belongs in `scripts/keeper_e2e.py`.
- LocalNet runs algod in dev mode: no blocks are produced on their own. Use `scripts/network.py::wait_for_round`, which pokes the chain with self-payments.
- Contracts requiring inner-txn fees rely on group fee pooling — callers add extra fee (see `scripts/keeper_e2e.py`).
- On TestNet, disable the suggested-params cache (`set_suggested_params_cache_timeout(0)`) and fund the app account's base MBR (0.1 ALGO) before it can escrow or hold boxes.
- Upkeep box values are ARC-4 head/tail encoded (32-byte creator, static fields inline, dynamic `call_data` in the tail via the offset at bytes [40:42]). `scripts/keeper_bot.py` has the reference decoder.

## Secrets

- `.env.*` files are gitignored and must stay that way. The TestNet deployer mnemonic in `.env.testnet` is a throwaway — never commit real mnemonics, never reuse it on mainnet.
