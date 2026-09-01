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
| 508 unit tests pass | 508, unchanged |
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
calls inside 6 rounds — short enough that an honest keeper on schedule is
never blocked.

| | |
|---|---|
| honest keeper, on schedule | creator paid 7,600 |
| attacker calls the target directly, once | costs the attacker 1,000 |
| honest keeper, at the due round | **blocked**, and it cost them 0 |
| attacker executes when the window reopens | creator paid **25,600** |
| attacker's net over the episode | **+21,600 µALGO** |

One cycle, one blocking call: the creator paid 21,600 more than base and the
attacker kept it, a **22x return** on the 1,000 it cost to arrange.

A six-cycle run of the same setup, attacking only the second, has the creator
paying 49,200 against a nominal 24,000. Attribute that honestly: 21,600 of
the 25,200 excess is the attack, and the remaining 3,600 is the first cycle
running a single round late on a ten-round interval, which is LocalNet's
round granularity rather than anybody's doing. It is worth noticing anyway,
because it shows how steep the ramp is: on a ten-round interval with a ten-fold
cap, **one round late costs 90% of the base fee again.** Real cadences are
thousands of rounds and do not have this problem; short ones do.

**Honest limits, all of which matter.**

- It needs `fee_cap > fee_per_execution`. On the live registry today that is
  **7 of 33 upkeeps**, each with a gap of 8,000–10,000 µALGO per manufactured
  cycle. The rest have escalation off and are immune.
- It needs a blockable target. A heartbeat that always succeeds — `pulse`,
  the dogfood — is immune, and so is any target with no third-party-movable
  precondition. Which of the seven are genuinely exposed was not established
  per target, and should be.
- It self-heals. Once the attacker stops, CATCH_UP re-phases the upkeep and
  honest keepers are back on base within two cycles. This is extraction, not
  capture.
- Every µALGO stays inside the cap the creator chose. The contract does
  exactly what it says.
- Four live targets already have more than one upkeep pointed at them, which
  is the cheapest form of this: your own upkeep's execution trips the guard
  on a schedule, and you never send a blocking transaction at all.

**What to do.** Not a contract change, at least not now.

1. **Advice, immediately.** An upkeep whose target can be blocked by anyone
   should set `fee_cap = 0`. Escalation is worth having when lateness is
   exogenous and worth nothing when it is purchasable, and the creator is the
   only one who knows which their target is. This belongs in the console next
   to the fee-cap field and in `docs/integrating.md`.
2. **A ramp that is not one interval, later.** Escalation currently runs from
   base to cap across a single missed interval, so one blocked cycle buys the
   whole ceiling. Spreading it over several intervals would make the attacker
   pay to block repeatedly while an honest keeper needs to get through once.
   Worth noting: **the ramp is in the program, not in the box**, so this is
   reachable by `update` and does not restart the struct clock or the MainNet
   gate. That is the one place this finding could still be acted on cheaply
   after a freeze — and after a freeze, it could not.

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
  settled, and the audit's guess at the cause was wrong. It is not our
  request rate. The endpoint answers with
  `x-and-quota: block=1;reqs=230824` and a plain-text
  `Daily free API quota exceeded`: Nodely's free tier allows 200,000 requests
  a day *shared across everyone using that endpoint*, and it was 30,000 past
  it before this repository sent anything. Once blocked, the edge sheds a
  fraction rather than refusing everything — measured at 4 refusals in 45
  requests, about 9%. A health run makes about 40 requests, and
  `1 - 0.91^40` is 98%, which is why two runs in three died while a single
  `curl` never did. Nothing here is fixable by sending less. The clients
  `net.connect` hands out now retry a 403 or 429 up to five times
  (`scripts/node_retry.py`), which every script gets by connecting. Three
  live `health` runs afterwards: all three succeeded, recovering from 1, 6
  and 2 refusals.

## 6. Would this change the MainNet answer?

No. The audit said yes-with-two-conditions, and both conditions survive: fund
the base minimum balance and prove it, and keep the creator a multisig with a
freeze decision made on a date. Section 3 adds a third thing, and it is
advice to creators rather than a gate: **tell them what `fee_cap` costs
them when their target can be blocked.** A creator who leaves it at 0 cannot
be hurt by anything in this document.

The one thing worth carrying into the freeze decision is section 3's second
recommendation. The escalation ramp is the only finding here that is
fixable by `update` and unfixable after `freeze`, which is a small but real
argument for the order those two happen in.
