# Fee escalation: what it has actually done

**Status:** open. This records a measurement and frames a decision. It does not
make it, because the measurement is strong and the inference from it is not.

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
2026-09-01 at round 66,899,702. 1,387 executions, 79 distinct upkeeps, fifteen
distinct keeper addresses, pagination run to exhaustion.

An execution counts as escalated when its inner payment exceeded the upkeep's
base fee. Base came from the box for upkeeps that still exist, and from the
lowest fee ever paid on that upkeep for the ones since cancelled. The two
methods agree where they overlap.

| | escalated | base | total |
|---|---|---|---|
| `GCQL3M7A…` (GitHub Actions cron) | 102 | 580 | 682 |
| `NUGVPQGZ…` (the laptop) | 143 | 488 | 631 |
| thirteen others | 0 to 6 each | | 1 to 14 each |

**238 of 1,387 executions, about 17%, paid more than base.** All seven upkeeps
with escalation enabled have fired it, and six of the seven have reached their
ceiling at least once. The total paid above base, all time, is **0.831 ALGO**.

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

**It does not establish that escalation does not work.** The event escalation
exists for is an upkeep going unserved for want of a bidder and then being
picked up because the price rose. That has never happened here, because two
keepers cover essentially everything. "Never attracted anyone" and "never had
the opportunity to" produce identical data, and nothing on this chain separates
them. Escalation has not failed a trial. It has not had one.

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

## What decides it

One query, and it is the one above. Before `freeze`, ask both halves:

1. Has any keeper appeared that only shows up for escalated work?
2. Has any upkeep gone unserved for want of a bidder and then been rescued by
   the price rising?

Today the answers are no and no. If the first is still no and the second is
still no at freeze time, escalation is untested rather than proven, and
freezing it in is the expensive choice: the ramp lives in the program rather
than the box, so `update` can change it and `freeze` closes that door forever.
If the second answer ever becomes yes, escalation has done its job and should
stay exactly as it is.

That is why this document does not recommend. The evidence for "it has never
helped here" is strong. The evidence for "it cannot help" does not exist, and
the difference matters more than the 0.831 ALGO does.

## Reproducing the measurement

The scan is not checked in as a script, because it is a question you should ask
fresh rather than a number to pin. It is an indexer `search_transactions` over
the app, filtered on the `execute` selector from
`scripts/registry_health.execute_selector`, comparing each execution's inner
payment against the upkeep's `fee_per_execution`. Run it to exhaustion: an
earlier version of this measurement filtered out upkeeps that had since been
cancelled, which dropped 65 executions from a single upkeep and undercounted
one keeper by 184.
