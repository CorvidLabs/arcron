# Arcron: Kimi 3 verification of the fixes

Date 2026-08-26. Run headless via `rune run -- kimi -p`, 2,171 seconds.

Trimmed to the report: the raw run was a 371KB transcript of tool calls. The
findings below are unedited, including the three that were about mistakes in
the author's own repair.

---

Area 1 — Governance signing: **HOLDS**

  `ms.refusals()` (`scripts/multisig.py:269-367`) collects rather than short-circuits, and each clause is fail-closed:

  - **Non-app-call**: keyed on `txn.type == "appl"` (`multisig.py:292-298`), no falsy escape. ✓
  - **App id**: `is_app_call and in_file != app_id` (`:299-303`). A create passes only with app-id 0. Residual: `--app-id` *defaults* to 0 (`scripts/govern.py:353`), so forgetting the flag is indistinguishable from naming a create. The other refusals still bind. Minor.
  - **Genesis**: tuple membership (`:304-309`); LocalNet's tuple matches reality — this machine's node answers `dockernet-v1` (demo logs, `docs/arcron.md:505`). The third entry `devnet-v1` I could not tie to any real chain; harmless unless a real network ever claims it. ✓
  - **Sender**: blob address vs configured (`:310-314`) — the right thing to compare. Two residuals: the check silently disappears when the *signing* machine has no multisig configured (`expected_address=None`), with no warning on the sign path; and `txn.sender` itself is never compared to the blob address (an on-chain-invalid mismatch otherwise, so low).
  - **Rekey/close**: checked for every type (`:315-324`). Gap: `asset_close_to` is never checked — reachable only under `--account-txn`, and `describe_transaction` doesn't surface it either. Suspected, low.
  - **Fee ceiling**: compares the literal decoded fee field against 10,000 (`:325-330`). Governance-produced files use raw `algod.suggested_params()` — no algokit-utils padding reaches the file — so honest files sit at ~1,000 µALGO and a hostile `flat_fee` is caught (tested at `tests/test_multisig.py:173`). ✓
  - **Escape flags** are opt-in and not triggerable by the file. Fine.

  Deliberately unchecked, confirmed: `on_complete` (a DeleteApplication file with the right app id passes refusals — but the keeper contract has no delete route; TEAL main switch asserts `OnCompletion == 0` for all ABI methods and only the update route accepts `UpdateApplication`, `Keeper.approval.teal:35-60`, so it dies on-chain) and `app_args`, which are shown as undecoded hex — **a freeze file still arrives as unlabeled hex** (my earlier M8 point; updates got the digest treatment, freeze got none). Six attack tests plus the genuine-update test exist and pass (21/21 in `test_multisig.py`).

  Also in range: `0c2f0f1` wires `require_mainnet_multisig()` into `load_network` (`scripts/network.py:74-99,125`) so every script gets it; fails closed, permutation-tested. HOLDS.

  ## Area 2 — govern create: **HOLDS** (one residual by the fix's own standard)

  - **Extra-pages formula** (`govern.py:194`): `(total-1)//2048` is exactly `ceil(total/2048) − 1`. Boundaries: 2048→0, 2049→1, 4096→1, 4097→2 — now pinned by `test_the_extra_pages_formula_holds_at_the_page_boundary`, including the live 2,108-byte case. ✓
  - **Schema from the spec** is safe: `create` rebuilds unconditionally (`govern.py:185`), `rebuild()` raises on failure, and HEAD's `Keeper.arc56.json` carries 2/0 + 0/0, matching the previously hand-typed values. A too-small schema fails the create on-chain rather than doing damage. ✓
  - **The unchecked permanent field I found — create files were invisible to holders — was fixed mid-review** by `3390568`, and the fix is real: `describe_transaction` now detects a create (`multisig.py:181-199`) and prints every permanent field, and `sign` rebuilds the tree and *refuses* carried programs that don't match (`multisig.py:356-366`, `govern.py:320-326`), tested in both directions. The typed confirmation is now full-address with no `--yes` (`govern.py:445-450`).
  - **Residual**: schema and extra pages are *displayed* but not *refused* on mismatch. A create carrying the right programs but an inflated schema or page count passes `sign` — the commit's own standard ("printing a hash asks somebody to compare it; this is the comparison") is met for programs but not for the other two permanent fields. Bounded: too-small fails on-chain, too-big permanently wastes creator MBR. No theft. Low.
  - Minor: the dirty-tree check runs `git status` without `cwd=REPO` (`govern.py:174-176`); invoked from the wrong directory it can pass vacuously. `verify_build` pins `cwd` (`verify_build.py:104-107`) — sibling inconsistency.

  ## Area 3 — Console trust banner: **INCOMPLETE**

  The main attack is closed, confirmed: the identity notice is ungated (`trust-banner.ts:26-50`), per-box `try/catch` drops one row (`arcron.service.ts:259-271`), undecodable count is itself a notice, `canSubmit` requires `ready` (`register-form.ts:359-369`), notices are ranked, the way-back button exists, the frozen test imports the real `isFrozen`, and the banner has real tests — **32/32 bun tests pass**, including "fires while the connection is failing". I found **no remaining path** where a register can proceed against a non-canonical app without the identity warning rendered: the notice needs no chain data, and the write path keys off the `appId` signal, not displayed data. TestNet's `defaultAppId` is the current live app (`js/src/networks.ts:60`). LocalNet correctly shows the "no published app recorded" warn instead.

  The generation guard is **half-applied**: `refresh()` guards only `status`/`error`/`lastRefreshed` (`arcron.service.ts:220-227`). Every write inside `refreshApp` — `nextUpkeepId` (:236), `frozen` (:238), `appAccount` (:242), `upkeeps` (:249) — and `undecodableBoxes.set()` (:274) is **unguarded**. Under rapid app-id switching, a stale slow read (a hostile app with many boxes = many HTTP fetches) can land *after* the switch and repaint its registry, and transiently a `frozen=true`, under the new id, until the newer refresh or the next poll (≤2.5 s) overwrites it. That is exactly Fable's C3 scenario, and the comment at :197-203 claims it is closed. Impact is bounded — the banner's identity notice doesn't depend on those signals and payments key off the `appId` signal — so this is transient mis-paint, not theft. Low severity, but the fix does not do what its comment says.

  Sibling miss, confirmed: the status gate was added to `register-form` but not to the registry-table row actions — `top_up`/`cancel`/`execute` gate on row state and `busy` only (`registry-table.ts:123,131,140,163`). `top_up` commits money on the same philosophy the register fix stated. Minor: `reset()` never clears `undecodableBoxes` (`arcron.service.ts:287-296`), so a stale count survives an app switch until the next good read (over-warning direction).

  ## Area 4 — Rain and deadman: **HOLDS** (verified by execution, not reading)

  - **Deadman**: `arm` requires `deposit > APP_BASE_MBR` and books `escrow = deposit − floor` (`deadman/contract.py:88,106`); `claim` pays only the booked amount. The demo asserts the split and dropped the silent pre-fund (`scripts/deadman_demo.py:39-43`). **I ran it: passes end-to-end** — fired by a keeper at the deadline, beneficiary pulled 900,000 µALGO by inner payment leaving exactly the floor. The correction is true, not merely different.
  - **Rain**: the claim gate mirrors `enter`'s four checks line for line (`rain/contract.py:385-393` vs `:202-207`), including the prize-not-a-ticket check that was the asymmetry. **No remaining asymmetry in the checks.** The honest limits are now stated identically in the contract docstring, the spec (error case 127), and the example: the token-walk still works (one transfer), a seller must reacquire to collect. One thing the docs still don't spell out: an allocation whose winner *never* reacquires is stranded forever — same class as any unclaimed prize, but "forfeits" reads like it goes somewhere. **I ran the gated demo: passes**, including the winner claiming against its own NFT and the double-allocation accounting. Mocked tests cover both directions (88/88 passed).
  - The dormancy-rule stragglers now state the correct rule (`docs/arcron.md:158,638`, `specs/keeper/testing.md:42`). ✓

  ## The reentrancy spike (`0c2f0f1`): the property holds; the repair is **BROKEN** two ways

  - I reproduced the author's claim with a fresh probe: all three variants rejected, `attempt to re-enter 2834`, assertions pass. The property is now genuinely measured.
  - **But the new assertion is wrong**: `if drained > upkeep.fee_cap or ...` (`scripts/spike_reentrancy.py:157`). `_register` always sets `fee_cap=0`, and in the keeper 0 means "escalation disabled, fee is always `fee_per_execution`" (`keeper/contract.py:85-87,179`) — so *any* accepted healthy execution trips it. It passes today only because all variants are rejected.
  - **And it is not reproducible on a used chain**: `deploy()` reuses the existing probe ("OperationPerformed.Nothing"), whose `probes_run ≥ 1` makes `reenter` return early (`resource_probe/contract.py:170-171`) *without attempting reentry at all*. My first run measured nothing and then failed for the wrong reason. The spike is green only on a fresh chain; the deadman demo already solved this exact problem by bare-creating a fresh instance per run.
  - The commit's rationale ("a re-entering target whose inner call is refused still completes its own execution") is also wrong about the AVM: a refused inner call fails the whole transaction; the accepted path only occurs when no reentry was attempted.

  ## Introduced by the fixes

  The spike's `fee_cap` assertion bug (above); `canSubmit` now dies on any transient poll failure (intended, but a behavior change); register payment notes change txids (harmless); `export_unsigned`'s note still says "Sign with scripts.multisig sign" (`multisig.py:128`) — no such CLI exists; it's `govern sign`.

  ## What I did not check

  The `govern create → sign → submit` flow end-to-end with a real multisig (unit-tested logic only; CLI wiring read, not executed — my attempt at a live run is the one thing I couldn't do without inventing holder keys); the console in a browser (the repaint is reasoned, not observed); the dirty-tree rekey sweep (out of scope, uncommitted); keeper e2e, `verify_build` against TestNet, the keeper bot, notifier; Foundation *provenance* of the TestNet beacon id (both apps exist on-chain, MainNet's id is externally corroborated by two independent sources, TestNet's is consistent across seven repo locations and pinned by a test, but I found no authoritative Foundation page); the `efe6d19`/`9561cd7` docs commits beyond a skim (they resolve the status.md self-contradiction about the MainNet gate; reasoning looked sound).

  ## Confidence: **8/10**

  Execution-backed on areas 4 and the spike; source-backed elsewhere. Docked for the unexecuted governance CLI flow and the unobserved console race.

  ## The single thing most likely wrong that I did not check

  **The create-file signing ceremony as holders will actually perform it.** The refusals are unit-tested, but nobody has run `govern create` → carried the file → `govern show`/`sign` on a *different* machine with a *different* checkout — which is where the rebuild-and-compare turns into either a clean refusal (holder on the wrong tag, or a dirty tree — likely in practice, and the error must be legible rather than looking like the file is hostile) or a false pass. That flow, not the contract, is what the MainNet create now depends on, and it has never been executed.

To resume this session: kimi -r session_86b608c0-7c07-4661-8b8a-d6885ab6a9c3
