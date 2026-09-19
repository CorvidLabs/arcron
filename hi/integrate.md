---
hi: 1
families: [INTEGRATE]
---

# Pointing it at your own contract

## Intent

Hooking a contract up to Arcron should be one method and an afternoon, and the guide should be one pass rather than five places. The expensive mistakes here are not compile errors — they are hooks that fail and quietly stop being serviced, hooks that count something anybody can fast-forward, and payouts that wedge everybody's schedule when one recipient goes bad — so the guide's real job is to name those before somebody escrows against them. Somebody working from another language, or reading over an API, should find the exact facts they need on the page they are reading rather than a pointer to an example they cannot import.

## Criteria

- **INTEGRATE-1**  I can watch a real upkeep run against a contract I did not write before committing to integrating.
  - **INTEGRATE-1.a**  That takes ten minutes and an amount of test ALGO I mostly get back.
- **INTEGRATE-2**  Pointing Arcron at a contract I wrote is one method that takes no arguments of its own.
- **INTEGRATE-3**  I can copy a complete, compiling target rather than assembling one out of prose.
- **INTEGRATE-4**  I am told whether to restrict my hook to the registry or leave it open to anybody.
  - **INTEGRATE-4.a**  I am told what each of those choices does not buy me.
- **INTEGRATE-5**  I am told to return rather than fail when there is nothing to do.
  - **INTEGRATE-5.a**  I am told what failing costs me.
- **INTEGRATE-6**  I am warned that my hook may be called more than once, and in bursts after an outage.
- **INTEGRATE-7**  I am told plainly that a cadence is counted in rounds and slides against the calendar.
- **INTEGRATE-8**  I can enforce my own minimum spacing when my hook counts or accrues, because anyone at all may register an upkeep against me.
- **INTEGRATE-9**  I am given one pattern for paying counterparties that does not wedge everybody's schedule when a single payout fails.
- **INTEGRATE-10**  I can pass arguments to my hook, fixed at the moment of registration.
  - **INTEGRATE-10.a**  I am not held to a handful of arguments when my hook needs more than that.
- **INTEGRATE-11**  I can build the registration group from a language that has no client library.
  - **INTEGRATE-11.a**  The method's exact signature is written out where I am reading, rather than left in an example I cannot import.
  - **INTEGRATE-11.b**  The order the transactions have to go in is written out the same way.
  - **INTEGRATE-11.c**  The box the call has to reference is named.
  - **INTEGRATE-11.d**  The deposit is given as a formula I can work out myself.
- **INTEGRATE-12**  I can read the registry and build the transactions that change it from TypeScript, with no backend and no indexer.
- **INTEGRATE-13**  I can stand up a keeper of my own to test against on a local chain in one command.
- **INTEGRATE-14**  I am told which failures a test against a mock cannot show me, so a green suite does not convince me the payout works.
