# Arcron repository split — migration plan

## 1. The repos

### New: `CorvidLabs/arcron-rain`
The Rain hub — a public prize-draw contract that runs on the Arcron keeper network. Its own contract, spec, tests, bot, TypeScript client, and its own console at its own canonical address.

### Deferred (do not create yet): `CorvidLabs/arcron-examples`
Would hold `smart_contracts/subscription/` plus `examples/`. **Recommendation: do not create it. Keep subscription in arcron.**

### Why one repo, not two or three

- **Rain must move.** It has real users, real money, a live *immutable* deployment (hub `770130162`), a release cadence of its own (`specs/rain/` is at version 4; every other spec is version 1), a product stack across five directories, and its own GitHub workflow and repository secret (`RAIN_MNEMONIC`, readable by every workflow in this repo). That is a product wearing a keeper network's clothes.
- **Subscription must not.** `smart_contracts/subscription/` contains only `__init__.py` and `contract.py` — no `deploy_config.py`, unlike rain. It has never been deployed anywhere, holds nobody's money, and its own docstring says it exists to demonstrate the pattern `docs/integrating.md` argues for. It *is* documentation. Moving it deletes arcron's only worked pull-payment example (`docs/integrating.md:217,249-251`, `docs/book/arcron-working-guide.md:667,697-698`) and creates a repo whose entire content is one undeployed contract and three markdown files. An examples directory inside a library repo is normal; a live product with its own money inside a library repo is not.
- **The probes are not products.** `resource_probe` and `sim_probe` are instruments that measure the keeper's own `execute()` boundary. `sim_probe` is a wired step in `[lanes.local]` (`fledge.toml:138`, `smoke-reference-boundary`) and is named in `specs/keeper/testing.md:13`. `resource_probe` is the reproducible evidence behind the limit stated in `docs/integrating.md:113`. Splitting them out would remove a step from the keeper's own lane.
- **Naming.** Use the `arcron-` prefix. Rain cannot run without a keeper network under it and its own code says so: `js/src/rain.ts:67-71` hardcodes `keeperAppId: 769891898` and `upkeepId: 91`. A bare `CorvidLabs/rain` hides that from the first reader.

**Ruling on the survey's conflict:** the five analysis dimensions routed rain to two different names and subscription to three different repos. The plan above supersedes all of them. One new repo, `arcron-rain`, `arcron-` prefix, subscription stays.

---

## 2. What goes into `CorvidLabs/arcron-rain`

### Contract and spec
| Path | Note |
|---|---|
| `smart_contracts/rain/contract.py`, `deploy_config.py` | |
| `smart_contracts/artifacts/rain/` | Must be deleted from arcron explicitly — see Break #9 |
| `specs/rain/rain.spec.md` | Plus `.specsync/config.toml` copied verbatim |

### Tests
`tests/test_rain.py` (41), `tests/test_rain_bot.py` (14), `tests/test_rain_one_draw.py` (7). All three import only rain modules; `test_rain.py`'s one keeper mention is a comment.

### Scripts (1,650 lines)
`scripts/rain_bot.py`, `rain_demo.py`, `community_rain_demo.py`, `rain_one_draw.py`, `rain_testnet_deploy.py`, `rain_testnet_live_proof.py`.

### TypeScript
`js/src/rain.ts`, `rain-abi.ts`, `rain-txns.ts` (966 lines) and `js/test/rain-abi.test.ts` (43 tests — its `../../smart_contracts/artifacts/rain/Rain.arc56.json` path becomes same-repo, which is better).

### Console
`web/src/app/pages/rain-page.ts`, `rain-detail-page.ts`, `rain-create-page.ts` (+ their `.test.ts`), `web/src/app/components/rain-create-form.ts`, `rain-stat-tiles.ts`, `web/src/app/core/rain.service.ts`, `nft-media.ts`, `nft-media.test.ts`, `prize-units.test.ts`, `web/e2e/rain.pw.ts`, the two `SCENARIOS` at `web/e2e/matrix.ts:64-65`, the `RAIN_APP_ID` stub in `web/e2e/chain.ts:28-34,46-47,235,261-307`, and `web/public/brand/corvid-0001.png` (which must **not** be re-filed under `brand/` — it is a sample NFT image sitting inside a vendored design-system directory that gets overwritten by `sync-to.sh`).

### Ops
`.github/workflows/rain-bot.yml`, `deploy/rain-bot.service`, `deploy/rain.env.example`, the `RAIN_MNEMONIC` secret.

### Docs
`examples/rain.md`, `examples/community-rain.md`, `docs/testnet.md:18-21,97-174`, `docs/releases.md:155-263`, `docs/status.md:39-98` (rain half) and `:118`, `docs/journeys.md:249-256`.

### Copied, not moved (arcron keeps its own)
- `scripts/network.py` (281 lines) — network selection, `.env.<network>`, genesis verification, MainNet guard, `ROUND_SECONDS`, `wait_for_round`.
- `scripts/verify_build.py` (imports only `scripts.network`) with `CONTRACTS = ("rain",)`. **Mandatory, not optional:** hub `770130162` is immutable, so byte-for-byte re-verification is its entire trust story, and `docs/status.md:79` and `docs/releases.md:166` both instruct the reader to run it.
- The four generic helpers from `scripts/keeper_e2e.py` — `_selector` (:66), `_box_mbr` (:75), `_quiet` (:121), `_assert` (:146).
- `Emitter` (`scripts/keeper_bot.py:126`) and `Shutdown` (:146) — ~40 lines of bot plumbing with nothing keeper-specific in them.
- `foldUnnamedResources` (`js/src/keeper-txns.ts:354-405`) plus `ResourceRefs`, `Signing`, `CallResult` (~65 lines total). `ASSET_OPT_IN_MBR` should be dropped, not copied — it is Algorand's flat 0.1 ALGO per-asset MBR, and `js/src/rain.ts:24` already declares its own `APP_BASE_MBR = 100_000` of the same value.
- The vendored design system (`web/public/brand/`) via the design system's own `sync-to.sh`.
- A ~200-line chain-connection service standing in for the five `ArcronService` members `rain.service.ts` uses: `network()`, `round()`, `algod()`, `genesisMatches()`, `status()`.
- The generated keeper client (`smart_contracts/artifacts/keeper/keeper_client.py`, which is committed) for `rain_demo.py` and `rain_testnet_deploy.py`.

### Dependencies of the new repo
- **On-chain, permanent:** the keeper registry. Rain *is* upkeep 91 on app `769891898`. `rain.service.ts:572-578` reads its own upkeep box out of the keeper registry, and `rain-detail-page.ts:17` uses `roundsUntilDue`. This is the correct direction and should be written down as a rule so it never inverts.
- **Code:** none, for the first cut. Vendor the ~65 TS lines and the ~5 Python helpers. Do not plan around depending on `@corvidlabs/arcron` — see Decision D7.
- **Runtime:** algosdk ^3, algokit-utils ^4, Python `>=3.12,<3.14`, puyapy `>=5.0,<5.10`.

---

## 3. What stays in arcron, and why it is coherent

**Contracts (6):** `keeper` (the product), `pulse` (the heartbeat target the uptime clock reads; the only target exercising both `tick` and `tick_with(uint64,string)`), `subscription` (the worked pull-payment example), `beacon_stub` (kept per the standing rule), `resource_probe` and `sim_probe` (the instruments behind two documented keeper limits).

**Scripts:** 41 of 47. Every one is keeper-network work — the bot, its backoff/sweep/daemon/assets, the e2e, soak, scenario, race, attacks, clawback, reference boundary, governance and multisig, health/topup/preview/reclaim/notifier, build and release verification, the seven research spikes, the console publisher and the docs sync.

**Client:** `@corvidlabs/arcron` becomes honestly keeper-only — `upkeep.ts`, `keeper-abi.ts`, `keeper-txns.ts`, `target-test.ts`, `board.ts`, `format.ts`, `networks.ts`.

**Consoles:** three Angular apps, one published. `web/` returns to three destinations; `web-keeper/` and `web-govern/` are local-only and pinned there by `tests/test_keeper_ui_stays_local.py` (`web-keeper` deliberately has no wallet dependency at all).

**The coherence claim, stated plainly:** after the split arcron contains a permissionless keeper registry, one heartbeat target it owns, three test instruments, one teaching example, one published console for the registry, two local operator tools, and the tooling to run, verify, govern and document all of it. Nothing in it holds a stranger's money except escrow the registry itself is for. That is one product.

**Two things to write down honestly rather than paper over:**
1. `beacon_stub` is scaffolding arcron keeps for *another repo's* contingency. `SECURITY.md:86-87` and `docs/arcron.md:715` currently read as though arcron owns a rain. Rewrite both to say what it actually is.
2. After the split the only third-party contract the keeper is ever proved to call on a real chain is `pulse`, which arcron wrote itself. `smoke-rain`, `smoke-community-rain` and `smoke-subscription` were the only non-pulse chain coverage; keeping subscription retains one of the three. `resource_probe` and `sim_probe` cover call shapes and reference boundaries. This is acceptable, but it is a real reduction and should be recorded.

---

## 4. Order of operations

`fledge lanes run ci` is green after every numbered step. Steps 0.x through 3 change nothing in arcron at all.

### Phase 0 — pre-flight, in arcron, before anything moves
Each is an ordinary PR; ci green throughout.

1. **Owner ratifies D1–D11** (§6). Nothing below is safe to start until D3 (the console) is answered in writing.
2. **Cancel upkeep 79.** It holds 7.7 ALGO of arcron escrow and still pays keepers to call `draw()` on superseded rain app `770029154` every 2,571 rounds — 11 executions in a recent 24 hours (`docs/status.md:52-62`). Do not export that into a new public repo.
3. **Fix the stale app ids.** `.github/workflows/rain-bot.yml:46` defaults to `769988156` and `deploy/rain.env.example:11` sets `RAIN_APP_ID=769988156` — both the *earlier* superseded app, while the workflow's own header comment claims it points at the hub. Neither id is in `tests/test_app_id_consistency.py`'s `SUPERSEDED` set, so nothing catches it on either side of the split.
4. **Remove the home-directory secret.** `scripts/rain_testnet_live_proof.py:61` is `AGENT_ENV = Path.home() / ".grok/secrets/agents/grok-4.6.env"`, read at `:111-114` for `DEPLOYER_MNEMONIC`. Replace with `--funder-env` / an env var. This is a public-release blocker, not move-time cleanup.
5. **Parameterise the keeper app id.** `scripts/rain_testnet_deploy.py:34` hardcodes `KEEPER_APP_ID = 769891898`. Make it `--keeper-app-id` / `ARCRON_KEEPER_APP_ID`, the way `rain_bot.py:204` already does for `RAIN_APP_ID`.
6. **Ship an `abandon` client.** `smart_contracts/rain/contract.py:427-441` declares `abandon`, `js/src/rain-abi.ts:21` declares the signature, and *nothing anywhere builds the transaction*. `rain_bot.py:249-250` says its own resolve/abandon paths are dead scans. On an immutable hub, `_fire_one` returns 0 while `prize_locked > 0` (`contract.py:607-610`), so a single unresolved ONE draw past the 800-round `SEED_WINDOW` stalls that rain permanently and only `abandon` frees it. Add it to `rain-txns.ts` and the console before any cutover. Today nothing on earth can unlock a stalled draw.

### Phase 1 — stand the new repo up (arcron untouched)

7. **Create `CorvidLabs/arcron-rain`, private, and seed it by copy.** Everything in §2, copied. Add `conftest.py`, a `pyproject.toml` modelled on arcron's (keep the `python = ">=3.12,<3.14"` bound and the `puyapy >=5.0,<5.10` pin), `.specsync/config.toml` verbatim, its own `fledge.toml`, and its own vendored `web/public/brand/`.
8. **Make its lane green:** `build`, `test`, `spec`, `js-test`, `web-test`, `web-render`, `smoke-rain`, `smoke-community-rain`. Then prove the trust story: `verify_build --network testnet --contract rain --app-id 770130162` must reproduce the deployed bytecode *from the new repo*. If it does not, stop — the immutable hub loses its verification story and nothing downstream is safe.
9. **Publish the rain console at `https://corvidlabs.xyz/rain/console/`** (a change in `CorvidLabs/site`, including the nginx `try_files` SPA fallback). Exercise `enter`, `deposit`, `claim`, `resolve` and the new `abandon` against `770130162` from the new address.

> ⚠️ **Flag day / overlap window opens here.** For the duration of steps 9–11 there are two legitimate addresses serving one hub. That temporarily weakens exactly the control `scripts/publish_console.py:52-58` and `web/README.md:12-15` describe. Announce it in advance; do not let anyone discover it.

### Phase 2 — cut arcron

10. **Commit A: the client and console cut.** Must land *before* Commit C — `js/test/rain-abi.test.ts:36` reads the rain artifact by relative path, so if the artifact goes first, the `js-test` step of the ci lane is red.
    - Delete `js/src/rain.ts`, `rain-abi.ts`, `rain-txns.ts`, `js/test/rain-abi.test.ts`; remove `js/src/index.ts:22-24` and the three `exports` entries in `js/package.json:22-24`.
    - Delete the six rain page/component files and four rain core/test files under `web/src/app/`.
    - **Replace, do not delete, the three routes in `web/src/app/routes.ts:46-60.`** Each becomes a tiny component that `window.location.replace`s to the new address, preserving the id. This keeps live shareable links working (`docs/console-plan.md:253` calls that the growth mechanic) *and* keeps `tests/test_publish_console.py:114` (`assert "rain/:id" in declared_routes()`) passing unchanged. Update `web/src/app/routes.test.ts:14-27` and the file's header comment.
    - Remove the rain fork from the shell: `web/src/app/app.ts` (`RainStatTiles` and `RainService` imports, the `imports` array entries, `inject(RainService)`, the `rainSurface` computed), `app.html` (the `@if (rainSurface())` tile swap, the activity-log guard, the footer's rain-hub branch), and `web/src/app/components/network-bar.ts:52,123`.
    - Delete `web/e2e/rain.pw.ts`, `web/e2e/matrix.ts:64-65` (`MATRIX_SIZE` falls 56→40 automatically, it is derived at `matrix.ts:90`), and the `RAIN_APP_ID` branches in `web/e2e/chain.ts`.
    - **Run `fledge run web-render` on the full matrix on this branch, read the thrown stale list, and trim `web/e2e/baseline.json` to exactly that set in the same commit.** I computed it from the 56 local `.findings` dumps: seven accepted keys are produced by the `rain` scenario alone — `table-cell:th@390`, `table-cell:th@768`, `text-size:span.chip.waiting`, `text-size:span.connect.you`, `text-size:span.dot`, `text-size:span.gate`, `text-size:span.prize.sub`. (`rain-detail` produces no rain-exclusive key. `span.dot` is also rendered by `network-bar.ts:195`, but that instance never fires a finding.) Confirm against the real run rather than trusting this number — `web/e2e/report.ts:130` skips the staleness check on a partial run, so a `--grep` will hide it.
    - Delete `web/public/brand/corvid-0001.png`; update `web/README.md:31,80-82,193` and `docs/console-plan.md:484-486`.
    - Housekeeping: sweep the 16 orphaned `rain-*` PNGs from `web/e2e/__screenshots__/` and 16 JSONs from `web/e2e/.findings/`. Both directories are gitignored (`web/.gitignore:40,56`), so nothing checks for orphans and they rot silently.

11. **Commit B: the documentation cut.** Must land *before* Commit C, while the files it stops citing still exist — that keeps `pytest` green at every commit.
    - Cut `docs/testnet.md:18-21,97-174`; `docs/releases.md:155-263`; `docs/status.md:39-98` (keeping upkeep 91 as a registry entry, the upkeep-77 wrong-selector story, and the pulse heartbeat) and `docs/status.md:118`; `docs/journeys.md:249-256`; `examples/rain.md`, `examples/community-rain.md` and their rows in `examples/README.md:15-17` (and the now-false "`fledge lanes run local` runs all three").
    - Rewrite the nine backticked paths that go dead, as prose or `https://` URLs (`tests/test_doc_paths.py`'s matcher rejects anything starting with `http`): `docs/releases.md:183,206,219,227,229,235` and `docs/status.md:69,87,91`.
    - Remove or repoint `docs/status.md:75`, `[the rain release entry](releases.md#the-rain-dogfood-deployment)` — its target heading is `docs/releases.md:155`, which this commit deletes, and `tests/test_doc_paths.py::test_every_anchor_a_document_links_to_exists` runs on every `pytest tests/ -q`.
    - Repoint the doctrine that cites moving code but must stay: `docs/arcron.md:677-702,715`, `docs/integrating.md:249-252`, `docs/design/out-of-scope.md:80-86`, `docs/design/scheduling-and-fees.md:56-64`, `docs/book/arcron-working-guide.md:697-698`, `SECURITY.md:84-89`, `AGENTS.md:67-69` and the same line in `CLAUDE.md`.
    - Rewrite `specs/beacon_stub/beacon_stub.spec.md:25,82-83`, whose Consumed By table names `scripts/rain_demo.py` and `smart_contracts/rain/contract.py`. Line 83 is *already false* — rain stopped calling a beacon on 2026-08-29. Nothing in ci checks spec prose, so this rots invisibly if skipped. Add a Change Log row; strict spec-sync requires the section anyway.
    - Leave `docs/reviews/` completely alone, including the parts that are now wrong. `docs/reviews/README.md` states the no-edit policy and `tests/test_app_id_consistency.py:60` exempts the prefix. Copy them to the new repo if useful; never split or edit the originals.

12. **Commit C: the contract, scripts and harness cut.**
    - `git rm -r smart_contracts/rain/ smart_contracts/artifacts/rain/ specs/rain/` — **delete the artifacts directory explicitly.** `smart_contracts/__main__.py:89-93` only clears the output dir of a contract it *discovered*, so removing the source leaves the artifact untouched and `ci.yml:145-150`'s `git diff --quiet -- smart_contracts/artifacts` stays green over a stale spec. That is a false gate, which is worse than a red build.
    - Delete the six scripts, `tests/test_rain.py`, `test_rain_bot.py`, `test_rain_one_draw.py`, `.github/workflows/rain-bot.yml`, `deploy/rain-bot.service`, `deploy/rain.env.example`.
    - `fledge.toml`: delete tasks `smoke-rain` (:139), `smoke-community-rain` (:140), `rain-one-draw` (:148) and the two lane entries in `[lanes.local]` (23 steps → 21). `[lanes.ci]` and `[lanes.endurance]` name no rain task and are untouched.
    - `.github/workflows/ci.yml:229`: edit `for task in build smoke-keeper smoke-rain` by hand. It is the one hand-written task list in CI; the build job at `:141` reads the lane out of `fledge.toml` and needs nothing.
    - `scripts/verify_build.py:40`: `CONTRACTS = ("keeper", "pulse")`. This also narrows `--contract` on `scripts/mainnet_clock.py:153`.
    - Re-base the two floors that would otherwise stop guarding: `tests/test_specs_match_contracts.py:79` (`>= 5`; 7 contracts → 6, so it survives with one of margin — re-base to 6 anyway) and `tests/test_workflow_permissions.py:81` (`>= 3`; 4 workflows → 3, exactly on the floor).

### Phase 3 — settle

13. **Retire the redirect stubs** after an announced window (30 days is reasonable), returning `web/src/app/routes.ts` to three routes and closing the two-address overlap.
14. **Follow-up, not gating:** publish a built `@corvidlabs/arcron` (dist + types + `publishConfig` + a publish workflow) and promote the reusable half of `scripts/keeper_e2e.py` into a supported module, then replace the rain repo's vendored copies with real dependencies.

---

## 5. What breaks, and the fix

| # | Break | Where | Fix |
|---|---|---|---|
| 1 | `tests/test_doc_paths.py::test_every_path_named_in_a_document_exists` — **nine** dead backticked paths, not the five two analyses counted | `docs/releases.md:183,206,219,227,229,235`; `docs/status.md:69,87,91` | Rewrite as prose or `https://` URLs in Commit B. 334 paths are recognised repo-wide, so the `> 40` self-check survives. |
| 2 | `tests/test_doc_paths.py::test_every_anchor_a_document_links_to_exists` | `docs/status.md:75` → `releases.md#the-rain-dogfood-deployment` | Repoint in the same commit that deletes `docs/releases.md:155`. 11 such links exist; the `> 5` self-check survives at 10. |
| 3 | `fledge run web-render` throws on stale baseline entries (a `[lanes.ci]` step) | `web/e2e/report.ts:141-147` | Run the full matrix on the branch with `matrix.ts:64-65` already cut, trim `baseline.json` to exactly the thrown set (expect the seven keys listed in step 10), same commit. |
| 4 | `web/src/app/routes.test.ts:14-27` asserts the exact six-path array | Commit A | Update to the new list; keep the three rain paths as redirect stubs so the shape barely changes. |
| 5 | `tests/test_publish_console.py:114` asserts `"rain/:id" in declared_routes()` | Commit A | Passes unchanged if the routes become redirect stubs. If the owner rejects redirects, delete the assertion in the same commit. |
| 6 | `js/test/rain-abi.test.ts:36` reads `smart_contracts/artifacts/rain/Rain.arc56.json` across the package boundary | `js-test`, a `[lanes.ci]` step | Ordering, not editing: the test leaves in Commit A, the artifact in Commit C. Never the other way. |
| 7 | `.github/workflows/ci.yml:229` runs `smoke-rain` from a hand-written list | LocalNet job (push only) | Edit by hand in Commit C. |
| 8 | `scripts/verify_build --contract rain` disappears, but `docs/releases.md:166` and `docs/status.md:79` instruct the reader to run it against an immutable app | Both lines move to the new repo | Fork `verify_build.py` into `arcron-rain` **before** Commit B rewrites those lines, and point them at the new repo's command. This is a prerequisite, not cleanup. |
| 9 | Stale `smart_contracts/artifacts/rain/` passes CI silently | `__main__.py:89-93` vs `ci.yml:145-150` | Delete it explicitly in Commit C. |
| 10 | `tests/test_specs_match_contracts.py:79` (`>= 5`) and `tests/test_workflow_permissions.py:81` (`>= 3`) land on or near their floors | Commit C | Re-base both to the post-split counts. |
| 11 | `specs/beacon_stub/beacon_stub.spec.md:25,82-83` names two files in another repo, and line 83 is already false | Nothing checks it — not `test_doc_paths` (specs/ is outside `CHECKED`), not `test_specs_match_contracts` (name matching only), not `.specsync/config.toml` (section presence only) | Rewrite in Commit B with a Change Log row. |
| 12 | `docs/ac/j1-j5.md:71,360,381,417,545` and `docs/ac/j2.md:39` cite `docs/status.md` **by line number** | Nothing checks them | Re-derive the citations after the `status.md` cut. Silent breakage; put it on the Commit B checklist. |
| 13 | `tests/test_keeper_topup.py:34,38,159` hardcodes `770130162` and calls it "the rain hub draw" | Fixture data only, not a code dependency | Keep the id, rename the fixture to a neutral target app. Upkeep 91 remains an arcron upkeep whose target lives elsewhere — correct behaviour for a permissionless registry. |
| 14 | `docs/design/1.0.md:112-116` makes the dogfood upkeep a MainNet gate condition | Becomes cross-repo | See D5. Do not leave `1.0.md` reading as though arcron still owns a rain. |
| 15 | Rain app ids `770130162`, `770029154`, `769988156`, `770030875` are not in `tests/test_app_id_consistency.py`'s `SUPERSEDED` set | Neither repo will notice a stale rain id | Port the guard to `arcron-rain` as part of step 7. |
| 16 | Live rain URLs under `/arcron/console/` would silently render the registry (SPA fallback), not 404 | `docs/console-plan.md:249-252` | The redirect stubs in step 10. |

**Test counts after:** pytest 572 → 510; `bun test` in `js/` 180 → 137; `bun test` in `web/` 228 → ~197; Playwright 16 rain click-through tests gone plus 16 of 56 matrix page-states; `[lanes.local]` 23 steps → 21; `[lanes.ci]` unchanged at 14.

---

## 6. Owner decisions

**D1 — How many new repos, and what are they called?**
*Recommendation:* one, `CorvidLabs/arcron-rain`. The `arcron-` prefix, because rain hardcodes `keeperAppId 769891898` and a bare name hides that.

**D2 — Does `subscription` move too?**
*Recommendation:* no. It has never been deployed (no `deploy_config.py`), holds nobody's money, and is the worked example two arcron guides lean on. If it must go, write its replacement in `examples/` in the same commit — do not leave `docs/integrating.md` pointing at nothing.

**D3 — One console for everything, or one console per product? (This blocks every file under `web/`.)**
**DECIDED 2026-08-31: one per product, and the reason is the audience, not the address.** The owner's words: rain "makes more sense that we serve the Rain as the user end UI and page, and not really mix it into the developer, agent, arcron work, as that might get confusing".

That is a stronger line than the one recommended below. It is not that two products need two canonical addresses; it is that they have **different readers**. The arcron console is a developer and agent surface: it shows selectors, reference grades, catch-up policy, escrow runway, and a quarantine panel that explains app-id look-alikes. Rain's reader holds an NFT and wants to know whether they are in and what they are owed. Those two audiences want opposite amounts of chain detail on the same screen, and `app.ts`'s `rainSurface` computed exists precisely to hide one from the other -- a fork that is evidence the shell is serving two products badly rather than one well.

Consequences:
- Rain's surface is a **user-facing page**, not a console. Call it `corvidlabs.xyz/rain/`; "console" is developer vocabulary and the wrong promise to a holder.
- The console cut in Commit A is worth doing properly rather than deferring. The cheap version in §7, which leaves the rain surface in arcron, no longer satisfies the goal: the confusion the owner named is the mixing, and the mixing is in `web/`.
- Restate the rule in `web/README.md` and `docs/console-plan.md` as *one canonical address per product*, with the audience as the reason.

*Original recommendation, which reached the same place on narrower grounds:* one published, wallet-connecting hub per product. The evidence: `rain-stat-tiles.ts`'s own doc comment says "Keeper tiles would lie here: this hub is a different app"; `quarantine.ts:40-48` vouches for exactly one canonical app id per network (the keeper's), so the rain hub gets *no* look-alike protection at the shared address today. Two shells each get a working gate; one shell cannot cleanly guard two ids. `web-keeper/` and `web-govern/` are not hubs -- they are never published and `web-keeper` has no wallet dependency by design. Post-split: arcron owns 3 apps and publishes 1; rain owns 1 and publishes 1.

**D3a — How much of Arcron does a rain user see? (decided 2026-08-31)**
**None of it.** The owner: rain and arcron are "totally different, and 100% not connected, other than they work together. But from a user perspective, they only need to see and know about rain, not arcron." Rain's page may look nothing like the console.

What that removes from `web/src/app/`, all of it today:
- `rain-page.ts:81` and `rain-detail-page.ts:281` link to `/u/{upkeepId}`, a route into the developer console. Both go.
- `rain-detail-page.ts:280` labels a row "Arcron upkeep". Goes.
- `rain-page.ts:49`'s own comment explains that registering an upkeep is `/register`. Rain's reader is not registering an upkeep.

**What must NOT be removed with it:** `rain.service.ts:526,575-578` reads the keeper's upkeep box, and `rain-detail-page.ts:559-560` combines the rain's own due-ness with `roundsUntilDue(upkeep, round)`. That pair is the only thing separating *"this rain is due"* from *"this rain is due and something is coming to fire it"*, and the difference is not cosmetic: `_try_fire` deliberately leaves `last_rain_round` untouched when a rain cannot pay, so a rain that has run dry is due **forever**. Four of the five rains on hub `770130162` are in that state right now. A page that reads only the rain box would tell all four of them "due now" indefinitely.

So the rule is *hide Arcron from the reader, do not make rain blind to it*. Rain keeps reading the upkeep and says "next drop expected around round N" in its own words, naming no keeper, no upkeep id, and no Arcron. If it ever cannot read the keeper, it must say "waiting" rather than "due" -- the one thing it may not do is promise a drop it has no evidence is coming.

This also settles the dependency edge in section 2: the new repo depends on the keeper registry at runtime, as data, and on Arcron in its documentation. It must not depend on it in its vocabulary.

**D4 — Does `beacon_stub` stay?**
*Recommendation:* yes, and this overrides the contracts analysis, which proposed moving it. Moving it drops arcron to 5 contracts (or 4 if subscription also went) and contradicts the standing rule. Rewrite `SECURITY.md:86-87` and `docs/arcron.md:715` to say plainly that it is a LocalNet stand-in kept for another repo's documented return path.

**D5 — Does arcron's MainNet gate keep depending on rain?**
*Recommendation:* keep rain as the dogfood and record the gate as explicitly cross-repo. `docs/design/1.0.md:92-99` chose rain precisely because it needs `SKIP_AHEAD` and pulse "proves nothing about #7 on its own"; repointing at pulse is cheap but silently weakens the gate. Accept that a rain outage in another repository stalls arcron's clock, and say so in `1.0.md`.

**D6 — Who owns byte-for-byte verification of the immutable hub?**
*Recommendation:* fork `verify_build.py` into `arcron-rain` (it imports only `scripts.network`) as a prerequisite of step 9, and drop `"rain"` from arcron's tuple in Commit C.

**D7 — Packaging: publish `@corvidlabs/arcron` and a Python package first, or vendor?**
*Recommendation:* vendor for the first cut. **Amended 2026-08-31:** the package is published, as `1.0.0-alpha.3` on GitHub Packages under the `alpha` dist-tag, by `.github/workflows/publish-js.yml`. The sentence this decision used to rest on — "there is nothing published to break" — is no longer true and is removed. The recommendation survives its own justification, for three reasons that are still true. Installing from GitHub Packages requires an auth token even for a public package, so "vendor" and "install" are closer in cost here than they would be against npmjs.org. The package still ships raw TypeScript (`"main": "./src/index.ts"`, `tsconfig` is `noEmit`) and resolves outside a bundler nowhere, though `arcron-rain` is an Angular/Bun repo and would not care. And `pyproject.toml:7` still sets `package-mode = false`, so the Python half has to be vendored regardless. What the publish *does* change is the cost of Commit A: `./rain`, `./rain-abi` and `./rain-txns` are now a published surface, 69 of 134 root exports. Because the published version is a prerelease, removing them is not a major bump — the first stable `1.0.0` is simply published keeper-only, and `^1.0.0-alpha.3` already admits it. That is the whole reason a prerelease number was chosen over `1.0.0`. Copy ~65 TS lines and ~5 Python helpers, accept the drift, and keep publishing a stable version as follow-up work rather than a gate in front of the split. (`docs/console-plan.md:437-448` records this hazard already biting once, fixed with `prebundle.exclude` in `web/angular.json:76-80`.)

**D8 — Where does `FOUNDATION_BEACON` live?**
*Recommendation:* stays in arcron with the beacon-id table at `docs/arcron.md:707-715`; the rain repo links to it. It has no code consumer after the split, but `tests/test_multisig.py:295-329` reads it, and that test was deliberately re-pointed off `specs/rain/rain.spec.md` onto deployed approval programs on 2026-08-29 *precisely so rain's churn could not break it*. Moving it means deleting the test and the table in the same commit.

**D9 — Does `arcron-rain` write the missing spec files?**
`specs/rain/` is one file where `specs/keeper/` and `specs/pulse/` are five. Copying `.specsync/config.toml` verbatim gives the appearance of the same strict gate without the substance. *Recommendation:* write `requirements.md`, `tasks.md`, `testing.md` as part of step 7 — the rain repo is the one that goes public with a live money-holding contract.

**D10 — Is `arcron-rain` public from day one?**
*Recommendation:* private until steps 0.4 (the home-directory mnemonic path) and 0.6 (the `abandon` client) have landed. Then public.

**D11 — Do the arcron rain routes become redirects or deletions?**
*Recommendation:* redirects for 30 days, then deleted. It keeps live links working, keeps `test_publish_console.py:114` green, and costs three tiny components.

---

## 7. Cost, honestly

| Phase | Work | Estimate |
|---|---|---|
| 0 — pre-flight (cancel 79, app ids, secret path, keeper-app-id flag, `abandon` client) | 5 small PRs, one of them a real contract-client addition | 1–1.5 days |
| 1 — seed `arcron-rain`, green lane, verified bytecode | The bulk of it: the standalone console needs its own ~200-line chain service, app shell, e2e harness and vendored brand | 2.5–3.5 days |
| 1 — publish the rain console + site change | | 0.5–1 day |
| 2 — Commit A (client + console cut) | Mostly `web-render` iteration | 0.5–1 day |
| 2 — Commit B (docs) | 3 files cut surgically, 9 paths, 1 anchor, ~8 doctrine repoints, 6 line-number citations | 1–1.5 days |
| 2 — Commit C (contract + scripts + harness) | Almost entirely mechanical | 0.5 day |
| 3 — follow-ups (publish packages, rain spec files, retire stubs) | Deferrable | 2–3 days |

**Total: roughly 6–9 working days to the clean end state, of which about 6 are gating.**

### The cheapest version that still achieves the stated goal

The owner's complaint was about *smart contracts* in the wrong project, not about Angular routes. That complaint can be satisfied in about **2–3 days** by doing Phase 0 plus a reduced Phase 2:

- Move the contract, spec, three test files, six scripts, both example docs, the `fledge.toml` tasks, the workflow and deploy units, and the documentation sections.
- Move `js/test/rain-abi.test.ts` with the artifact (it is the only file that forces the js/contract ordering).
- **Leave `js/src/rain*.ts` and the console's rain surface in arcron for now.** They keep compiling and keep serving the live hub from the canonical address; `web/e2e/chain.ts` keeps stubbing it; `baseline.json` needs no trimming; no flag day, no overlap window, no second address.

That gets `smart_contracts/` down to six coherent directories and gets the rain product's money, secrets, deployment and release record out of arcron. What it defers is honest and should be written down: arcron's published console and its client library still ship a rain surface whose contract now lives elsewhere, with no compiler enforcing that the two agree. That is a real debt — the same class of debt `js/src/index.ts:5-11` argues against — but it is a *visible* debt on a decoder that has been stable, not a live-money hazard.

Do the cheap version first if the console question (D3) is not yet settled. Do not do the console cut without D3 answered in writing: every other move in this plan is mechanical, and that one is not.