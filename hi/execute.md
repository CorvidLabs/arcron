---
hi: 1
families: [EXECUTE]
---

# Executing what is due

## Intent

The moment an upkeep comes due it becomes work anybody can take, and the contract's job is to make taking it safe enough that no trust is needed in either direction. A keeper should be paid in the same breath as the call it made, never before, and losing to a faster keeper should cost nothing at all. Everything about the price and the risk should be readable from the registry before a keeper commits anything. A hostile target should be able to waste a keeper's time and nothing else.

## Criteria

- **EXECUTE-1**  Any account at all can execute a due upkeep, with no allowlist, no stake and no registration.
- **EXECUTE-2**  A keeper is paid in the same group as the call it made, so the fee moves only if the work actually happened.
- **EXECUTE-3**  A keeper that loses a race to a faster one pays nothing at all.
- **EXECUTE-4**  A keeper can work out exactly what an upkeep pays right now before deciding to take it.
- **EXECUTE-5**  One upkeep's escrow can never pay for another upkeep's execution.
- **EXECUTE-6**  An upkeep left unserviced pays more the later it gets, up to the ceiling the person who registered it set.
  - **EXECUTE-6.a**  A keeper draining a backlog is paid the ordinary fee for each replay rather than the escalated one.
  - **EXECUTE-6.b**  An upkeep whose escrow has fallen below the escalated price stays executable at its ordinary fee.
- **EXECUTE-7**  A target that refuses the call costs the keeper nothing.
  - **EXECUTE-7.a**  An escrow is left untouched when the call it would have paid for did not happen.
- **EXECUTE-8**  A hostile target cannot turn the call it was given into an attack.
  - **EXECUTE-8.a**  It cannot re-enter the registry.
  - **EXECUTE-8.b**  It cannot reach a keeper's own funds.
  - **EXECUTE-8.c**  It cannot alter the upkeep that called it.
  - **EXECUTE-8.d**  The worst it can do to a keeper is waste their time.
- **EXECUTE-9**  An execution reaches whatever accounts, assets and apps the target needs to touch.
  - **EXECUTE-9.a**  I do not have to declare those references when I register, because I may not know them yet.
- **EXECUTE-10**  A target needing more references than one call can carry is named as unservable before anybody escrows against it.
