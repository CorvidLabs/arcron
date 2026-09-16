---
hi: 1
families: [GOVERN]
---

# Running a deployment

## Intent

Anyone should be able to run an Arcron of their own, and whoever does holds a real power over other people's escrow until they give it up. The tooling should make every permanent decision deliberate — check the things creating the application fixes forever before it exists, ask for the deployment to be typed back before freezing it, and rebuild from source rather than signing bytes it was handed. Giving up the update key should be a normal, checkable event rather than a promise, and until it happens the operator should be told the moment somebody who is not them escrows anything.

## Criteria

- **GOVERN-1**  I can stand up a registry of my own on any network in one command.
  - **GOVERN-1.a**  Everything that creating the application fixes forever is checked before the application exists.
  - **GOVERN-1.b**  A mistyped network cannot reach real money.
- **GOVERN-2**  A new deployment starts able to be fixed.
  - **GOVERN-2.a**  Its operator gives that power up permanently before asking anybody to rely on it.
- **GOVERN-3**  Giving that power up asks the operator to type the deployment back before it acts, because it is the one thing here that can never be undone.
- **GOVERN-4**  An operator can hold a deployment behind a multisig rather than a single key.
  - **GOVERN-4.a**  One holder of that multisig cannot act alone.
- **GOVERN-5**  Replacing a deployment's programs rebuilds them from source rather than trusting a file the operator was handed.
- **GOVERN-6**  The page that authorizes permanent changes runs on the operator's own machine.
  - **GOVERN-6.a**  It is never published anywhere a stranger could reach it.
- **GOVERN-7**  An operator can recover the escrow and deposits stranded in a deployment they have abandoned.
  - **GOVERN-7.a**  That reach extends only to upkeeps the operator registered themselves.
- **GOVERN-8**  Each release stage names what it freezes.
  - **GOVERN-8.a**  It names what is at stake if that turns out to be wrong.
- **GOVERN-9**  A release records the hash and the commit, so the claim that an app is a given build can be checked without trusting anybody.
- **GOVERN-10**  A claim about how long a deployment has run stops counting the moment the code stops matching what is deployed.
- **GOVERN-11**  An operator is told when somebody who is not them registers an upkeep, because that is a real person who has trusted them.
