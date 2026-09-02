# Fee escalation: what it has actually done

**Status:** open, with one thing decided. The measurement below is strong and
the inference from it is not, so this document does not choose between keeping,
replacing and deleting escalation. It does make the one recommendation the
evidence supports, which is about *when* that choice has to be made.

Escalation is the rule in `execute` that pays a keeper more when an upkeep is
late. It exists to clear a market: if nobody will take a job at the base fee,
raise the price until somebody does. It shipped with #14 and has never been
reviewed against what it does in practice.

Two things happened on 2026-09-01 that make that worth doing. The keeper audit
found that the lateness escalation pays for can be manufactured by a third
party, and a scan of the live registry found that escalation has never once
attracted a keeper who was not already there.

## The measurement

Every `execute` ever sent to app `769891898`, read from the indexer on
2026-09-01 at round 66,900,477. **1,399 executions and 117 registrations**,
pagination run to exhaustion. Thirty-two upkeeps are live, of which thirty-one
have ever run; 1,069 executions were against those and 330 against the
eighty-five since cancelled.

An execution counts as escalated when its inner payment exceeded the upkeep's
base fee, and **base and ceiling come from each upkeep's own `register` call**,
recovered from the transaction's arguments, so a cancelled upkeep is measured
exactly like a live one. Zero of the 1,399 executions belong to an upkeep whose
registration could not be recovered.

That matters because the first version of this measurement inferred base from
the lowest fee ever paid on an upkeep, which is wrong for any upkeep that was
never once executed on time: its floor would itself be an escalated fee. Three
reviewers were asked to attack that specific weakness. Reading the real
arguments removes it rather than bounding it.

| | total executions |
|---|---|
| `GCQL3M7A…` (GitHub Actions cron) | 689 |
| `NUGVPQGZ…` (the laptop) | 636 |
| thirteen others | 14 or fewer each |

**258 of 1,399 executions, 18.4%, paid more than base.** Sixteen upkeeps have
ever been registered with escalation enabled, twelve of them have fired it, and
**eleven have reached their ceiling at least once**. The total paid above base,
all time, is **0.831 ALGO**.

Read the fifteen addresses carefully: eleven of them are the end-to-end suite,
which registers an upkeep, executes it about ten times, and cancels inside a
few hundred rounds. Some of its escalated executions are the suite deliberately
asserting that escalation works. Two addresses are the real keepers and they
account for 1,325 of the 1,399.

And the number the decision turns on:

> **Keepers that only ever collected an escalated fee: none.**

Every address that has ever taken a premium also takes base fees routinely. The
premium went to the two keepers that were already servicing the registry.

## What that does and does not establish

It establishes that escalation, on this registry, has never bought a keeper.
0.831 ALGO moved from creators to incumbents for work those incumbents were
doing anyway. On today's registry both sides of that transfer are ours, so it
is an internal accounting entry rather than a cost, but it would not be with
outside creators.

**It does not establish that escalation does not work, but it is closer to
that than the first draft of this document admitted.** That draft said the
market had never had the opportunity to clear, because two keepers cover
everything. That is false, and the numbers above say so: **eleven upkeeps have
reached their ceiling**, and reaching a ceiling means the price sat at its
maximum for at least a full interval with nobody taking it. The opportunity
arose repeatedly. Nobody new came.

What that still does not settle is why. No keeper outside this project has ever
watched this registry at any price, so the offer was never made to an audience
that could accept it. That is a fact about the audience rather than about the
mechanism, and it is the honest limit of what a TestNet registry with no
outside participants can show.

The 17% is genuine lateness rather than manufactured lateness: our keepers are
late because GitHub Actions delivers a fraction of the runs it is asked for and
the other keeper is a laptop. Escalation is correctly detecting that our own
infrastructure is unreliable and charging our own creators for it.

## The three options

### 1. Keep it

No change, no new surface. The cost is that it pays a premium for something it
has not been shown to deliver, and it carries the audit's finding: a third
party who can make a target refuse collects the escalated fee, and a target
whose cooldown outlasts its upkeep's interval pays the ceiling forever with
nobody attacking it. Both are documented in
[`../security.md`](../security.md) and neither is closed.

### 2. Replace it with a creator-signed raise

A `raise_fee(upkeep_id, fee)` bounded by the stored `fee_cap`, creator only.
The bid then comes from the party who pays it and who can see whether their
target is genuinely unserviceable, so there is nothing for a third party to
manufacture and no windfall to an incumbent.

Adds a ninth method to a surface [`1.0.md`](1.0.md) says freezes after the
current batch. Touches no box field, so it is reachable by `update` and does
not restart the struct clock. The real cost is that it makes an upkeep need
tending: a creator who is not watching when the keeper set thins out simply
does not get serviced, which is the opposite of what a scheduler is for.

### 3. Delete escalation

The smallest surface of the three and the only one that removes the finding
rather than routing around it. `fee_cap` becomes a dead field in a struct that
cannot be reshaped without a new app id, which is a permanent scar for a
temporary problem. And it forecloses the mechanism before it has been tested.

## What decides it, and when

Two questions. Before `freeze`, ask both:

1. Has any keeper appeared that only shows up for escalated work?
2. Has any upkeep gone unserved for want of a bidder and then been rescued by
   the price rising?

Today: no, and no. Note what the second one now means, given that eleven
upkeeps have reached their ceiling with nobody taking the offer. The price has
gone to its maximum and stayed there repeatedly. What has never happened is
somebody arriving because of it.

**The recommendation this document does make is about sequencing.** Escalation
is the only feature in the contract carrying an unmitigated audit finding, and
its ramp lives in the program rather than in the box, so `update` can change it
and `freeze` closes that door permanently. `fee_cap` deleted after a freeze
would be a dead field in a struct that cannot be reshaped without a new app id
and every creator re-registering by hand. So: **re-ask both questions
immediately before `freeze`, and treat a second "no" as requiring a decision
then, not a deferral past it.** Whichever way it goes, it has to go before the
door shuts.

An earlier draft stopped at "ask again later" and called that refusing to
recommend. Three reviewers pointed out that on a registry with no outside
participants the trigger cannot fire, so deferring indefinitely is choosing to
keep escalation while declining to say so. They were right. Scheduling the
decision is the honest version, and it is what this document recommends.

## What three reviewers did to the first version of this

Grok 4.6, Kimi 3 and Fable 5.1 read it the day it was written, each told what
the author believed its weakest point was and asked to assume there was a hole
he had not found. **35, 47 and 70 out of 100.**

The weak point he named, inferring base from the lowest fee ever paid on a
cancelled upkeep, was checked and closed: the real figures are recoverable from
each upkeep's `register` arguments, which is what the measurement above now
does. The holes were elsewhere.

- **The headline contradicted its own table.** It said 238 escalated and about
  17%; the table under it summed to more than that, and the chain says 258 and
  18.4%. 238 was a leftover from a first scan that silently dropped every
  execution against a since-cancelled upkeep, carried into the prose while the
  table came from the corrected run.
- **"Seven upkeeps fired it, six hit the ceiling" counted only live upkeeps.**
  Sixteen have ever had escalation enabled, twelve fired it, eleven reached a
  ceiling.
- **"The market never had the opportunity to clear" was false**, and the
  document's own numbers refuted it: eleven upkeeps reaching a ceiling means
  the price sat at its maximum with no taker, repeatedly.
- **Refusing to recommend was an abdication.** All three said so. The two
  questions deferred to cannot come out differently on a registry with no
  outside participants, so deferring indefinitely was choosing to keep
  escalation without saying so. Hence the sequencing recommendation above,
  which is what the document had been implying and not stating.
- And the commit that corrected stale keeper counts in `README.md` and
  `START-HERE.md` **left the dateline on the previous day**, so those pages
  briefly mixed two snapshots inside one dated claim. Kimi: "Same disease the
  commit was fixing, in the commit that was fixing it."

Everything load-bearing survived: the split, the premium, the per-keeper
figures, and the finding that no keeper has ever collected only escalated fees.

## Reproducing the measurement

The scan is not checked in as a script, because it is a question you should ask
fresh rather than a number to pin. It is an indexer `search_transactions` over
the app, run to exhaustion, keeping two things: every `register`, for the base
fee and ceiling in its arguments and the upkeep id in its log, and every
`execute`, for its inner payment and its sender. Compare the two.

Three traps, all of which this measurement fell into once:

1. **Do not filter on upkeeps that still exist.** The first version dropped
   every execution against a cancelled upkeep, losing 65 from one upkeep and
   undercounting a keeper by 184.
2. **Do not infer the base fee from the fees paid.** An upkeep that was never
   once executed on time has an escalated fee as its floor. Read `register`.
3. **Take every number from one round.** The counts here are round 66,900,477
   for the executions and 66,901,001 for the live boxes, which is close enough
   to say so and not close enough to leave unsaid.
