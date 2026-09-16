---
hi: 1
families: [SCHEDULE]
---

# Scheduling a call

## Intent

Somebody with a contract that has to do something on a schedule should be able to say "call this method every N rounds, here is the money" and then stop thinking about it. The schedule lives on chain in escrow they can take back, and once it is registered nobody — not the keeper that runs it, not us — can change what gets called. The decisions that cannot be undone later should be put to the person at the moment they are made, in the words of their consequence rather than as a field to fill in. A registration that could never work should be refused before any money moves, rather than accepted and left holding funds it can never spend.

## Criteria

- **SCHEDULE-1**  I can register a scheduled call against any app on the chain without asking anyone's permission.
  - **SCHEDULE-1.a**  Registering escrows my ALGO in the contract rather than handing it to anybody.
  - **SCHEDULE-1.b**  Nobody, not even the keeper that runs it, can change what my upkeep calls once it is registered.
  - **SCHEDULE-1.c**  The deposit that holds the registration comes back in full when I cancel.
- **SCHEDULE-2**  I choose at registration what should happen to runs that get missed.
  - **SCHEDULE-2.a**  That choice is put to me as what it will do to my escrow rather than as a number to set.
  - **SCHEDULE-2.b**  I am warned that replaying missed runs can spend a whole escrow in one burst after an outage.
  - **SCHEDULE-2.c**  Skipping missed runs keeps the schedule's own phase instead of drifting to whenever a keeper happened to turn up.
- **SCHEDULE-3**  Anyone can top up an upkeep, whether or not they registered it.
- **SCHEDULE-4**  Only the person who registered an upkeep can cancel it.
  - **SCHEDULE-4.a**  Cancelling returns everything still sitting in the escrow, not only the deposit.
- **SCHEDULE-5**  A registration that could never work is refused before my money moves rather than accepted and left stuck.
  - **SCHEDULE-5.a**  A fee too low to pay for the work it asks for is refused outright rather than quietly raised to the floor.
  - **SCHEDULE-5.b**  Somebody else cannot take ownership of the upkeep my payment funded by signing the call themselves.
- **SCHEDULE-6**  I can cap what a single run is ever allowed to cost me.
- **SCHEDULE-7**  I can leave that cap off entirely rather than being made to guess a number I cannot predict.
- **SCHEDULE-8**  I can pay keepers a bonus in my own asset on top of the ALGO fee, never instead of it.
  - **SCHEDULE-8.a**  A keeper who does not hold my asset still runs the upkeep.
  - **SCHEDULE-8.b**  A bonus that cannot be delivered stays in my escrow rather than being lost.
  - **SCHEDULE-8.c**  An asset I can no longer receive never blocks my ALGO coming back when I cancel.
- **SCHEDULE-9**  An upkeep that has run out of money goes quiet rather than doing anything surprising.
  - **SCHEDULE-9.a**  It starts running again the moment somebody tops it up, with nothing to re-register.
