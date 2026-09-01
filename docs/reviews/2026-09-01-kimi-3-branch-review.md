# Kimi 3 on the audit branch

Date 2026-09-01. Run headless via `fledge rune run -- kimi -p`, 1157 seconds,
against `review/audit-verification`. One of three independent passes over the
same branch, each given the same prompt and none shown the others' answers.

Kept unedited, including the parts that are wrong and the parts this repository
then acted on. The prompt asked each model to try to refute the branch's own
new finding, to look for what two same-family passes would not think to check,
and to end with a confidence number it was told to keep honest rather than
agreeable.

**Confidence: 78 / 100.** What was done about it is in
[`2026-09-01-opus-5-audit-verification.md`](2026-09-01-opus-5-audit-verification.md),
section 7.

---

## 1. The bought-lateness finding: I could not refute it, and it is worse than the verification says

**It reproduces, exactly.** `poetry run python -m scripts.spike_hostile_target --network localnet` ran green for me: honest keeper paid 7,600 (excess 1 → 4,000 + 36,000·1//10), one 1,000 µALGO blocking call, honest keeper blocked at cost 0, attacker collected 25,600 (excess 6 → 4,000 + 36,000·6//10), net +21,600. Every number in the verification's table checks against `smart_contracts/keeper/contract.py:390-400`. The six-cycle arithmetic also checks: 7,600 + 25,600 + 4×4,000 = 49,200 against 24,000 nominal; the 3,600 attribution to LocalNet granularity is honest; "one round late costs 90% of base" on a 10-round interval with a 10x cap is correct (3,600/round of ramp).

**The worse version the verification did not find: the fallback is the drain.** The verification's attack only pays when the escrow covers the escalated fee, because below that `execute` falls back to base (contract.py:406-407) and the attacker nets 4,000 − 1,000 − 3,000 = 0. The verification itself corrected the audit's I1 — a keeper can decline the fallback by topping up in the same group — but never combined its two findings. Combined, they invert the protection: the attacker *over-blocks* on purpose to push the escalated fee above the escrow, triggers the fallback, declines it with a top-up, and takes the **entire remaining escrow in one execution**, not one capped fee.

Call path: `guarded` directly (twice, to hold the window shut a full extra interval so the fee reaches the cap), then one group `[Payment(fee − escrow → app), top_up(uint64,pay), execute]` — `top_up` binds payer to caller (contract.py:278-280), so the attacker's own money goes in and comes back inside the fee. Measured on LocalNet (`scratchpad/spike_hostile_combined.py`, untracked, rerunnable): base 4,000, cap 40,000, interval 10, gap 6; one honest run leaves escrow 32,400, **below** the cap, where the plain attack breaks even. Two blocking calls (2,000), top-up 7,600, group fees 5,000; `execute` pays the cap 40,000; **escrow after: 0; attacker net +25,400**. Who profits: the attacker, by min(fee, escrow) minus ~7,000 of fees per episode — and when fee > escrow that is the whole escrow, extracted for one execution's worth of service. The general strategy: if the escalated fee exceeds the escrow, top up the difference and drain; otherwise just collect. Over-blocking to force the fallback is worth 1,000 per extra blocking call whenever escrow − fee exceeds that.

This sharpens the severity claim. "Bounded by the `fee_cap` the creator chose" is true per execution but the per-episode bound is the escrow, and a small escrow — which a creator might think limits exposure — is exactly the case that gets drained whole. I agree it remains advice rather than a contract change: no invariant is broken, the target property is only knowable to the creator, and it self-heals (one cycle after the attacker stops, not two — the verification overstates; after the escalated execution `next_due = due + interval > last_serviced`, so the next on-time execution has excess 0 and pays base). But the advice should say "your escrow is the stake, not your cap."

**The remedy claims check out, with one omission.** The ramp is genuinely in the program, not the box: contract.py:390-400 computes `fee = base + (cap − base)·excess // interval` with `excess` capped at `interval`, all from existing box fields. Flattening it (say `excess` capped at `4·interval` — overflow bound 4·10⁹·10⁹ = 4e18, still inside uint64) needs only `update`, no struct change, and `freeze` would close that window. Correct as claimed, and correctly flagged as the one finding whose fix deadline is the freeze. What the verification doesn't say: "set `fee_cap = 0`" is only available at registration — nothing mutates `fee_cap`, so a live upkeep's creator must cancel and re-register. And the advice was never landed: `docs/integrating.md` and the console carry no such warning, so the "immediately" recommendation currently lives only in a review document.

**What LocalNet hides: the reopening race, which neither document mentions.** When the window reopens, every polling keeper's simulate succeeds and everyone sends. The spike is uncontested by construction. The attacker's real edges: they know the reopening round exactly, and losing costs only the 1,000 block fee with an immediate re-block available, while honest keepers' blocked attempts cost them nothing and teach them nothing. Break-even win probability on the live registry's numbers (8,000–10,000 µALGO gaps) is roughly 10–15%; the timing edge makes that comfortably reachable. So the race dilutes the attack but does not close it. The omitted "honest limit" cuts the other way too, though: **on the live registry every keeper is one operator** (this branch's own README correction), and a singleton keeper needs no blockable target at all — it simply executes late and collects the escalation, free, against `pulse`-style always-succeed targets where blocking is impossible. The audit filed "colluding keeper set" economics as unestablished; the trivial case needs no collusion when the set has one member, and under SKIP_AHEAD the `due > last_serviced` gate is always true, so *every* deliberately-late execution escalates. Bounded by the cap, but it's the cheapest version of the attack and it works on exactly the targets the verification calls immune.

**The "22x" is the most flattering defensible framing.** 21,600 is net of all costs, but the denominator counts only the 1,000 blocking fee, treating the 3,000 execute fee as free because any keeper pays it. Against total outlay (4,000) the return is 5.4x net; against the honest-keeper baseline (who would have netted 4,600 on the same upkeep) the incremental profit is 17,000 on 1,000, i.e. 17x. Same conclusion, less garnish; `docs/reviews/README.md`'s "22x return, measured" should name its denominator.

## 2. What both passes missed

Beyond the combined drain above, one code-level item, argued from source (I did not reproduce it on chain): **the "best effort" ASA refund in `cancel` is best-effort for one creator at the expense of the others.** The app account's holding of a fee asset is shared across every upkeep naming it, while `asset_balance` is per-upkeep book. After a partial clawback, `held < bonus` makes `cancel` transfer the *entire* remaining holding (contract.py:329-332, `bonus = held`) — including other upkeeps' book balances. Worked example: upkeeps A and B each escrow 100 of asset X; the issuer claws back 150 of the app's 200; A cancels and receives 50, half of it B's; B's box still says 100, `pays_bonus` (contract.py:435-442) silently stops paying because the actual holding check fails, and B's own cancel finds nothing. Reachable only via clawback (deposits and executions move book and holding together; direct transfers only add), so it requires a clawback-enabled `fee_asset`, which is already a trust-the-issuer choice — informational, not a bug, but neither document notes that the cross-upkeep sharing exists, and it is the same "sibling with the identical shape" pattern this repo keeps rediscovering.

## 3. The code on the branch

- **`scripts/node_retry.py` — the POST-retry argument is sound.** Vanilla algod never emits 403 or 429 (401 for bad tokens, 400 for logic errors); both statuses come from intermediaries that refuse before forwarding, so the request never reached algod and replaying a POST cannot submit twice. The residual case — some non-Nodely gateway refusing after forwarding — degrades safely: a resubmitted identical signed transaction dedups by txid, so the caller gets a false failure, never a double execution. Excluding 5xx is right: a 502 can follow acceptance, and this module cannot distinguish. The install-twice guard, the indexer body-marker fallback, and the ~7.5s worst case (0.5+1+2+4) all check out. One wrong number: the docstring's "four retries take the odds of losing every one from 9% to about 1 in 15,000" matches 0.09⁴ = 1/15,242, i.e. four *attempts*; the code makes five, 0.09⁵ ≈ 1/169,000. It understates itself 11x. Relatedly, verification §5 says clients "retry a 403 or 429 up to five times" — the code retries up to four times (five attempts). Both are wrong in the safe direction, but both are wrong in documents that are checked in as evidence.
- **`registry_health.py` solvency — correct bookkeeping.** Spendable = amount − min-balance already excludes box and opt-in reserves; the escrowed sum contains no MBR, so nothing is double-counted, and `cancel`'s refund (balance + MBR) is exactly covered. `read_escrowed`'s placeholder arguments (1.0, 0) are honestly documented and provably dead: they feed only `runway_days`, `rounds_late` and `can_pay_fee` (`effective_fee` clamps lateness at 0 → base), none of which the `escrow` field reads. Fragile if someone later reads more from the result, fine today.
- **`govern.py status` — the line is right** and matches `health`'s semantics; "anyone can fix this with an ordinary payment" is accurate. Cosmetic: the SHORT BY warning splits one sentence across two `logger.warning` calls.
- **`test_the_fallback_to_base_is_the_keepers_to_decline` — proves its mechanism** (top-up + execute → cap paid, escrow zeroed, keeper net == prior escrow) and its cited LocalNet numbers are internally consistent (40,000 − 15,600 = 24,400; 40,000 − 15,600 − 3,000 − 2,000 = 19,400). One overstatement in the docstring: "strictly better than base whenever the fallback would have triggered" fails at the exact boundary `balance == base`, where the keeper nets base either way. Trivial, but the docstring asserts more than the test.
- **`spike_hostile_target` in `[lanes.local]` — yes, and it is deterministic enough.** It asserts properties only an AVM can answer, with wide margins (25,600 vs ≤7,600; net +21,600), hard-refuses any non-LocalNet network, deploys a fresh probe per run, and dev-mode round poking makes the schedule exact. I ran it green in about five minutes. Its blind spot is structural, not flaky: the lane has no competing keeper, so the one contested step (executing when the window reopens) is uncontested by construction. Fine for a lane; worth knowing it can never catch a regression in the race economics.

## 4. Document errors beyond the two the verification caught

The audit's numbers all recompute (F1's six numbers, the MBR figures — 62,100 is independently pinned by `js/test/upkeep.test.ts:173` — the 98% figure, I1's arithmetic). I found no new factual error in the audit. In the verification and the branch, beyond the items above: "within two cycles" (one); "up to five times" (four retries); the 1-in-15,000 docstring; "22x" framing; `fee_cap = 0` needing cancel-and-re-register; the recommended creator advice not landed anywhere a creator would see it. I could not verify any TestNet-dependent claim — "7 of 33 upkeeps" escalating with 8,000–10,000 gaps, four targets with multiple upkeeps, the 54.201/54.217 solvency lines, the Nodely quota measurements — because TestNet was off limits; they are internally consistent but unverified by me.

**The largest document problem is the second commit's own contradiction.** Commit `61d9a5a` corrects README and START-HERE to "the count of upkeeps registered by somebody who is not us is zero" — and leaves `AGENTS.md:67-74` grounding the dogfood decision on "26 of the 33 live upkeeps now belong to somebody else", and `docs/design/split.md` D5 keeping "22 of the 28 live upkeeps belong to accounts other than the deployer, across seven addresses" with the conclusion "the gate no longer rests on a single rain we drive". The corrected attribution guts that premise: all seven addresses are one operator, so registry-as-dogfood is a self-driven soak test — precisely what rain was. "The point survives without it" is asserted, not argued, and as written it does not survive: the difference between six outside addresses and six of our own is the difference between adoption evidence and a soak. In a repository whose documents are its evidence, and whose own reviews named fix-one-site-not-the-siblings as the recurring failure (#105), shipping the correction at two sites while the derivative claims stand on the old premise at two others is exactly that failure. The MainNet-gate evidence base should be restated honestly: sustained operation across cadences and policies by the operator, zero third-party usage to date.

Verification status of what I ran: 535/535 unit tests, `specsync check --strict` green, `scripts/attacks` 3/3 refused for the right reasons, the hostile-target spike green with the verification's exact numbers, and my combined-attack experiment confirming the full-escrow drain (+25,400, escrow to 0). I did not run the full `fledge lanes run ci` (web lanes) — nothing on this branch touches `web/` or `js/`. The working tree is untouched except my untracked experiment file.

CONFIDENCE: 78
BLOCKERS: Reconcile the attribution correction with AGENTS.md's dogfood bullet and docs/design/split.md D5's conclusion — the branch currently ships "zero outside users" in two documents and the opposite premise in two others, and the MainNet-gate evidence reads differently once corrected
Amend the verification's §3 honest limits: the per-episode bound is the escrow, not the cap (fallback-decline combination, measured, +25,400 draining a 32,400 escrow where the plain attack nets 0), the reopening race is unmentioned, and "set fee_cap = 0" requires cancel-and-re-register for live upkeeps
Correct the two wrong numbers in checked-in evidence: node_retry.py's "1 in 15,000" (five attempts is ~1 in 169,000) and the verification's "retries up to five times" (four retries, five attempts)
Land the fee_cap = 0 advice where the verification says it belongs (docs/integrating.md, console), or track it explicitly — right now the "immediate" recommendation exists only inside a review document

To resume this session: kimi -r session_99f77235-19b9-4935-baed-44655c425d2f

---

# Second pass, after the fixes

Re-run the same day against the same branch with the blockers above worked on,
this report handed back so it could mark each one closed, partly closed or
open. **Confidence 78 to 88.** What it found this time was mostly created
by the first round of fixes.

---

## My earlier blockers

- **Attribution contradiction (AGENTS.md dogfood bullet, split.md D5)** — CLOSED. Both rewritten to say zero outside users and soak-test-not-adoption explicitly, and the third sibling (`docs/status.md:52`) they found on their own is corrected too; no live document still argues from outside adoption.
- **§3 honest limits (escrow-not-cap, reopening race, `fee_cap` needs re-register)** — CLOSED. §3 now states all three, the escrow composition is measured and asserted in `measure_escrow_bound` (I watched it drain an escrow to 0 on LocalNet), the backoff paragraph correctly cites `INNER_FAILURE_MARKER`/`MAX_BACKOFF_ROUNDS`, and the write-once caveat is in §3 and `docs/integrating.md`.
- **Two wrong numbers in checked-in evidence (1-in-15,000; "up to five times")** — CLOSED. Docstring now says `0.09**5 ≈ 1 in 169,000` with `MAX_ATTEMPTS = 5`, and §5 says four retries, five tries.
- **Creator advice not landed** — CLOSED. `docs/integrating.md` now has the blockable-target warning with measured numbers and the `fee_cap`-is-write-once note.

## The other claims I verified

- **Claim 1 (spike exit-0)** — CLOSED. I walked every early-return path in all six measures: each unmeasured outcome now returns a failure string and `main` exits non-zero; `_send` re-raises anything not matching the AVM rejection markers.
- **Claim 2 (variants measured and asserted)** — CLOSED, with a caveat below. All three variants exist, assert, and the spike ran green twice on my machine.
- **Claim 3 (raise_fee over spread-ramp)** — the right call. The ramp remedy assumed an honest keeper gets through once; against the shipped bot's one-hour backoff that event doesn't exist, against cooldown>interval there is no attacker to outlast, and against the sibling the block is scheduled. A creator-signed raise bounded by `fee_cap` puts the bid with the party who pays and can see the target; no box change, update-reachable, and the freeze ordering is correctly identified as the binding decision. It also honestly leaves the liveness question open rather than pretending to fix it.
- **Claim 4** — CLOSED. `read_solvency` raises on missing `min-balance` (with tests), `read_upkeeps` reuses the paginated `scan_upkeeps` (pagination tests included), `govern status` catches the refusal and warns instead of tracing back. The deliberate retention of the floor fallback in `read_keepers` is argued correctly.
- **Claim 5** — CLOSED. The test now runs both paths and asserts the comparison from measured state (`_fee_paid == cap`, balance 0 vs `held − base`).
- **Claim 6** — CLOSED. txid-idempotency argument replaces the unverifiable CDN one; duplicate-after-replay is recognized and answered with the id hashed from our own blob, gated on `replayed` so a first-try duplicate still raises; 5xx retried for GET/HEAD/OPTIONS only; all covered by named tests.
- **Claim 7** — CLOSED, and the reversal is right. I checked the evidence myself: `launchctl` shows `xyz.corvidlabs.arcron.keeper.testnet` running, and its log holds 11,543 scans and 1,993 `scan_failed` 403s — the document's 11,541/1,964 plus log growth. A bot scanning 33 boxes every ~5 rounds lands at exactly the counter's magnitude, so "shared across everyone, sending less won't help" is refuted whichever way the bucket is keyed.
- **Claim 8 / spot-checks** — CLOSED. ci lane green (14/14), 559 tests pass, specsync strict green, spike green twice, tree clean.

## What the fixes introduced or missed

- **The quoted spike numbers are not stable across runs.** My first run of the identical, unmodified spike printed 32,800/+28,800, fees [7600, 29200, 22000, 29200], 3-of-4 shut out / 62,800 / +44,600, and 28,800 escrow / 11,200 top-up / +23,800 — different from the document on all four scenarios. My second run reproduced the document's figures exactly (25,600/+21,600; 22,000×3; 4-of-4/55,600/+45,600; 32,400/7,600/+27,400). The assertions are inequalities and held both times, and every qualitative finding is robust — but the verification §3, `docs/integrating.md`, and the commit message all quote one sample's exact figures as measured fact, and a reader rerunning has roughly even odds of watching the spike contradict them. This is the "checked-in evidence that doesn't reproduce" failure in a milder form; the docs should say "about", or the spike should pin its phases.
- **`measure_sibling_blocker` keeps one green-without-measuring path.** `net_gain > 0` can be satisfied by the blocker's own base fees (+1,000/cycle) with `paid_to_attacker` empty — nothing asserts the attacker ever collected an escalated fee from the victim, which is the variant's core claim. Small, but it is the same shape this commit exists to fix.
- **One unfailable assert survives in the fallback test.** `took_under_the_bypass == held` with `shortfall` defined as `cap − held` is still true by construction, and the new comment ("both numbers come from what the contract did") overclaims for that line. Cosmetic now that the real comparison is asserted.

## Still wrong in the documents

- §5's measurement paragraph has sub-1% arithmetic slips: 11,541 × 37 = 427,017, not the stated 429,957; 429,957/1.958 days = 219,590/day, not 219,564; 5.53 × 86,400 = 477,792, not 477,403. Direction unaffected — I verified the underlying log independently — but this is a paragraph whose whole job is to be measured rather than asserted.
- §1's table row "508 unit tests pass | 508, unchanged" is stale at 559. Dated historical record, tolerable, but it now reads as a current claim.
- Nothing else. The MainNet answer, the freeze-ordering argument, and the corrected limits all hold up.

CONFIDENCE: 88 - that this branch is correct, its documents are true, and it is safe to merge
BLOCKERS: NONE

To resume this session: kimi -r session_be155098-0c7b-48f4-9032-8e353d6f707f
