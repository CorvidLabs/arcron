# CLAUDE.md

Guidance for Claude Code in this repo. See `AGENTS.md` and `README.md` for the
full picture — keep all three consistent when conventions change.

## What this is

Algorand smart contracts (Algorand Python / Puya + AlgoKit). Main project:
`smart_contracts/keeper/` — a permissionless keeper network, live on TestNet
(app 769772891). `pulse/` is its demo target (app 769772906).
`corvid_vault/` is a parked experiment — don't extend it without being asked.

## Commands

- Everything: `fledge lanes run ci` (build + unit tests + spec check — must stay green)
- Build: `poetry run python -m smart_contracts build` (always rebuild after contract changes)
- Test: `poetry run pytest tests/ -q`
- Specs: `specsync check --strict`
- Keeper bot: `poetry run python -m scripts.keeper_bot [--once]`

## Rules

- Poetry venv, Python 3.13 — never 3.14 (coincurve has no wheels).
- puyapy stays pinned `>=5.0,<5.10` to match algorand-python 3.5.x.
- Every contract has a strict spec-sync spec in `specs/<name>/`; update the
  spec's Public API tables, requirements, testing.md and Change Log whenever
  the contract surface changes.
- Tests use `algorand-python-testing` mocks: inner app calls are recorded,
  not executed (prove those on TestNet/LocalNet); `UInt64()` takes plain `int` only.
- On TestNet: disable the suggested-params cache
  (`set_suggested_params_cache_timeout(0)`) and fund the app account's base
  MBR (0.1 ALGO) before it can escrow or hold boxes.
- Upkeep box values are ARC-4 head/tail encoded; `scripts/keeper_bot.py` has
  the reference decoder (`_decode_upkeep`).
- `.env.*` files are gitignored and must stay that way. Never commit mnemonics;
  the TestNet deployer is a throwaway — never reuse it on mainnet.
