---
spec: keeper.spec.md
---

## Tasks

- [x] Run the TestNet demo end-to-end (fund deployer via dispenser, deploy, register, execute), done 2026-08-23; Keeper 769772891, Pulse 769772906, Pulse.beats = 1
- [x] Redeploy to TestNet with the box-MBR fix and re-verify on-chain, done 2026-08-24; Keeper `769802474`, Pulse `769772906`, all 14 e2e stages green (first execution round 66629036, catch-up stage through round 66629138). Escrow reclaimed from the deprecated app `769772891` and its registry emptied.
- [ ] Cancel leftover demo upkeeps 0 to 3 to reclaim their escrow (~0.08 ALGO)
- [ ] Off-chain keeper bot that watches rounds and executes due upkeeps (reference implementation)

## Gaps

- Inner app call execution is not verifiable in the mock AVM (records only); covered by TestNet e2e.
- No multi-account test for `cancel` by a non-creator (mock sender overrides are limited); the assertion is simple and reviewed.
- No `upkeep_info` readonly getter; clients decode the box struct themselves.

## Review Sign-offs

- **Product**: pending
- **QA**: pending
- **Design**: n/a
- **Dev**: pending
