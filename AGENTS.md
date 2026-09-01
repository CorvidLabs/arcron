# AGENTS.md

Guidance for AI agents working in this repo. Also see `README.md`.

## What this is

Arcron: Algorand smart contracts (Algorand Python / Puya + AlgoKit). The main
project is `smart_contracts/keeper/`, a permissionless keeper network, live on
TestNet (app 769891898; 769802474 and 769772891 are superseded and predate the
1.0 contract). `pulse/` is its demo target (app 769891902).

## Commands

- Everything: `fledge lanes run ci` (build + unit tests + spec check)
- Everything, on a real chain: `fledge lanes run local` (ci + the keeper e2e; needs `algokit localnet start`)
- Sustained operation: `fledge lanes run endurance` (adds a soak; ~3 min)
- Console: `cd web && bun run ng serve` (LocalNet by default), `bun test` for its unit tests
- Reading the live deployment (all read-only, none of them signs anything):
  `fledge run health` (upkeeps about to starve, upkeeps that pay a keeper
  nothing, keeper solvency), `fledge run clock` (how long the deployment has
  been the deployment, and it refuses to count once the local build stops
  matching the chain), `fledge run keeper-ui` (a local dashboard on 4300, never
  published).
- Fixing what `health` finds starving: `fledge run topup` prices every upkeep in
  **days of runway** and prints the top-ups that reach 30 of them. Planning is
  read-only; `fledge run topup -- --send` is the one that signs. It refuses to
  fund an upkeep whose cadence makes 30 days cost more than 10 ALGO, because
  that upkeep wants cancelling rather than funding: 98-109 run every 20 rounds
  and would need 192 ALGO each. `top_up` is permissionless, so this needs no
  creator key, only a funded account of its own.
- Would running a keeper here be worth it: `fledge run keeper-preview`
  measures what the registry actually paid (the inner payment `execute`
  sends, less the group fee the keeper paid to send it), reports the split
  rather than only the total, and simulates the due upkeeps so a target
  that reverts is never counted as money on the table. Read-only. It exists
  so alpha task #93 can be decided before it is started.
- Console as a rendered page: `fledge run web-render`. Builds it, serves it, and audits computed style and layout at four viewports in both themes against a stubbed chain. Needs `fledge run web-render-install` once, for a Chromium build. In the `local` lane, not `ci`.
- Console for hosting: `fledge run web-build-hosted` (base href `/arcron/console/`), then `fledge run web-verify-hosted` (serves it at that subpath and fetches every file). Stage it into a `CorvidLabs/site` checkout with `fledge run site-console -- --site ../../site`; neither script commits or pushes.
- Build: `poetry run python -m smart_contracts build` (rebuilds artifacts and typed clients; always rebuild after contract changes)
- Test: `poetry run pytest tests/ -q`
- Specs: `specsync check --strict`
- End-to-end: `poetry run python -m scripts.keeper_e2e --network localnet|testnet`
- Keeper bot: `poetry run python -m scripts.keeper_bot [--once] [--network N] [--app-id N]` (signs as KEEPER_MNEMONIC, else DEPLOYER_MNEMONIC)
- Keeper race: `poetry run python -m scripts.keeper_race --network localnet|testnet` (two real bots reaching for the same due upkeep in the same round; off LocalNet it needs two funded mnemonics in the environment, since one account cannot race itself)
- Python env: Poetry (`.venv` in project). Python 3.13. Do NOT use 3.14 (coincurve has no wheels).

## 1.0 scope (decided 2026-08-24)

Full reasoning in [`docs/design/1.0.md`](docs/design/1.0.md). The short version,
because these are easy to get wrong from memory:

- **A struct change means a new app id**, and every creator cancelling and
  re-registering by hand. That holds however a deployment is governed: an
  update replaces code, not the shape of boxes that already exist. (The
  contract *is* upgradeable until its creator calls `freeze`, which buys a
  bug fix, not a reshaped struct. See `docs/security.md`.) So #7, #14, #8 and
  #9 are batched into one release and the surface is then frozen.
- **#9 is an ASA fee *capability*, not a commitment.** CORVID is not wired in;
  escrow and fees stay ALGO by default. "No token required" remains true.
- **#15 (staking) and #22 (keeper-supplied data) are closed**, not merely
  deferred. Reasoning in [`docs/design/out-of-scope.md`](docs/design/out-of-scope.md).
  Staking has nothing to slash; keeper-supplied data inverts the one guarantee
  Arcron makes. The line between #8 and #22 is the reason: declaring which
  *resources* a call may touch is safe because the creator still fixes what is
  called, while letting a keeper supply *data* is a different product. Do not
  reopen either without reading that first.
- **Dogfood** is the live TestNet registry itself, watched by the notifier,
  with `pulse` as the heartbeat target for the uptime clock. It started as a
  recurring `rain` draw we ran ourselves; rain moved to
  https://github.com/CorvidLabs/arcron-rain on 2026-08-31 and still runs, as
  upkeep 113 against the hub it redeployed there, but it is no longer the
  evidence, because the registry as a whole is. Not because outsiders use it:
  the count of upkeeps registered by somebody who is not us is **zero**, and
  every one of the seven addresses that looked like a stranger is an agent of
  ours. What the registry proves is sustained operation across every policy
  and cadence, by one operator, which is a soak test rather than adoption.
  Say it that way round; an earlier version of this bullet said 26 of the 33
  live upkeeps belonged to somebody else, which was the reading corrected in
  [#236](https://github.com/CorvidLabs/arcron/pull/236). Upkeep 91 points at the hub rain abandoned and is a loose end,
  not the dogfood.
- **MainNet gate** is self-review plus sustained TestNet time across the live
  registry. No paid audit. Any struct change restarts that clock, and no other
  repository can stall it.
- **Public release** waits until the deployment is one we are not about to
  replace; the licence and docs (#50) land before visibility does.

## Conventions

- Every contract has a spec-sync spec in `specs/<name>/` (strict mode). Update the spec's Public API tables, requirements, testing.md and Change Log whenever the contract's surface changes; `specsync check --strict` must stay green.
- Every script picks its network with `--network` / `ARCRON_NETWORK` (`scripts/network.py`), which loads `.env.<network>` and then verifies the node's genesis id. LocalNet needs no mnemonics; accounts come from KMD.
- Tests use `algorand-python-testing` mocks. Three mock limits to know: it records but does not *execute* inner app calls, it does not enforce minimum balances (an app that cannot pay looks fine), and its `UInt64()` accepts only plain `int`. Anything depending on those belongs in `scripts/keeper_e2e.py`.
- LocalNet runs algod in dev mode: no blocks are produced on their own. Use `scripts/network.py::wait_for_round`, which pokes the chain with self-payments.
- Contracts requiring inner-txn fees rely on group fee pooling, so callers add extra fee (see `scripts/keeper_e2e.py`).
- On TestNet, disable the suggested-params cache (`set_suggested_params_cache_timeout(0)`) and fund the app account's base MBR (0.1 ALGO) before it can escrow or hold boxes.
- Upkeep box values are ARC-4 head/tail encoded (32-byte creator, static fields inline, dynamic `call_data` in the tail via the offset at bytes [40:42]). `scripts/keeper_bot.py` has the reference decoder; `js/src/upkeep.ts` is its TypeScript twin and both are pinned to the same recorded box.
- The console is styled only with the CorvidLabs design system vendored in `web/public/brand/` (tokens, fonts, sun/moon toggle). Never hardcode a colour or re-derive a token; re-vendor with the design system's `sync-to.sh`.
- A CSS change to `web/` is not reviewed until `fledge run web-render` has run. Unit tests, four independent agent reviews and an axe-core pass at zero violations all missed a disabled Register button rendering at 1.02:1, because none of them ask a browser what colour anything ended up. The suite asserts properties of the rendered page rather than diffing screenshots, and writes screenshots and a ranked `findings.md` to `web/e2e/__screenshots__/`. Anything knowingly unfixed lives in `web/e2e/baseline.json` with its measurement and the reason it stands; adding an entry there to make a run pass is the one thing that file must never be used for.
- The console's canonical address is `https://corvidlabs.xyz/arcron/console/`, and that is a security property rather than a convenience: the contract is permissionless, so anyone can build a front end, and the address is the only thing separating ours from a copy. Changing it means changing the base href in `fledge.toml`, the three constants at the top of `scripts/publish_console.py`, and every document that names it. Every asset the console emits is relative, so the base href is the only thing that has to know the path.
- Wallets come from `@txnlab/use-wallet` (`web/src/app/core/wallets.ts`), same pattern as our other Algorand front ends: Pera, Defly, Lute, Exodus and Kibisis need no configuration; only the generic WalletConnect entry takes a project id, and it is only offered when one is set.
- Amounts are shown in ALGO, not µALGO. Round counts are also rendered as human time via the measured (or nominal 2.8 s) round rate.

## Secrets

- `.env.*` files are gitignored and must stay that way. The TestNet deployer mnemonic in `.env.testnet` is a throwaway. Never commit real mnemonics; never reuse it on mainnet.
