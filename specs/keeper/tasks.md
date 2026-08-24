---
spec: keeper.spec.md
---

## Tasks

- [x] Run the TestNet demo end-to-end (fund deployer via dispenser, deploy, register, execute) — done 2026-08-23; Keeper 769772891, Pulse 769772906, Pulse.beats = 1
- [ ] Cancel leftover demo upkeeps 0–3 to reclaim their escrow (~0.08 ALGO)
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
