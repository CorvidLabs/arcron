# Design: catch-up policy and fee escalation

**Status: **shipped**. Escalation, the catch-up policy and the fee cap are all
in the deployed contract; this page is kept as the reasoning that produced them..** Issues [#7](https://github.com/CorvidLabs/arcron/issues/7)
and [#14](https://github.com/CorvidLabs/arcron/issues/14) must be designed
together. This is that design, for review before any code is written.

Both change the `Upkeep` struct, and that has a consequence worth stating
before anything else.

## The keeper contract cannot be upgraded

There is no update or delete path, deliberately: an upgradeable keeper contract
is one where somebody can change the rules after you have escrowed funds.

So a struct change is **not a migration.** It is a new application, at a new
app id, with an empty registry. Every existing upkeep must be cancelled by its
creator and re-registered against the new app, and nobody can do that on their
behalf, because `cancel` is creator-only. That has happened once already
([#3](https://github.com/CorvidLabs/arcron/issues/3)) and stranded 243,000
µALGO of box MBR in the old app.

**Therefore: batch every struct change into one deployment.** #7, #14,
[#8](https://github.com/CorvidLabs/arcron/issues/8) and
[#9](https://github.com/CorvidLabs/arcron/issues/9) all touch the struct.
Landing them separately means four redeployments and four rounds of asking
every creator to move. This design covers #7 and #14; the recommendation at the
end is to hold the deployment until #8 and #9 are decided too.

## Why they cannot be designed apart

Under catch-up semantics, an upkeep neglected for N intervals fires N times in
a row. Under escalation, a late upkeep pays more. Combine them naively, with
escalation measured from the schedule as #14 proposes, and **every replayed
execution pays the escalated fee**, because clearing one slot barely reduces
how overdue the upkeep is.

Simulated with a 100-round interval, a 4,000 µALGO base, a 12,000 µALGO cap,
and an upkeep funded with 400,000 µALGO, which its creator would reasonably
call "a hundred runs":

| | Executions | Escrow spent | Share of escrow |
|---|---|---|---|
| Neglected 20 intervals, naive rule | 20 | 232,000 µALGO | **58%** |

Fifty-eight per cent of the escrow, in a burst of a few rounds, for twenty
executions nobody asked for, at three times the price the creator agreed to.
The two features are individually reasonable and multiply into something the
creator cannot have modelled.

## The demos already disagree about catch-up

This is the strongest argument that the policy must be per-upkeep rather than a
protocol constant. At the time this was written, three shipped demos gave
three different right answers; `treasury` and `deadman` were later cut from
the repository ([2026-08-26](../status.md)) as example contracts whose review
findings outweighed their purpose, but the disagreement they illustrated is
unchanged:

| Demo | Missed a week. What should happen? |
|------|-----------------------------------|
| `rain` (daily draw; [moved to its own repository](https://github.com/CorvidLabs/arcron-rain) on 2026-08-31) | **Skip.** Replaying seven draws in one burst is absurd; only the latest matters. |
| `treasury` (scheduled distribution, no longer in the repo) | **Catch up.** Every period's deposits must be distributed; skipping silently loses a week of allocations. |
| `deadman` (dead man's switch, no longer in the repo) | **Skip.** It fires once and goes inert; replays are pure waste. |

A protocol-wide constant would be wrong for two of the three whichever way it
was set.

## Proposal

### 1. Catch-up policy, chosen at registration

```
CATCH_UP   = 0   next_due += interval                       (today's behaviour, the default)
SKIP_AHEAD = 1   next_due += (missed + 1) * interval        where missed = (round - next_due) / interval
```

`SKIP_AHEAD` snaps forward to the next slot strictly in the future while
preserving the schedule's phase. A daily upkeep stays aligned to its original
time-of-day rather than drifting to whenever the keeper happened to arrive.
Zero is today's behaviour, so nothing registered under the current contract
changes meaning.

### 2. Escalation expressed as a ceiling, not a rate

#14 proposes `fee + overdue * escalation_rate`, capped. Two numbers to reason
about, one of which (`escalation_rate`, µALGO per round) nobody has intuition
for.

Instead, the creator sets the fee they expect to pay and the most they will
ever pay, and the contract fills in the curve:

```
lateness  = Global.round - last_serviced_round
excess    = max(lateness - interval, 0)
effective = fee + (fee_cap - fee) * min(excess, interval) / interval
```

The fee rises linearly from base to cap over one missed interval, then holds.
`fee_cap == 0` means "no escalation", so it is backward-compatible in intent
and free for anyone who does not want it.

Both inputs are quantities a creator already understands, and worst case is a
division they can do in their head: `balance / fee_cap` runs, always.

### 3. Escalation is measured from the last service, not from the schedule

This is the rule that defuses the interaction, and it follows from what
escalation is *for*. Escalation exists to clear a market: an upkeep nobody
wants becomes worth doing. Once a keeper has arrived, **the market has
cleared.** The rest of the backlog is the same keeper draining the same queue,
and there is no market-clearing argument for paying triple for it.

So the contract records `last_serviced_round` and measures lateness from it.
The first execution of a burst pays the escalated, market-clearing price; the
remaining replays pay base, because by then the upkeep was serviced moments
ago and is not late at all.

Same scenario as above:

| | Executions | Escrow spent | Share |
|---|---|---|---|
| Naive (escalation from the schedule) | 20 | 232,000 µALGO | 58% |
| **Proposed (escalation from last service)** | 20 | **88,000 µALGO** | **22%** |

One execution at the 12,000 µALGO ceiling, nineteen at the 4,000 µALGO base.
The escalation still does its job, since it is what got a keeper to show up,
and the burst costs what the creator budgeted.

### 4. `last_serviced_round` fixes something already broken

The field is not only for escalation. Nothing on-chain currently records when
an upkeep actually ran; both the console and the notifier derive it as
`next_execution_round - interval_rounds`, which is the round it was
*scheduled* for. Those differ by exactly the backlog whenever an upkeep is
catching up.

That has already caused one real bug: the notifier
([#27](https://github.com/CorvidLabs/arcron/issues/27)) attributed executions
to the wrong block for any lagging upkeep, because it looked in the scheduled
round. It now searches the elapsed range instead, a workaround for a fact the
contract should simply record.

## Cost

Three new `uint64` fields (policy, `fee_cap`, `last_serviced_round`) add 24
bytes per box, so box MBR rises from 41,300 to 50,900 µALGO for a 4-byte
selector. That is paid by the registrant and refunded on cancel, so it is a
deposit rather than a cost, but it raises the entry price of an upkeep by
about 23%.

Not proposed: `escalation_rate` as a fourth field. The base/cap formulation
above removes the need for it, which is most of why it is worth preferring.

## What has to move together

Per [#31](https://github.com/CorvidLabs/arcron/issues/31), a struct change is
a five-file lockstep or the bot and console silently misread the registry:

1. `smart_contracts/keeper/contract.py`: struct, `register`, `execute`
2. `scripts/keeper_bot.py::_decode_upkeep`, where offsets shift
3. `js/src/upkeep.ts`, its TypeScript twin
4. `tests/test_keeper_bot.py`: the pinned box vector, plus its twin in `js/test/upkeep.test.ts`
5. `specs/keeper/`: Public API, requirements, testing, Change Log

Beyond the struct:

- **The bot** should choose work by effective fee, descending, rather than by
  upkeep id. Today it takes them in registry order, which is exactly the
  behaviour escalation exists to change.
- **The console** shows base fee, current effective fee and cap, and the
  dormancy threshold, because `balance >= effective_fee` means an upkeep can
  go dormant at a higher balance than its creator expected. The board's
  "reward" column should rank on effective fee.
- **`scripts/keeper_e2e.py`** gains a case per policy: neglect an upkeep for
  three intervals under each and assert execution count and escrow drawdown.

## Open questions for review

1. **Should `SKIP_AHEAD` be the default?** Two of three demos want it, and
   accrual-shaped work is probably the rarer case. Against: it changes the
   meaning of every upkeep registered so far, and a silent semantic change on
   redeployment is worse than a default nobody loves. Recommendation: keep
   `CATCH_UP` as zero, and have the console default the *form* to
   `SKIP_AHEAD` so new users get the safer behaviour without the encoding
   lying about it.
2. **Cap as an absolute fee or a multiple of base?** Absolute is proposed:
   it is what the creator's escrow arithmetic is denominated in. A multiple
   would be one field either way.
3. **Should escalation also raise the dormancy threshold?** ~~As specified,
   yes.~~ **Resolved: no.** The alternative in this entry is what shipped. The
   fee falls back to `fee_per_execution` when the escrow cannot cover the
   escalated one, because the version specified here is a one-way door:
   lateness only grows, so an escrow that once fell below the escalated price
   could never reach it again, and the upkeep would hold up to a full ceiling
   of escrow that no keeper could spend. The cost is that the fee is not always
   what it says, which is the trade that was taken. See Invariant 12 in
   `specs/keeper/keeper.spec.md`. This entry is left in place because
   `docs/integrating.md`, `docs/arcron.md` and `specs/keeper/requirements.md`
   all repeated the un-taken answer as fact for months.
4. **Is a linear curve right?** It is easy to reason about and easy to verify.
   Anything steeper is harder to defend without evidence that keepers ignore
   linearly-escalating work, which nobody has yet.

## Recommendation

Land #7 and #14 together, in one deployment, and **hold that deployment until
#8 and #9 are decided.** Every struct change costs a redeployment and asks
every creator to move their upkeeps by hand.

Implementation order once the design is agreed: contract and spec first, then
the two decoders and both pinned vectors in the same commit, then the bot's
selection logic, then the console, then the e2e cases. Deploy last, and only
after `fledge lanes run local` is green on all of it.
