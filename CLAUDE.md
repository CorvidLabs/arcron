# CLAUDE.md

Guidance for Claude Code in this repo. See `AGENTS.md` and `README.md` for the
full picture — keep all three consistent when conventions change.

## What this is

Archon — Algorand smart contracts (Algorand Python / Puya + AlgoKit). Main
project: `smart_contracts/keeper/` — a permissionless keeper network, live on
TestNet (app 769802474; 769772891 is its deprecated, pre-box-MBR-fix
predecessor). `pulse/` is its demo target (app 769772906).

## Commands

- Everything: `fledge lanes run ci` (build + unit tests + spec check — must stay green)
- On a real chain: `fledge lanes run local` (ci + the keeper e2e; needs `algokit localnet start`)
- Endurance: `fledge lanes run endurance` (adds `scripts/keeper_soak.py`, ~3 min)
- Console: `cd web && bun run ng serve`; `bun test` for its unit tests
- Build: `poetry run python -m smart_contracts build` (always rebuild after contract changes)
- Test: `poetry run pytest tests/ -q`
- Specs: `specsync check --strict`
- End-to-end: `poetry run python -m scripts.keeper_e2e --network localnet|testnet`
- Keeper bot: `poetry run python -m scripts.keeper_bot [--once] [--network N] [--app-id N]`

## 1.0 scope — decided 2026-08-24

Full reasoning in [`docs/design/1.0.md`](docs/design/1.0.md). The short version,
because these are easy to get wrong from memory:

- **The contract cannot be upgraded.** A struct change means a new app id and
  every creator cancelling and re-registering by hand. So #7, #14, #8 and #9
  are batched into **one** release and the surface is then frozen.
- **#9 is an ASA fee *capability*, not a commitment.** CORVID is not wired in;
  escrow and fees stay ALGO by default. "No token required" remains true.
- **#15 (staking) and #22 (keeper-supplied data) are out of 1.0** and must not
  be implemented without review. The line between #8 and #22 matters: declaring
  which *resources* a call may touch is safe because the creator still fixes
  what is called; letting a keeper supply *data* is a different thing.
- **Dogfood** is a recurring treasury distribution on TestNet, serviced by a
  keeper we run and watched by the notifier.
- **MainNet gate** is self-review plus sustained TestNet time — no paid audit.
  Any struct change restarts that clock.
- **Public release** waits until the deployment is one we are not about to
  replace; the licence and docs (#50) land before visibility does.

## Rules

- Poetry venv, Python 3.13 — never 3.14 (coincurve has no wheels).
- puyapy stays pinned `>=5.0,<5.10` to match algorand-python 3.5.x.
- Every contract has a strict spec-sync spec in `specs/<name>/`; update the
  spec's Public API tables, requirements, testing.md and Change Log whenever
  the contract surface changes.
- Tests use `algorand-python-testing` mocks: inner app calls are recorded, not
  executed, and minimum balances are not enforced (prove both in
  `scripts/keeper_e2e.py` on LocalNet); `UInt64()` takes plain `int` only.
- Scripts choose their network with `--network` / `ARCHON_NETWORK`
  (`scripts/network.py`); it loads `.env.<network>` and verifies the node's
  genesis id. LocalNet is dev mode — rounds only advance when you send
  transactions (`network.wait_for_round`).
- On TestNet: disable the suggested-params cache
  (`set_suggested_params_cache_timeout(0)`) and fund the app account's base
  MBR (0.1 ALGO) before it can escrow or hold boxes.
- Upkeep box values are ARC-4 head/tail encoded; `scripts/keeper_bot.py` has
  the reference decoder (`_decode_upkeep`), and `web/src/app/core/upkeep.ts`
  is its TypeScript twin — both pinned to the same recorded box.
- `web/` is styled only with the CorvidLabs design system vendored in
  `web/public/brand/`: no hardcoded colours, no hand-rolled theme toggle.
- Wallets come from `@txnlab/use-wallet` (see `web/src/app/core/wallets.ts`),
  following the house pattern: Pera, Defly, Lute, Exodus and Kibisis need no
  configuration; only the generic WalletConnect entry takes a project id, and
  it is added only when one is set.
  Amounts display in ALGO; cadences display as time as well as rounds.
- `.env.*` files are gitignored and must stay that way. Never commit mnemonics;
  the TestNet deployer is a throwaway — never reuse it on mainnet.
