# Keeper audit — verification of the 2026-09-01 pass

> A follow-up to [`2026-09-01-opus-5-keeper-audit.md`](2026-09-01-opus-5-keeper-audit.md),
> not an independent review. Scope is that audit's own claims: re-establish
> each one or say it could not be, then look hardest at what it did not test.

| | |
|---|---|
| **Reviewing** | the keeper audit of 2026-09-01, commit `5d7f6a8` |
| **Date** | 2026-09-01 |
| **Method** | Re-ran everything the audit ran; rebuilt F1's table from scratch on a fresh unfunded keeper; added five hostile-target behaviours to `resource_probe` and drove them on LocalNet; read the live registry |
| **Not done** | Nothing signed on TestNet. No MainNet parameters checked. Whether each live escalating upkeep's target is actually blockable was not established per target |

**Verdict.** F1, F2 and F3 hold, F1 to the microalgo. Two sentences in the
audit are wrong and are corrected below. One thing it did not look for is
worth more than anything it found: **an upkeep's lateness can be bought.**
For one application call — 1,000 µALGO — a third party can shut a conditional
target, block every honest keeper, and be paid the escalated fee for the
lateness they created. Measured at 21,600 µALGO of profit on a 1,000 µALGO
outlay. Nothing about it is a contract bug, and it does not change the
MainNet answer, but it does change what a creator should be told.

---

## 1. What reproduced

Everything, without qualification.

| Claim | Result |
|---|---|
| the audit's 508 unit tests pass | 508 at the time; 559 now, as this branch added tests |
| `scripts/attacks.py` 3/3 refused | 3/3, each for the right reason |
| `scripts/spike_reentrancy.py` 3/3 refused | 3/3, `attempt to re-enter <app>` |
| F1's whole table on a fresh unfunded keeper | every number identical, including `balance 37900 below min 100000` |
| F2: a bracketed target sees `group_size` 1 | reproduced, now asserted in a spike |
| F2: the target learns only the app account | reproduced |
| Failing targets cost the keeper 0 µALGO | reproduced for both budget exhaustion and refusal |

F1 was rebuilt from a separate probe and a separate script rather than
re-running the audit's, and landed on the same six numbers. It is real.

**Two things the audit did not test, which hold.** `register` charges and
`cancel` refunds the *ledger's* box minimum balance, not an approximation of
it: for a one-argument upkeep the account's `min-balance` rose by exactly the
62,100 charged and `cancel` returned exactly it, and for a three-argument
upkeep exactly the 81,300 charged. The audit asserted this from the formula
and a mock test; it is now measured on chain at two different argument counts.

## 2. Two sentences that are wrong

**"No third-party loss" (Informational I1) is the wrong way round.** The
fee-fallback bypass moves money from the upkeep's creator to the keeper, and
a creator is a third party to the keeper. What the audit meant — that it is
bounded by the cap the creator chose — is true and is not the same statement.
Measured: base 4,000, cap 40,000, escrow 24,400. Left alone the fallback pays
4,000 and leaves 20,400 in escrow. With a 15,600 top-up in front of it in the
same group, the same execution pays 40,000, empties the escrow, and clears
the keeper 19,400 after its own fees. The fallback protects the *upkeep*, by
keeping it executable; it does not protect the creator's wallet, and no
keeper has to accept it. That now has a test:
`tests/test_keeper.py::test_the_fallback_to_base_is_the_keepers_to_decline`.

**"Two keepers racing produce one payment" (sound item 2) is too broad.** It
holds for an upkeep inside its own interval. It fails for one behind: under
CATCH_UP each execution advances the schedule by a single interval, so a
backlog can be drained several deep. Measured: three `execute` calls, one
sender, **one atomic group**, three executions and 12,000 µALGO. Each replay
paid base, which is the design and is already unit-tested; the audit's
sentence simply claimed more than the code does. `scripts/keeper_race.py`
does not catch this because it races an upkeep that is on schedule.

## 3. What the audit missed: lateness is purchasable

Fee escalation pays a keeper more when an upkeep is late. The premise is that
lateness means nobody would take the job at the base price — that the market
did not clear. That premise fails whenever the *target* can be made to
refuse.

Any target whose call is conditional on state a third party can move is
exposed: an oracle that rejects a stale update, a rebalancer that runs once an
epoch, a claim that pays once a period. Blocking is cheap because a rejected
execution is not in the ledger, so honest keepers can be made to fail for
free, all day, without noticing anything except a target that looks broken.

**Measured** (`scripts/spike_hostile_target.py`, LocalNet). Upkeep with base
4,000, cap 40,000, interval 10 rounds, against a target that refuses two
calls inside 6 rounds — short enough that an honest keeper on cadence is
never blocked.

**Every µALGO figure in this section is one run's.** LocalNet advances a round
per transaction, so which round an execution lands on moves between runs and
the fee moves with it: `excess` is counted in rounds. A second run of the
identical spike printed 32,800 and +28,800 where the table below prints 25,600
and +21,600, and both are correct. Two of the three reviewers reran it and saw
different numbers, which is why this paragraph exists. **The properties are
what reproduce**, and they are what the spike asserts: the honest keeper is
blocked at zero cost, the attacker collects more than base, the escrow reaches
zero, the fee never returns to base. Read the numbers as a sample of a shape,
not as constants.

| | |
|---|---|
| honest keeper, one round late (LocalNet makes every run one round late; base is 4,000) | creator paid 7,600 |
| attacker calls the target directly, once | costs the attacker 1,000 |
| honest keeper, at the due round | **blocked**, and it cost them 0 |
| attacker executes when the window reopens | creator paid **25,600** |
| attacker's net over the episode | **+21,600 µALGO** |

One cycle, one blocking call: the creator paid 21,600 more than base and the
attacker kept it, **21.6x** the 1,000 it cost to arrange. That figure is one
upkeep with nobody competing, and it is the smallest of the numbers in this
section rather than the headline: the variants below reach 45,600 on a single
target, and Grok measured 65,800 against two upkeeps sharing one.

A six-cycle run of the same setup, attacking only the second, has the creator
paying 49,200 against a nominal 24,000. Attribute that honestly: 21,600 of
the 25,200 excess is the attack, and the remaining 3,600 is the first cycle
running a single round late on a ten-round interval, which is LocalNet's
round granularity rather than anybody's doing. It is worth noticing anyway,
because it shows how steep the ramp is: on a ten-round interval with a ten-fold
cap, **one round late costs 90% of the base fee again.** Real cadences are
thousands of rounds and do not have this problem; short ones do.

**Three worse forms, all measured after the first draft of this section
claimed limits that do not hold.** Grok 4.6, Kimi 3 and Fable 5.1 each found
at least one of these independently; the numbers below are this machine's.

*It does not self-heal.* The first draft said CATCH_UP re-phases the upkeep
and honest keepers are back on base within two cycles. That is true only of
the configuration the spike happened to use, a cooldown of 6 rounds against
an interval of 10. Point the same upkeep at a target whose cooldown is
*longer* than the interval and every successful execution re-arms the guard
past the next due round, so the upkeep is permanently late by its own
schedule and the fee never comes back down. Measured under SKIP_AHEAD with a
15-round cooldown and **no attacker at any point**: a first fee near base, then
every later cycle escalated and none returning — 7,600 then 22,000, 22,000,
22,000 on the run this was written from. Under CATCH_UP the same setup pays base after two
hits and the backlog grows without bound instead. Nobody has to buy anything;
the upkeep blocks itself and whichever keeper is waiting collects. This is
the likelier shape in the wild, not the exotic one, because "an oracle that
rejects a stale update, a rebalancer that runs once an epoch" *is* a
cooldown, and a creator who picks a cadence shorter than that epoch has built
it by accident.

*Blocking is cheaper than an application call, and Arcron pays for it.*
Register a second upkeep against the same target at `fee_cap = 0`. Its
execution trips the guard on a schedule, the attacker executes it themselves
so the base fee comes straight back, and no blocking transaction is ever sent
by hand. Measured over four cycles: honest keepers shut out of **every cycle**, the
creator spending several times what four base fees would have cost, and the
attacker finishing well ahead after its own escrow float and every fee. On the
run this was written from, 4 of 4, 55,600 against 16,000, and +45,600. This
also walks through the defence `docs/integrating.md` recommends: a target
told to `assert Txn.sender == keeper_app.address` refuses a raw call and
accepts this one, because the inner sender is the keeper app either way, and
a permissionless registry cannot stop anyone registering a second upkeep.

*The bound is the escrow, not the cap.* Compose this with the fallback
decline in section 2. On an upkeep whose escrow has fallen below the
escalated fee, the fallback is what an ordinary keeper collects, and it may
not cover the block. Top the escrow up to the cap in the same group instead
and the cap is what you collect, of which only the shortfall was yours.
Measured: an escrow below the escalated fee, where the fallback would have
paid base, goes to **zero** in one execution and the attacker clears roughly
what the box was holding. On the run this was written from, 32,400 under a cap
of 40,000, a 7,600 top-up, and +27,400. Kimi 3 found this composition during the branch
review. The per-episode ceiling is the whole remaining escrow, not
`cap − base`.

**Limits that do hold.**

- It needs `fee_cap > fee_per_execution`. On the live registry today that is
  **7 of 33 upkeeps**, each with a gap of 8,000–10,000 µALGO per manufactured
  cycle. The rest have escalation off and are immune. Read from TestNet with
  `scripts/keeper_bot.scan_upkeeps`; none of the three reviewers could
  confirm it, because none was allowed a TestNet read.
- It needs a blockable target. A heartbeat that always succeeds — `pulse`,
  the dogfood — is immune, and so is any target with no third-party-movable
  precondition. Which of the seven are genuinely exposed was not established
  per target, and should be.
- Every µALGO stays inside the `fee_cap` the creator stored, `cancel` still
  works, and no other upkeep's escrow is reachable. This is an incentive bug
  in a feature, not a theft path in the program.

**And one assumption that was wrong in the other direction.** An earlier
draft of this section reasoned that an attacker still has to win a race with
polling keepers when the window reopens. Against the keeper this repository
ships, there is no race to win. `scripts/keeper_backoff.py` reads an inner
transaction failure as a broken target rather than a lost race
(`INNER_FAILURE_MARKER = "inner tx"`), and `select_due` then omits that
upkeep for `1 x interval` rounds, capped at `MAX_BACKOFF_ROUNDS = 1_286`,
about an hour. One blocked attempt sends our own bot away. Every number above
is therefore a lower bound against the reference client, not an overestimate.

That has a second consequence worth more than the fee arithmetic: the same
single failure silences the bot on an upkeep **whose escalation is off**. For
a liquidation, an oracle or a keep-alive, the missed window can be worth far
more than the fee, and nothing in the contract or the health report meters it.

**Since fixed, with one hole left open on purpose.** The schedule now branches
on where the failure happened: a refusal by the target's own program logic is
conditional by construction, because `execute` checked the schedule and the
escrow before it called anything, so those back off 1, 2, 4 … rounds to a
64-round ceiling instead of an hour. Any execution clears the streak, and the
loop reports each due-but-held upkeep with the fee going unclaimed, so a
blackout is now visible rather than inferred.

What the split does not cover is a failure that happens **before the inner
program starts**, which carries no `inner tx N failed` for algod to attribute:
`dynamic cost budget exceeded` against the keeper's own program, and
`tx references exceed MaxAppTotalTxnReferences = 8`. A target whose cost or
resource appetite is state-dependent can put itself on that side of the line
and still buy the full hour. It is a cost-shaped shutter rather than a
logic-shaped one, both reviewers found it, and it stays on the long schedule
because those two usually mean the target needs more than any keeper can
bring, which retrying in a round does not fix.

So the honest price of an arranged blackout is not "twenty refusals an hour".
Against a logic-shaped refusal it is a **reopening every 64 rounds at the top
of the ramp**, which the attacker must win; against a cost-shaped one it is
still the hour. The figures above stay lower bounds for the registry as it was
when they were measured.

**What to do.**

1. **Advice, immediately, and it is half-written already.** An upkeep whose
   target can be blocked by anyone should set `fee_cap = 0`. The console
   already defaults the field to 0 (`web/src/app/components/register-form.ts`)
   and `docs/integrating.md` already tells creators to leave it there, for a
   different reason — that a lone keeper will not bid. Neither says that a
   blockable target turns the ceiling into a prize somebody can come and take.
   That sentence is the change, and it now exists in `docs/integrating.md`.

   **With a caveat the first draft missed: `fee_cap` is write-once.** It is
   set in `register` and read in `execute`; there is no method that changes
   it. The seven live escalating upkeeps cannot take this advice at all
   without cancelling and re-registering, which returns their escrow and box
   MBR and gives them a new id.

2. **Not the ramp, on reflection.** The first draft proposed spreading the
   ramp over several intervals so an attacker must block repeatedly while an
   honest keeper needs one success. Two of the three reviewers rejected it and
   they are right. It does nothing against a cooldown longer than the
   interval, where nobody is blocking at all; it does nothing against a
   sibling upkeep whose block is already scheduled; and against the reference
   bot there is no "honest keeper gets through once", because one failure
   backs it off for up to an hour. `(cap - base) * excess` also has to stay
   inside uint64, which bounds the spread to roughly 18 intervals.

   The remedy that removes the incentive is to stop paying automatically for
   lateness the keeper set can be made to create: **a creator-signed
   `raise_fee(upkeep_id, fee)` bounded by `fee_cap`**, so the bid comes from
   the party who pays it and who can see whether their target is genuinely
   unserviceable. Fable 5.1 proposed it; it adds a method and touches no box
   field, so it is reachable by `update`.

3. **Which makes the freeze ordering the real decision.** Escalation shipped
   with #14. Everything above is reachable by `update` and closed forever by
   `freeze`. Nothing here argues for delaying MainNet; it argues that the
   freeze should not happen while the fee mechanism still pays for lateness
   that a third party can manufacture.

## 4. F1 is not new, and that is the point

`docs/reviews/2026-08-25-kimi-3.md` H1 is the same bug in DeadMan, found a
week earlier: an app deployed without its base minimum balance, an inner
payment that fails every time, escrow stranded. It was fixed there. The
keeper's `deploy_config` funds the account; the keeper's *governance* create
path could not, and got a log line instead.

That is exactly the pattern the 2026-08-26 re-score named — a fix that is
correct where the bug was found and absent at the sibling with the identical
shape — and it is issue #105's open instance, one contract further along.
The audit found F1 without noticing it had been found before. Worth saying
plainly: this repository's most productive review technique is to take last
month's finding and go looking for its siblings.

Two documents already told an operator to fund the account
(`docs/deploying.md`, `docs/releases.md`), so the audit's "relies on a log
line" understates them. What did not exist was any way to *confirm* it. Now
`govern status` prints the same line `health` does, and the release checklist
asks for the output rather than the memory:

```
  escrow    54.201 ALGO owed, 54.201 ALGO spendable
```

## 5. What could not be established

- Whether each of the seven live escalating upkeeps has a blockable target.
  That needs a per-target read and is the obvious next task.
- Whether the fee-fallback bypass has ever been used on TestNet. Detecting it
  means correlating a `top_up` and an `execute` in one group per upkeep, and
  the indexer query for that was not written.
- Anything about MainNet consensus parameters that differs from LocalNet.
- The audit's I4 (`health` returning HTTP 403 from the public node) is
  settled, and **both earlier answers were wrong, this document's included.**
  The audit guessed our request rate. The first draft of this section then
  asserted the opposite — that the quota is "shared across everyone using that
  endpoint" and "nothing here is fixable by sending less" — on the strength of
  a counter reading `x-and-quota: block=1;reqs=230824` against a cited
  200,000-a-day free tier. Fable 5.1 refused to take that as settled and was
  right to.

  Measured since. `launchctl list` shows `xyz.corvidlabs.arcron.keeper.testnet`
  running on this machine, pointed at the same host as the refused `health`
  runs. Counting `{"event": "scan"}` lines in its own log: 11,543 scans across
  63,013 rounds, one every 5.46 rounds, at 37 requests a scan (a status, the
  box listing, one read for each of 33 boxes, and the block wait; the account
  read is on the heartbeat, one scan in twenty). That is **416,125 requests
  over 1.97 days, about 211,000 a day, from this one bot**, against a counter
  that stood at 230,824 — roughly 92% of it,
  from one process on one laptop. Three people counted this separately and
  landed within 1% of each other, which is the agreement that matters rather
  than the last digit; the log grows while you read it. Two read-only
  `GET /v2/status` calls 45 seconds apart moved `reqs` by 50, about one a
  second, which is not how a bucket shared by every user of a public Algorand
  endpoint would climb.

  **Corrected twice since, both against this paragraph.** The scan is 37
  requests, not 36: the account read is made every scan, not on the heartbeat
  as written above, so 211,000 a day was a floor. And the fix has landed —
  `keeper_bot.py` now re-reads a box only on the round its cached copy could
  change a decision and sleeps to the soonest of those rounds, measured at
  **about 9,500 requests over the same window, some 4,800 a day: a
  fortieth.** Counted at a client that subclasses the real one
  (`tests/test_keeper_bot.py::TestWhatOneDayCosts`), but only half of it is a
  client count. The 2,400-a-day reading half is genuinely counted; the
  execution half is 594 executions at a measured eight requests each, taken
  from a real `--once` on LocalNet. That test claimed "about 3,000 a day, a
  seventieth" until 2026-09-01, when Fable 5.1 pointed out it was hand-adding
  two per execution: a constant is not a measurement, and the half it stood in
  for was a fifth of the truth.

  So it was our request rate, it was one bot, and sending less was exactly the
  fix — which belonged in `keeper_bot.py`, not in a retry helper. What stays
  open is what the bucket is keyed to, since nobody has read Nodely's side,
  and the 200,000 figure is the one number here that is cited rather than
  measured.

  The retry stands as the right thing for the reports regardless: the edge
  sheds about 9% of requests once blocked, a `health` run makes about 40, and
  `1 - 0.91^40` is 98%, which is why two runs in three died while a single
  `curl` never did. `net.connect` installs four retries after the first
  attempt, five tries in all (`scripts/node_retry.py`), and every script gets
  it by connecting; three live `health` runs afterwards succeeded, recovering
  from 1, 6 and 2 refusals. It is a band-aid over a bot that reads all 33
  boxes every five rounds, and it should not be mistaken for the repair.

## 6. Three independent passes over this branch

The two documents above were written by the same model family in two sessions,
which is a weakness no amount of care inside them fixes. So the branch was put
to Grok 4.6, Kimi 3 and Fable 5.1 headless, each with the same prompt, none
shown the others' answers, each asked to refute the new finding and to end
with a confidence number it was told to keep honest rather than agreeable.
The three reviews are checked in beside this one, unedited.

| | Confidence | What it found that the others did not |
|---|---|---|
| [Grok 4.6](2026-09-01-grok-4.6-branch-review.md) | **52** | The reference bot backs off on a target refusal, so there is no race to win; the spike passed when the attack did not happen; `fee_cap` is write-once |
| [Fable 5.1](2026-09-01-fable-5.1-branch-review.md) | **62** | The node quota is probably ours, not shared; the solvency fallback guessed in the unsafe direction; a better remedy than the ramp |
| [Kimi 3](2026-09-01-kimi-3-branch-review.md) | **78** | The branch shipped contradictory premises about outside users; the per-episode bound is the escrow, not the cap |

None could refute the finding. All three reproduced it, and each made it
worse. What they changed:

- **Section 3 was rewritten.** Its "self-heals within two cycles", "extraction
  not capture" and "the attacker must win a race" were all false, and the
  cooldown-longer-than-interval and sibling-upkeep variants are now measured
  and asserted rather than absent.
- **The ramp remedy was withdrawn** in favour of a creator-signed raise.
- **`scripts/spike_hostile_target.py` exited 0 when the attack did not
  happen.** Every path that measures nothing now fails, and `_send` no longer
  reads a dead node as a refusal from the chain. A lane step that passes
  without measuring is worse than no lane step, and this is the second time
  this repository has caught that shape in its own spikes.
- **`test_the_fallback_to_base_is_the_keepers_to_decline` ended in a
  tautology**, `cap - shortfall == held` where `shortfall` was defined as
  `cap - held`. It now compares the two paths against each other.
- **`read_solvency` guessed the ledger's floor low when a node omitted it**,
  which inflates spendable and hides the shortfall the check exists to find.
  It refuses now. The live registry runs at exactly zero margin — 54.126 ALGO
  spendable against 54.126 owed — so an inflated figure would have hidden a
  real hole one for one.
- **`read_upkeeps` did not paginate** while `keeper_bot.scan_upkeeps` did, so
  the new solvency sum would have under-reported liabilities past 1,000 boxes
  and called an insolvent registry solvent. It reuses the paginated reader now.
- **Three documents carried premises the attribution fix had already
  retired.** `AGENTS.md`, `docs/design/split.md` and `docs/status.md` still
  grounded the dogfood and MainNet-gate arguments on outside adoption that
  [#236](https://github.com/CorvidLabs/arcron/pull/236) had just shown does not exist. Kimi found two; the third was found
  looking for its siblings. This is #105 again, and finding it by looking for
  siblings is the technique section 4 recommends.
- **The advice landed.** `docs/integrating.md` now says what a blockable
  target costs, with the measured numbers, and says that `fee_cap` cannot be
  changed afterwards.
- **Section 5's account of the node refusals was wrong, and it was this
  document's own confident sentence rather than the audit's.** Fable would not
  take "shared across everyone" as established. Measuring it showed the
  opposite: our own keeper daemon accounts for essentially the whole counter.
  The retry is still right for the reports; it is a band-aid, and the fix
  belongs in the bot.
- **`node_retry` claimed a POST replay was safe because the CDN never
  forwarded the request.** That was never checkable. The true argument is that
  a replay re-sends the same signed blob, and an Algorand transaction id is a
  hash of it, so the chain answers "already in ledger" rather than paying
  twice. The duplicate answer is now recognised and returned rather than
  raised, because `keeper_bot` would otherwise report it as losing a race to
  itself. 5xx is now retried for reads and never for a submission.

Four small errors they caught are corrected in place above: the table row that
labelled a one-round-late execution "on schedule", "22x" for 21.6x, F1's
"first creator" (the shortfall lands on whoever's `cancel` runs last, not on
whoever registered first), and the retry count.

### The second pass

The same three read the branch again with their own reports in front of them
and were asked to mark each of their blockers closed, partly closed or open.
Grok moved 52 to 76, Kimi 78 to 88, Fable 62 to 68; none held a blocker from
the first pass open. What they found the second time was mostly created by the
first round of fixes, which is the useful direction:

- **The pagination fix was broken, and so was what it reused.** Grok checked
  the new test's fake against the client the production path uses and found
  `scan_upkeeps(algod, app_id)` continuing pages with
  `application_boxes(app_id, next=token)`, which algosdk cannot accept: it
  builds that call's query string from `limit` alone and forwards the rest to
  `algod_request`, which has no `next`. A second page raised `TypeError`. This
  was a **pre-existing bug in the keeper bot**, not in the branch — it has
  never fired because the registry is 33 boxes — and the branch had just built
  a solvency check on top of it. Fable found it independently. The first page
  now goes through the typed method and only the continuation drops to the raw
  request, and the fake subclasses the real client so a keyword it would
  reject cannot pass here again.
- **Two spike assertions did not match the sentences they were evidence for.**
  `measure_no_self_heal` escaped only if *every* later cycle recovered, so a
  single escalated cycle followed by three at base would have been reported as
  confirming the opposite. `measure_sibling_blocker` checked only that the
  attacker finished ahead, which its own blocking upkeep's base fees satisfy
  even if the victim was never taken. Both now assert the claim.
- **The tautology survived its own fix.** `cap - shortfall == held` became
  `took_under_the_bypass == held` with the same arithmetic underneath, and the
  comment claiming otherwise made it worse. Both sides are now read back from
  the contract, and mutating the fee to base fails the test.
- **The numbers are one run's, and three reviewers reran the spike and got
  different ones.** Section 3 now says so and quotes the properties.
- **The sibling sweep found three sites and there were six.** `CLAUDE.md` —
  the file that tells agents to keep the three top-level documents in step —
  `docs/testnet.md`, and `docs/design/1.0.md`, which still gated MainNet on
  "its outside creators", all carried the retired premise. Missing them in the
  commit that named #105 as the recurring failure is the most instructive
  thing on this page.

## 7. Would this change the MainNet answer?

No, and all three reviewers agreed on that separately: nothing here is a theft
path in the program. The audit said yes-with-two-conditions and both survive.
Fund the base minimum balance and prove it. Keep the creator a multisig, with
a freeze decision made on a date.

What changed is the third condition, which is no longer just advice. Section 3
is an incentive bug in a feature that shipped with #14, it is reachable by
`update`, and `freeze` closes it forever. So the ordering matters: **do not
freeze while the fee mechanism still pays automatically for lateness a third
party can manufacture.** Either decide that escalation stays off by default
and say so where creators read it, which is now done, or replace it with a
creator-signed raise before the programs stop being replaceable.

There was also a liveness question this branch opened and did not answer: one
inner-transaction failure sent the reference keeper away from an upkeep for up
to an hour, escalation or not, worth more than every fee in this document on a
liquidation or an oracle, and metered by nothing.

**It is answered now**, in section 3 and in `scripts/keeper_backoff.py`: a
logic-shaped refusal costs 64 rounds rather than an hour, an execution clears
the streak, and each due-but-held upkeep is reported with the fee going
unclaimed. A cost-shaped one still costs the hour and is written down as such.

That this paragraph said otherwise for several hours after section 3 was
updated is the fourth instance in one day of the failure this document keeps
naming: a fix applied where the finding was raised and absent at the sibling
saying the same thing elsewhere. Grok 4.6 caught it. #105 is not a bug to be
closed; on this evidence it is a property of how this repository is edited,
and the only technique that has ever worked against it is grepping for the
claim rather than the code.
