---
hi: 1
families: [KEEPER]
---

# Running a keeper

## Intent

Running a keeper should be a plain process somebody starts on a machine they already have, and it should pay for itself rather than needing a budget. Before spending the hour, they should be able to find out what the registry has actually been paying and what their share of it would be, net of what sending the executions costs them. Once it is running it should be honest about whether it is alive, whether it can still afford to work, and what it is skipping and why, because a keeper that fails silently takes the whole network down with it. Afterwards the operator should be able to see what their own keeper earned without that view ever touching a key.

## Criteria

- **KEEPER-1**  I can find out what running a keeper here would earn me before I install anything.
  - **KEEPER-1.a**  The estimate divides what the registry pays by the keepers already there, because arriving splits the work rather than creating it.
  - **KEEPER-1.b**  The estimate is net of what sending those executions costs me.
  - **KEEPER-1.c**  Work whose target would refuse the call is not counted as money on the table.
- **KEEPER-2**  A keeper earns more per execution than the fees it spends, so it needs a starting balance rather than an ongoing budget.
- **KEEPER-3**  The keeper refuses to start when it cannot cover its own account and one execution.
  - **KEEPER-3.a**  It says exactly which of those it cannot cover.
  - **KEEPER-3.b**  I am warned that it is running low while there is still time to fund it.
- **KEEPER-4**  I can tell from outside whether my keeper is still alive.
  - **KEEPER-4.a**  Silence on an idle registry is not reported to me as a fault.
- **KEEPER-5**  An upkeep whose target keeps failing is retried more and more slowly rather than on every scan.
  - **KEEPER-5.a**  That backoff survives a restart, so a keeper run from cron does not re-attempt a doomed upkeep every time.
  - **KEEPER-5.b**  Losing a race to another keeper never counts against an upkeep's backoff.
  - **KEEPER-5.c**  I can clear one upkeep's backoff myself once I have fixed its target.
- **KEEPER-6**  The keeper says which upkeeps it is skipping.
  - **KEEPER-6.a**  It says what skipping them is leaving unclaimed, so I can decide whether to care.
- **KEEPER-7**  I can run a keeper on a machine I already own, as a container, a service or a background agent.
  - **KEEPER-7.a**  It comes back by itself after the machine reboots.
  - **KEEPER-7.b**  The signing mnemonic lives somewhere only the keeper can read.
- **KEEPER-8**  I can run a keeper without owning a machine at all.
  - **KEEPER-8.a**  I am told what that costs me in punctuality before I choose it.
- **KEEPER-9**  I can forward what a keeper earns to my own wallet without starving the account it signs from.
- **KEEPER-10**  A keeper keeps working when a public node starts refusing to answer.
- **KEEPER-11**  Two keepers watching the same registry compete for the same work rather than quietly taking turns.
  - **KEEPER-11.a**  I can tell afterwards whether I lost a race or whether the work was never there.
- **KEEPER-12**  I can see what my own keeper has earned and what it has executed.
  - **KEEPER-12.a**  That view never holds a signing key.
  - **KEEPER-12.b**  It stays on my own machine rather than being published.
