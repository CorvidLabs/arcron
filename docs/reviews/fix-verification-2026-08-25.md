# Arcron: adversarial verification of the six fixes

_Date: 2026-08-25. Scope: `git diff 470148a..HEAD` (commits `ce26fe8`, `fb4546c`, `c5574f7`, `d719e71` — plus HEAD is actually a fifth, unlisted commit, `74cda65` "Ask rain's gate a second time, at claim", which is inside the range and was treated as in-scope). Method: code read at HEAD, hashes recomputed where docs claimed them, both unit suites run (259 pytest + 80 bun, all green), installed algokit-utils read to verify fee semantics. Nothing was run on a chain._

## 1. Per fix: does it hold?

### Fix 1 — deadman `arm` reserves the floor (`fb4546c`): **contract HOLDS; demo sibling BROKEN**

The contract change is right, on every point raised:

- **100,000 is the correct floor.** DeadMan has no boxes, no assets, no opt-ins (`smart_contracts/deadman/contract.py` — full read). An app account's MBR is 100,000 base + per-asset + box costs; global-state schema is charged to the *creator*, not the app account. Nothing raises this app's own minimum above 100,000 in any reachable state. Confirmed.
- **No underflow/ordering problem.** `assert deposit.amount > APP_BASE_MBR` (`contract.py:87-89`) precedes the subtraction (`contract.py:103`); strict inequality leaves escrow ≥ 1.
- **No other path below the floor.** `sweep` is bookkeeping only (`:115-133`); `claim` pays exactly `allocated` ≤ balance − 100,000 with inner fee 0 (`:144`), so the app never pays fees either; direct donations only add headroom.
- **No deposit size passes `arm` but can't pay a claim.** Minimum passing deposit 100,001 → escrow 1 → claim pays 1, leaving exactly 100,000. ✓

But the LocalNet proof **does not exist at HEAD**: `scripts/deadman_demo.py:36` keeps `ESCROW = 1_000_000`, sends it as the deposit (`:77`), then asserts the *booked escrow equals the full deposit* at `:85`, and again at `:143` (allocated), `:163` (claimed), `:167` (net). The contract now books 900,000, and `_assert` is strict equality (`scripts/keeper_e2e.py:144-145`). **The demo dies at stage 1.** Nothing else funds the app — the whole demo was read; only the arm deposit reaches it. The unit tests did the `DEPOSIT`/`ESCROW` split correctly (`tests/test_deadman.py:21-24`); the demo didn't get the same edit, so the comment at `:55-62` ("this demo running clean is the proof") asserts something that is currently false. Confirmed by reading — arithmetic, not executed; it cannot pass. The fix itself is right; its proof is broken in the most visible way possible, so it will be caught on first run — but at HEAD the e2e evidence for the H1 fix is zero.

### Fix 2 — watchdog assert order + zero reporter (`ce26fe8`): **HOLDS**

- The order swap leaks nothing: configured-ness is public on-chain state, and "Not configured" is simply the accurate error (`smart_contracts/watchdog/contract.py:99-102`).
- No state where both asserts pass while unconfigured: `threshold_rounds` has exactly one setter — `configure`, once-only (`:81-85`) — which sets `reporter` in the same call, and `reporter` can no longer be zero (`:86`). Pre-configure, every caller gets "Not configured"; post-configure, non-reporters get the sender error. Spec error table updated (`specs/watchdog/watchdog.spec.md:110-112`), artifacts rebuilt, both new branches tested (`tests/test_watchdog.py:67-90`).
- Residual, accepted: `configure` still accepts the watchdog's own app address or any other uncontrolled non-zero address as reporter — fail-closed, creator-typo-only, informational in the original review. Fine to leave; say so in the spec if it should be closed.

### Fix 3 — deployer fallback gated on frozen (`c5574f7`): **HOLDS** (one overclaim in the reasoning, practically safe)

- **Ordering is correct.** The guard runs at startup, before the key is loaded and before anything is signed (`scripts/keeper_bot.py:446-465`), and it queries the genesis-verified `algod` from `net.connect` (`:437`). `frozen` is one-way, so there's no TOCTOU: an app that passes at startup can never become unfrozen later.
- **"Missing key means frozen": practically right, theoretically wrong — and the wrong case doesn't matter.** For this project's lineage it's correct: pre-governance deployments are NoOp-only immutable, no update path exists, so missing flag = frozen. In the abstract, an app *could* have no `frozen` key and be updateable (programs updated to a version that deletes the key) — but reaching it requires the operator to explicitly point the bot at a crafted app, and the downside there is bounded fees, not key exposure: the operator's DEPLOYER key is not the crafted app's creator. The docstring (`keeper_bot.py:278-281`) overclaims as a universal rule; the behavior is safe.
- **base64/pagination:** key comparison is exact; global state is protocol-capped at 64 entries so `application_info` can't paginate or truncate it; a bytes-typed value reads as `uint 0` → "not frozen" → refuses — fail-safe direction.
- **Guard runs before any key use** — strictly before `from_environment("DEPLOYER")` succeeds.
- **No regression on LocalNet or CI, confirmed:** `from_environment("KEEPER")` on LocalNet resolves via KMD and never reaches the `except` (installed `algokit_utils/accounts/account_manager.py:424`+ — the LocalNet branch creates/funds a KMD wallet), so the demos are untouched; the GitHub workflow sets `KEEPER_MNEMONIC` or skips before the bot runs (`.github/workflows/keeper-bot.yml:62-66`). A VPS operator with no `KEEPER_MNEMONIC` against the unfrozen TestNet app now gets a loud, correct refusal — intended.
- Amusing but harmless: `deploy/vps/install.sh:90` still defaults to app `769823086` (pre-governance), which now reads as frozen — the one app where the DEPLOYER fallback is still allowed is the dead one the docs forbid. (M4 remains unfixed; see §3.)

### Fix 4 — ASA surcharge checks opt-in (`c5574f7`): **HOLDS for the targeted bug; the remaining gap is real but bounded**

- **Within-scan staleness (Python):** holdings are re-read every scan (`keeper_bot.py:522-527`), and only the operator can change the account's opt-in set. Worst cases: operator opts in mid-scan → bot underpays the surcharge on an execution where the contract *would* pay a bonus → group fails on pooled fees → one backed-off execution, self-healing next scan; operator opts out mid-scan → one 1,000 µALGO overpay. Bounded, self-inflicted, fine.
- **The remaining predicate gap is a real cost, and here is its size.** The contract's condition (`smart_contracts/keeper/contract.py:393-400`) is `opted_in AND app-real-holding ≥ bonus AND neither side frozen`. The clients check opt-in plus the *book* asset balance. A bonus ASA that gets clawed back below book value, or frozen on either side, makes the contract skip the bonus while the client pays the surcharge anyway: **1,000 µALGO wasted per execution of that upkeep, every interval, indefinitely** — but never net-negative, because `MIN_UPKEEP_FEE` (4,000) always covers the worst-case cost (4,000). It needs a managed (clawback/freeze-enabled) ASA as the bonus asset. Cheap partial improvement: the bot already fetches `account_info`, which carries its own holdings' `is-frozen` flags — its own freeze status is free to check; the app-side holding/freeze needs extra lookups. **INCOMPLETE-but-bounded.**
- **JS type handling:** `BigInt(holding.assetId) === assetId` is robust for algosdk v3 whether `assetId` is bigint or number (`js/src/keeper-txns.ts:57-68`); `feeAsset` undefined → `0n` → early `false` → no surcharge. Correct.
- **The thing that could have been a new bug and isn't:** the JS client attaches the fee asset as a foreign reference whenever it's set, *not* only when `paysBonus` (`keeper-txns.ts:231`) — required, because the contract evaluates `is_opted_in(Asset(...))` and the freeze/balance reads even when it ends up skipping the bonus, and those reads need the reference. Done right, with a comment explaining why (`:203-207`). The Python bot inherits correct attachment from simulate-first.
- **Untested:** the bot's surcharge branch is inline in `main()`'s loop; the new tests cover `is_frozen` only (`tests/test_keeper_bot.py:195-227`). The branch that decides whether 1,000 µALGO moves has no unit test. Minor, but it's the same shape as the bug being fixed.
- **max_fee (bundled in this commit, original M3): verified in the installed library.** algokit-utils computes `txn.fee = suggested + extra_fee` and raises `ValueError` if it exceeds `max_fee` (`.venv/.../transaction_composer.py:2126-2130`). The bot's cap `10_000 + extra_fee` (`keeper_bot.py:566-571`) bounds the node-influenced portion at 10,000 µALGO exactly as the comment claims. Fixed.

### Fix 5 — board net reward (`c5574f7`): **HOLDS, deliberately conservative**

`executionCost` quotes 4,000 for any upkeep with `feeAsset > 0` (`js/src/board.ts:64-66`). For a viewer who is *not* opted in, the real cost would be 3,000 (no bonus moves), so the board understates net by 1,000 for them — but understatement is the safe direction, the original bug was the overstatement, and an exact quote requires knowing the viewer's holdings, which a wallet-less board cannot know. The comment says exactly this. Test pins the 1,000 delta (`js/test/board.test.ts:55-64`); the console's headline figure stays at the ALGO-only cost with an accurate comment (`web/src/app/components/upkeep-board.ts:211-213`). Correct in every case as a *worst-case* quote; exact per-viewer numbers would need a connected wallet.

### Fix 6 — documentation corrections (`d719e71`): **HOLDS with two missed spots**

The corrected rule is **true in every case checked**, confirmed against the contract: a capped upkeep on its first run is funded for the cap (`keeper/contract.py:205-210`); `fee_cap < fee_per_execution` cannot exist (`:179`); mid-escalation the fee falls back to base when the escrow can't cover it (`:364-366`), so dormant ⟺ `balance < fee_per_execution`, always. The new text in `docs/integrating.md:379-389`, `docs/arcron.md:158-163`, and `specs/keeper/requirements.md:50` states it correctly and keeps the right nuance (price *runway* at the ceiling). The design-doc entry was marked resolved with the history preserved — good practice. `docs/deploying.md:84-91`'s sample output **verifies exactly**: recomputed from the artifact — approval is 2104 bytes, combined sha256 is `0afab368…bf49`, matching the doc.

The misses:

- **`docs/arcron.md:636-637` still teaches the old rule**: "an upkeep with a ceiling can go dormant at a balance that would have covered several runs at its base fee." False under the fallback. It sits in the "what changed in 1.0" summary — the highest-skim location in the docs. Confirmed.
- **`specs/keeper/testing.md:42` still says**: "A ceiling set, escrow between the base fee and the ceiling → Executable while on time, **dormant once late**." False — once late it falls back to base and stays executable. `specsync check --strict` passes (10/10) because it validates structure, not semantics; this is precisely the drift class it cannot see. Confirmed. (`testing.md` also still lacks the ASA-bonus/governance coverage rows from the original review.)

### Fix 7 (the unlisted one) — rain gate re-check at `claim` (`74cda65`): **INCOMPLETE, and three of its own texts oversell it**

Mechanically consistent: `claim(gate_asset)` re-checks the gate exactly as `enter` does (`smart_contracts/rain/contract.py:373-377`), ABI + artifacts + spec + both demos + tests all moved together, unit tests green, ungated draws ignore the argument.

But it **does not stop the dilution attack it exists to stop.** The check is "holds a collection token *now*". The walker controls all ten accounts *and* the NFT: when any walked ticket wins, one transfer moves the NFT to the winning account and `claim` passes (`:375-377`). Win probability per walked ticket is unchanged; collection costs one extra transfer. The fix's real effect is narrower: a winner who *permanently lost* the token can no longer collect (anti-dodge), at the disclosed cost that an honest winner who sells post-win forfeits too. That's a real rule, honestly labeled in the docstring (`:364-368`) — but the commit message ("the other nine stop being worth anything"), the spec (`specs/rain/rain.spec.md:127`: "worthless"), and `examples/community-rain.md:98-101` ("the other nine tickets cannot be collected on") all claim the walk is neutralized. It isn't; it's inconvenienced. The contract docstring itself (`:361-362`) says it too. "Cannot be collected on *while the token sits in another of the walker's own accounts*" is the true sentence, and it's much weaker.

Two further issues:

- **Sibling check not mirrored:** `enter` rejects the prize asset as a gate token ("The prize is not a ticket", `contract.py:207`); `claim` does not (`:375-377`). On an ASA-prize draw minted by the collection's own creator — the common case, called out in the code — a past winner holding only *prize* tokens passes the claim gate with no collection token at all: a permanent exemption from the hold-at-claim rule for exactly the class it targets. Confirmed by reading both checks; no test covers it.
- The full fix (ticket per asset id) does require new box semantics → new app id, and the maintainer said so and deferred it. That's a legitimate decision; the problem is only that the user-facing texts claim more closure than the decision delivered.

## 2. What the fixes introduced

- **The deadman demo's broken asserts** (above) — the flagship introduced issue: fix right, proof broken.
- **Rain's claim-side prize-asset gate hole** and the overstated "worthless" language in contract docstring, spec, and example.
- **Nothing new** in watchdog, the bot, the JS client, or the board — those diffs are clean, and the JS foreign-asset subtlety was handled correctly.
- The frozen guard's behavior change (VPS operator without `KEEPER_MNEMONIC` on the unfrozen TestNet app is refused) is intentional and documented in the error message; not a bug, but it is a breaking change for anyone running the bot that way today.

## 3. What the fixes missed in siblings

- **`scripts/deadman_demo.py`** (Fix 1's proof) — the worst one.
- **`docs/arcron.md:636-637` and `specs/keeper/testing.md:42`** (Fix 6's rule) — same wrong sentence, two surviving locations.
- **Rain `claim` vs `enter`** (prize-asset exclusion) — above.
- **Original review findings untouched by this surface** (spot-checked, all still present at HEAD): M4 `deploy/vps/install.sh:90` superseded app id; M7 console app-id authenticity (`web/src/app/core/entry.ts` still accepts any `?app=N`, no `frozen` surfacing); the `rekey_to`/`close_remainder_to` assert sweep (keeper `register`/`top_up` and the target contracts still check only receiver+amount); `scripts/spike_reentrancy.py` still TypeErrors; the systemd `StateDirectory` and box-pagination issues; subscription/treasury/embargo residuals. Some are tracked as issues (#100) — open, not fixed.
- **Uncommitted M8 work exists and looks strong** (`scripts/govern.py`, `scripts/multisig.py`, `scripts/verify_build.py` are dirty in the working tree): collected refusals that close the `app_id()==0` hole from the original review (non-app txns refused by default, genesis-id check, blob-address vs configured, rekey/close refused, 10k fee ceiling), blob-authoritative member display, combined-digest pinning that is byte-identical to `verify_build` (recomputed the keeper digest to check). It's uncommitted, so it isn't real yet — and it was only skimmed. Also `tmp-mainnet-path-review.md` is an untracked temp file at the repo root.

## 4. Confidence that these six areas are now correct: **7/10**

Per area: deadman contract 9 (demo 2 — fix the asserts and it becomes the proof it claims to be); watchdog 9; frozen guard 8; surcharge 8 (residual gap bounded and understood); board 9; docs 7 (two surviving locations of the old rule); the unlisted rain change 4 *as an M2 fix* — 8 as an honestly-labeled partial, if the three "worthless" claims are rewritten to match what it actually does.

## 5. The single thing most likely to be wrong that was not checked

**Whether the rebuilt artifacts for deadman, watchdog, and rain at HEAD are byte-identical to a fresh compile of their sources.** The *keeper* artifact's hash was verified against `deploying.md`, but the three changed contracts were not recompiled — a stale artifact would make demos deploy old behavior while the unit tests exercise the fixed source, and it would be invisible to everything run here. Close behind: no demo was run on LocalNet (the deadman demo failure is arithmetic-certain, but rain's gate-at-claim flow on a real chain is entirely unverified), and the `docs/security.md` 3-of-5 multisig address was not verified against the real member set (the members are, correctly, not in the repo).
