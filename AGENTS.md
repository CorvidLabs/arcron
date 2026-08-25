# AGENTS.md

Guidance for AI agents working in this repo. Also see `README.md`.

## What this is

Arcron — Algorand smart contracts (Algorand Python / Puya + AlgoKit). Main
project: `smart_contracts/keeper/` — a permissionless keeper network, live on
TestNet (app 769802474; 769772891 is its deprecated predecessor, which predates
the box-MBR fix). `pulse/` is its demo target.

## Commands

- Everything: `fledge lanes run ci` (build + unit tests + spec check)
- Everything, on a real chain: `fledge lanes run local` (ci + the keeper e2e; needs `algokit localnet start`)
- Sustained operation: `fledge lanes run endurance` (adds a soak; ~3 min)
- Console: `cd web && bun run ng serve` (LocalNet by default), `bun test` for its unit tests
- Build: `poetry run python -m smart_contracts build` (rebuilds artifacts and typed clients — always rebuild after contract changes)
- Test: `poetry run pytest tests/ -q`
- Specs: `specsync check --strict`
- End-to-end: `poetry run python -m scripts.keeper_e2e --network localnet|testnet`
- Keeper bot: `poetry run python -m scripts.keeper_bot [--once] [--network N] [--app-id N]` (signs as KEEPER_MNEMONIC, else DEPLOYER_MNEMONIC)
- Python env: Poetry (`.venv` in project). Python 3.13 — do NOT use 3.14 (coincurve has no wheels).

## 1.0 scope — decided 2026-08-24

Full reasoning in [`docs/design/1.0.md`](docs/design/1.0.md). The short version,
because these are easy to get wrong from memory:

- **The contract cannot be upgraded.** A struct change means a new app id and
  every creator cancelling and re-registering by hand. So #7, #14, #8 and #9
  are batched into **one** release and the surface is then frozen.
- **#9 is an ASA fee *capability*, not a commitment.** CORVID is not wired in;
  escrow and fees stay ALGO by default. "No token required" remains true.
- **#15 (staking) and #22 (keeper-supplied data) are closed**, not merely
  deferred — reasoning in [`docs/design/out-of-scope.md`](docs/design/out-of-scope.md).
  Staking has nothing to slash; keeper-supplied data inverts the one guarantee
  Arcron makes — and the line between #8 and #22 is the reason: declaring which
  *resources* a call may touch is safe because the creator still fixes what is
  called, while letting a keeper supply *data* is a different product. Do not
  reopen either without reading that first.
- **Dogfood** is a recurring treasury distribution on TestNet, serviced by a
  keeper we run and watched by the notifier.
- **MainNet gate** is self-review plus sustained TestNet time — no paid audit.
  Any struct change restarts that clock.
- **Public release** waits until the deployment is one we are not about to
  replace; the licence and docs (#50) land before visibility does.

## Conventions

- Every contract has a spec-sync spec in `specs/<name>/` (strict mode). Update the spec's Public API tables, requirements, testing.md and Change Log whenever the contract's surface changes; `specsync check --strict` must stay green.
- Every script picks its network with `--network` / `ARCRON_NETWORK` (`scripts/network.py`), which loads `.env.<network>` and then verifies the node's genesis id. LocalNet needs no mnemonics — accounts come from KMD.
- Tests use `algorand-python-testing` mocks. Three mock limits to know: it records but does not *execute* inner app calls, it does not enforce minimum balances (an app that cannot pay looks fine), and its `UInt64()` accepts only plain `int`. Anything depending on those belongs in `scripts/keeper_e2e.py`.
- LocalNet runs algod in dev mode: no blocks are produced on their own. Use `scripts/network.py::wait_for_round`, which pokes the chain with self-payments.
- Contracts requiring inner-txn fees rely on group fee pooling — callers add extra fee (see `scripts/keeper_e2e.py`).
- On TestNet, disable the suggested-params cache (`set_suggested_params_cache_timeout(0)`) and fund the app account's base MBR (0.1 ALGO) before it can escrow or hold boxes.
- Upkeep box values are ARC-4 head/tail encoded (32-byte creator, static fields inline, dynamic `call_data` in the tail via the offset at bytes [40:42]). `scripts/keeper_bot.py` has the reference decoder; `web/src/app/core/upkeep.ts` is its TypeScript twin and both are pinned to the same recorded box.
- The console is styled **only** with the CorvidLabs design system vendored in `web/public/brand/` (tokens, fonts, sun/moon toggle). Never hardcode a colour or re-derive a token; re-vendor with the design system's `sync-to.sh`.
- Wallets come from `@txnlab/use-wallet` (`web/src/app/core/wallets.ts`), same pattern as our other Algorand front ends: Pera, Defly, Lute, Exodus and Kibisis need no configuration; only the generic WalletConnect entry takes a project id, and it is only offered when one is set.
- Amounts are shown in ALGO, not µALGO. Round counts are also rendered as human time via the measured (or nominal 2.8 s) round rate.

## Secrets

- `.env.*` files are gitignored and must stay that way. The TestNet deployer mnemonic in `.env.testnet` is a throwaway — never commit real mnemonics, never reuse it on mainnet.
