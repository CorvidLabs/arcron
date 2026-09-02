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

---

# Third pass, on the two items left open

The liveness fix and the request-load fix, reviewed the same day they landed.
**Confidence 89.** All three passes were asked for an honest number rather
than an agreeable one, and this one was also asked whether 95 is the right bar
for a repository that has produced a previously undiscovered true-sounding
claim in every round including this one.

---

> The third pass, as first committed, carried about 700 KB of the reviewer's
> working transcript between this line and its verdict: its reading plan, its
> tool output in 64 KB chunks, a copy of `fledge.toml`, the diffs it was
> reviewing. That was never review text and was cut on 2026-09-02. The verdict
> below is unchanged from what was appended.

• All checks are in. Here is the third pass.

## The two open items

**1. Liveness — CLOSED, with one boundary correction.** The classifier does what the commit claims for the failure shape that was actually reported: an assert-style refusal from inside the target carries algod's `inner tx N failed` attribution, gets the 1,2,4…→64-round schedule (pinned by ten new tests in `test_keeper_backoff.py`), and e2e stage 14 verifies the classification and the `app=/pc=` site out of a real node's own error text, not a made-up string. The monotonicity argument behind "conditional by construction" checks out against `contract.py:361-489`: schedule, funding and box all assert before the inner call, and — checking the one place the claim could leak — the keeper's own fee payment can never be the failing inner tx, because every funding path binds its payment (`register`, `top_up`, `opt_in_asset` all assert `sender == Txn.sender` and receiver is the app), so book escrow ≤ spendable invariantly; an execute that passes `Insufficient funding` cannot overspend. An execution clearing the streak opens nothing: clearing only makes the keeper retry sooner, and `advanced` is read from the box, which no target can forge. The "twenty refusals an hour" arithmetic holds (1,286/64 at the cap; the min-with-interval clause makes it ~64/hour on the 20-round cadences, which is the right trade). What an attacker can still buy: the reopening round, and a blackout on any target whose cost — rather than its assert — is poke-able (below).

**The correction:** the docstring's "The marker is `inner tx N failed`… A target cannot suppress it" is false, and this lane run falsified it. Step 22's log shows the bot classifying two target-caused failures onto the *hour* schedule because pooled-resource failures carry no marker: `dynamic cost budget exceeded, executing swap` (upkeep 77, five failures, retry at +80 = 8×interval — the keeper-side schedule) and `tx references exceed MaxAppTotalTxnReferences = 8` (upkeep 121, retry at +10). Both are group-level resource failures, attributed to the outer txn, so `is_target_refusal` is False. For these two permanent cases the hour is the right answer anyway, but the generalization the docstring makes — that where the failure happened is "the one thing about a failure a target does not choose" — does not survive a target that fails by burning the pooled budget instead of asserting. That is precisely Fable's pass-1 "variable inner-txn cost" shutter, unreproduced then and still unaddressed: a state-dependent-cost target buys the full hour exactly as before this branch. Zero of 33 (now 32) live upkeeps are attackable this way today, so it changes nothing operationally — but the honest-limits list in the docstring should name it, because right now the document asserts the opposite.

**2. Request load — CLOSED; the number is real and the correctness argument holds against the contract.** I re-ran the measurement outside pytest: 5,901 requests over the 63,013-round window, exactly as claimed (754 scans, 594 executions, `{boxes: 754, box_read: 1248, execute: 1188, account: 72, status: 2639}`). The cannot-miss-due-work argument verifies: `next_execution_round` is written only at `register` (contract.py:247) and `execute` (contract.py:447), and `execute` sets it to `due + (missed+1)·interval` or `due + interval` — always strictly later, so a cached "not due until X" is a lower bound and the cache can only ever be early. The introduced lateness is bounded and documented: a top-up reviving a starved upkeep is noticed within 1,286 rounds (tested), a new registration within 128 (tested), and nothing is trusted past a day (tested). `CountingAlgod` genuinely subclasses `AlgodClient` and *asserts* `application_boxes` takes no extra kwargs — the pagination lesson is baked into the instrument. Two flatteries, neither structural: (a) executions are added by hand at 2 requests each, but the real loop spends at least 3 — `_balance(algod, keeper.address)` at keeper_bot.py:1601 is one `account_info` per attempt, plus confirmation polling in `send.execute` — so the honest day is nearer 4,000 than "about 3,000", and "counted at a client that subclasses the real one" covers only the read half; (b) the test docstring's "everything that costs a request is here" is therefore not true, in the same request category whose mis-counting this branch itself corrected in §5. The 70× reduction claim is untouched by both.

## The two smaller items

- **`reclaim.py` (48801ba) — CLOSED, verified on TestNet.** The preview splits ours/theirs before pricing and names the other creators with their holdings. My read-only scan of app 769891898 confirms the commit's claims end-to-end: 32 upkeeps, not 33 (91 is gone), and exactly twelve starved — 98–109, the N43ZVH3J set it could not cancel. Nit: the `--commit` loop still iterates `found`, not `ours` (reclaim.py:162), so it re-attempts the twelve and logs them "not ours"; refused validation costs nothing, so it's untidy, not wrong.
- **Doc updates (807de55) — CLOSED.** §3 carries the "Since fixed" paragraph with the 64-round ceiling and the metering; §5 carries "Corrected twice since" and admits the scan was 37 requests, not 36, making 211,000 a floor. Both match the code I read.

## What these fixes introduced

- The `keeper_backoff` docstring's universal claim about the marker, falsified by pooled-resource failures in this very lane run (above).
- `wait_for_work`'s model of dev-mode LocalNet is wrong, measured: `status_after_block` on an idle dev node does not "come back with the same round it went in with" — it hangs into a socket timeout, so an idle LocalNet loop lives in `scan_failed`/`error_delay` rather than the give-up branch. Dev-only, but the comment describes behavior that does not exist.
- Doc drift already in flight: keeper_bot.py:161's "thirteen of the 33 live upkeeps" is twelve of 32 as of 91's cancellation this afternoon. The test fixture is honestly dated; the prose isn't.
- Pre-existing, not this branch, noted once: the `--app-id` help text still says "else the TestNet app" while `resolve_app_id` has no default; that wording dates to #61.

Spot-checks: 578/578 unit tests, `fledge lanes run ci` green (14/14), `fledge lanes run local` green (22/22, including hostile-target and the e2e's new chain-verified stage-14 assertions). Nothing signed; TestNet touched read-only once.

On the bar: yes, this repo has produced a fresh bug in every round, but each round's yield has fallen by an order of severity — this round's are a docstring overclaim, a 25% measurement flattery, and a dev-only wart. That is what convergence looks like, not a reason to move the bar. What gets this to 95: rewrite the marker paragraph in `keeper_backoff.py` to say the split covers program-logic refusals and that budget/reference failures stay on the hour (ideally with the cost-shaped shutter added to §3's honest list), and either count the per-execution requests in `TestWhatOneDayCosts` instead of hand-adding two, or say "about 4,000 a day".

CONFIDENCE: 89 - that this work is correct, its documents are true, and it is safe to merge
BLOCKERS: NONE

To resume this session: kimi -r session_365fafa2-c4b4-4d01-8b28-55d359a53405

---

# Fourth pass: the escalation measurement

One measurement and the document built on it, `docs/design/escalation.md`.
**Confidence 70.** The author predicted this measurement had a hole he had not
found, named the one he thought most likely, and asked all three models to
assume he was right and go looking. He was, and it was not the one he named.

---
> The same cut as the third pass: the fourth pass as first committed carried
> the reviewer's transcript, including its scan scripts and their raw output,
> between here and the verdict. Cut on 2026-09-02; the verdict is unchanged.
> The independent scan it wrote is described in the verdict and is not
> otherwise preserved.

• All verification is complete. Here's the full review.

---

## How I checked

I rebuilt the measurement from scratch against the public TestNet indexer and algod (AlgoNode, read-only, no keys): every transaction touching app 769891898 to exhaustion (1,657 txns: 1,399 `execute`, 117 `register`, 85 `cancel`, 44 `top_up`, 4 `opt_in_asset`, 6 `top_up_asset`, 1 `update`, 1 create), all 32 live boxes decoded, and every `register` call's ABI arguments decoded to recover the real `fee_per_execution` and `fee_cap` for all 117 upkeeps ever registered. Then I replayed every upkeep's schedule from its registration forward — due rounds, policies, the escalation ramp, escrow balances reconstructed from group payments — and compared the contract's own formula against what each execution actually paid. The author's script was never run; the only shared inputs are the chain and the selector derivation.

## 1. The measurement

**It reproduces almost exactly, and the stated weak point does not bite — but the headline number is wrong.**

- Pinned at the document's round 66,899,702: **1,387 executions, 79 distinct upkeeps, 15 keeper addresses** — exact match. GCQL3M7A… 682, NUGVPQGZ… 631, thirteen others 1–14 each: exact match. Per-keeper splits 102/580 and 143/488: exact match. Premium 830,676 µALGO = **0.831 ALGO**: exact match.
- **The inferred-base weak point is fully recoverable and I recovered it.** `register` args decode cleanly for all 117 upkeeps (the signature takes `target_app` as `uint64`; the ARC-56 spec has it). Register-derived base equals the live box for all 32 live upkeeps, and for **all 47 cancelled upkeeps the payment floor equals the register base — zero exceptions**. Every cancelled upkeep paid its true base at least once, so the floor method's inferred bases are all correct, and nothing is hiding in the 321/330 executions against cancelled upkeeps. The author flagged the right weak point; it happens not to bite.
- **The hole the author didn't find: "238" is wrong.** Counting `payment > base` — by the floor method or with recovered true bases, identically — gives **258 escalated executions (18.6%), not 238 (17%)**. The document's own table sums to 258 (102+143+6+2+2+2+1) and its own premium total is the 258-figure's premium; only the headline count and percentage are stale, almost certainly carried over from the earlier scan version the commit message admits dropping cancelled upkeeps (escalations on cancelled upkeeps: 19). Seven for seven on this repository's central statistics needing a correction, though this one is a transcription error, not a methodology error: the classification underneath is right.
- **"All seven upkeeps with escalation enabled have fired it, and six of the seven have reached their ceiling"** is true only under an unstated live-upkeep scope. All-time, **16** upkeeps registered with `fee_cap > base`; 12 were ever executed, **all 12 fired escalation, 11 hit their ceiling**. The seven live ones did all fire and six did hit ceiling, so the sentence is defensible if you read "upkeeps" as "live upkeeps" — but the surrounding paragraphs are all-time statistics, and a reader will misread it. It also undersells the mechanism's activity.
- **What the `times_executed` cross-check does not cover:** I re-ran it (sum over live boxes 1,069 at my round; scan count 1,069; per-upkeep zero mismatches). It validates *counts* for the 32 live upkeeps only. It says nothing about the 330 executions on cancelled upkeeps, nothing about keeper attribution, and nothing about the escalated/base split — which is the part that depends on base correctness. It also cannot detect the indexer silently losing transactions for cancelled upkeeps; the only thing closing that gap is lifecycle arithmetic, which does close: 117 registered = 85 cancelled + 32 live, no executions before registration or after cancellation.
- **What neither the author nor the cross-check thought to do, and what settles the split:** replay the ramp. All **258** above-base payments equal the ramp formula `base + (cap−base)·excess//interval` *exactly* — no payment above base came from anything but escalation, no payment ever fell below base, and the escrow clamp (`balance < fee → pay base`) has **never fired** on this registry. So "paid more than base" ⇔ "escalation fired" is exact, not a proxy.

## 2. The conclusion

"No keeper only ever collected escalated fees" survives — verified under both base methods — but you're right that it's a weak formulation, and better queries are runnable. I ran them:

- **First-ever execution per keeper, escalated?** For 14 of 15 addresses: no, base fee. The one exception is CEPY52VZ…, whose first-ever execution was upkeep 82 *at the 20,000 µALGO ceiling* — and CEPY52VZ is documented in the repo's own README as the operator's agent. The only address that ever showed up for a premium is the operator testing his own mechanism.
- **Did escalation ever move supply?** Escalated executions are taken by whoever was already servicing that upkeep — they bounce between GCQL and NUGV (the same operator's cron and laptop), plus one CEPY cluster at rounds ~66,830,658–674 across six upkeeps that looks like a single test session. No escalated execution was ever taken by an address that wasn't already an incumbent.
- **Did the rising price accelerate service?** Median delay past due: 4–8 rounds for base-fee executions, **122–616 rounds for escalated ones**, on every escalating upkeep. Escalations are exactly the keeper-outage windows; service resumed when the incumbent came back, at whatever price the ramp had reached by then. The price rose while nobody was listening.
- **The expected-fee channel:** total keeper revenue all time is 10.53 ALGO; the escalated component is 0.83 ALGO (8%). A keeper attracted by expected fees would have been attracted by the base stream; the premium is economically noise.

So the better question isn't "did anyone collect only escalated fees" but "did any keeper arrive at all" — and the answer is no: every executing address is attributed to the operator. On that evidence the document's conclusion ("escalation has never bought a keeper here; the chain cannot distinguish that from 'never had the opportunity'") is correct and, if anything, understated: the mechanism has never been observed to change any keeper's behavior, including the incumbents'.

## 3. The framing

Refusing to recommend between keep / `raise_fee` / delete is **the right call, not abdication** — with one caveat. The decision genuinely turns on an event this chain cannot produce (an upkeep unserved for want of a bidder, then rescued), the document says precisely what would change the answer and when to re-ask it, and my better queries above confirm the evidentiary situation rather than break it. A recommendation among the three would be manufactured confidence.

The caveat: the document *does* effectively recommend on sequencing — "freezing it in is the expensive choice" — and should just say so. The evidence does support one firm recommendation: **the two questions must be re-asked before `freeze`, and if both are still no, escalation should be removed or replaced via `update` before that door closes**, because it is the only feature carrying an unmitigated audit finding, its clamp path is as untested as its market path (zero firings), and `fee_cap` deleted post-freeze would scar the struct permanently. That's a recommendation to schedule the decision, not to make it — and it's the honest version of what the document already almost says.

## 4. The stale numbers

- **682 of 1,387, NUGVPQGZ 631, fifteen addresses** — verified exactly at round 66,899,702. ✓
- **The old numbers were right when written**: at round 66,860,306 I reproduce 1,011 total, GCQL 592, NUGV 345, and the old window (32,000 rounds) yields exactly 215/36/10 from three addresses. ✓
- **746 executions across 33 live boxes as of round 66,860,306** — reproduced exactly (33 upkeeps live at that round, 746 executions on them). ✓
- **But the correction introduced a new inconsistency**: the README passage still says "Read from the chain on 2026-08-31, at round 66,860,306" and describes "the preceding 30,857 rounds" — and *in that window* the counts are still **215/36/10 from three addresses**, not 232/57 from two. The new figures verify only in the 30,857-round window ending at round 66,899,702 — the escalation scan's round, a day later. The commit moved the numbers without moving the dateline, so README and START-HERE now mix two reading dates inside one dated claim (the 746/33 beside them belongs to 8-31; the 232/57 belongs to 9-01). Same disease the commit was fixing, in the commit that was fixing it.

**Summary of what's wrong in the document**: (a) the headline "238 / about 17%" should be **258 / about 19%** — its own table and premium already say so; (b) "all seven upkeeps with escalation enabled" needs its live-only scope stated, or the all-time figures (16 enabled, 12 fired, 11 at ceiling); (c) README/START-HERE need the window's anchor round corrected to 66,899,702 or the numbers recomputed at 66,860,306. Everything load-bearing — the split, the premium, the per-keeper numbers, the inferred-base weakness, the "no escalated-only keeper" conclusion, the freeze-sequencing argument — checks out against the chain.

CONFIDENCE: 70
BLOCKERS: NONE

To resume this session: kimi -r session_26d09b87-f32a-4232-93da-30035b0b7f81
