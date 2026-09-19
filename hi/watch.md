---
hi: 1
families: [WATCH]
---

# Seeing what the network is doing

## Intent

A network whose work is invisible looks dead even when it is running perfectly. Anybody should be able to ask the chain what the registry is doing right now and get an answer that separates a creator's problem from a keeper's, because blaming keepers for an empty escrow makes the signal useless. The same applies to money: runway should be reported in days, since the same balance is six weeks on a daily schedule and forty minutes on a per-minute one. Punctuality should be measured and published rather than asserted, because it is the only claim here that matters and the easiest one to fudge.

## Criteria

- **WATCH-1**  I can ask what is wrong with a live registry right now and get an answer read from the chain rather than remembered.
  - **WATCH-1.a**  An overdue upkeep is explained, so an empty escrow and a refusing target do not read alike.
  - **WATCH-1.b**  The report says whether the keepers can still afford to keep running.
- **WATCH-2**  I can have the registry announce what changed into a channel people already read.
  - **WATCH-2.a**  It announces the failures as loudly as the executions.
  - **WATCH-2.b**  It names which keeper earned an execution without my having to run an indexer.
  - **WATCH-2.c**  The announcer holds no key and cannot move anybody's money.
- **WATCH-3**  I can see every upkeep's runway in days rather than in microalgo.
  - **WATCH-3.a**  An upkeep whose cadence puts a month of runway out of reach is reported to me rather than topped up, because the answer to that one is to cancel it.
  - **WATCH-3.b**  A top-up plan is printed before anything is signed.
  - **WATCH-3.c**  Nothing is sent until I say to send it.
- **WATCH-4**  I can see how late the registry actually runs, measured rather than asserted.
