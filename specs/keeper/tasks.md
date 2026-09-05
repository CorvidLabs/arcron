---
spec: keeper.spec.md
---

## Tasks

- [x] Run the TestNet demo end-to-end (fund deployer via dispenser, deploy, register, execute), done 2026-08-23; Keeper 769772891, Pulse 769772906, Pulse.beats = 1
- [x] Redeploy to TestNet with the box-MBR fix and re-verify on-chain, done 2026-08-24; Keeper `769802474`, Pulse `769772906`, all 14 e2e stages green (first execution round 66629036, catch-up stage through round 66629138). Escrow reclaimed from the deprecated app `769772891` and its registry emptied.
- [ ] Cancel leftover demo upkeeps 0 to 3 to reclaim their escrow (~0.08 ALGO)
- [x] Off-chain keeper bot that watches rounds and executes due upkeeps (reference implementation): `scripts/keeper_bot.py`, live on TestNet since 2026-08-26

## MainNet rollout

The plan, the ceremony and the dated goal are `docs/design/mainnet-rollout.md`. The
contract does not change for any of this; these are operating tasks against the
bytecode already on TestNet (`c94c6e0c…`).

- [x] The create ceremony (`scripts/deploy.py`) refuses what a create cannot take back and reads every permanent field back, pinned by `tests/test_deploy.py`, done 2026-09-05
- [x] Ceremony rehearsed on LocalNet from a clean detached worktree: second-keeper refusal, then create with `--another`, `govern status`, `verify_build`, wrong typed confirmation refused, `govern update`, `govern freeze`, update refused after freeze, done 2026-09-05
- [ ] Ceremony rehearsed on TestNet with a fresh throwaway creator and the record kept in the rollout doc (blocked on funding the throwaway; the TestNet deployer has 0.5 ALGO spendable)
- [ ] G1, by 2026-09-12: a VPS runs the keeper and the notifier against TestNet from `main`, with a node of our own or a fallback, and the notifier has posted to Discord for seven days
- [ ] G2, by 2026-09-19: `git tag mainnet-1`, `fledge run deploy-mainnet -- --with-pulse`, `govern status` and `verify_build` recorded privately, keeper and notifier watching the new id before the first upkeep, Pulse `tick` registered at `fee_cap 0` and executed
- [ ] G3, by 2026-10-19: `arcron-rain` has a MainNet path and its `draw()` is an upkeep; alpha-4 (escalation decision plus the three `opt_in_asset` / `top_up_asset` asserts) lands on TestNet, soaks, and reaches MainNet by `govern update`; weekly `health-mainnet` and `clock-mainnet`
- [ ] G4: the announce and freeze decisions, recorded in `docs/releases.md` with the notifier's record as evidence
- [ ] Cancel starved upkeeps 98 to 109 on TestNet (20-round cadence, escrow 0); their creator is agent account `N43ZVH3J`, whose key is not in this repository

## Gaps

- Inner app call execution is not verifiable in the mock AVM (records only); covered by TestNet e2e.
- No multi-account test for `cancel` by a non-creator (mock sender overrides are limited); the assertion is simple and reviewed.
- No `upkeep_info` readonly getter; clients decode the box struct themselves.

## Review Sign-offs

- **Product**: pending
- **QA**: pending
- **Design**: n/a
- **Dev**: pending
