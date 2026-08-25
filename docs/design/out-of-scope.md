# Deliberately out of scope

Two features were filed early, deferred through 1.0, and are now closed. Both
were closed on the same reasoning: they answer a question Arcron does not
have, and the version of them that *would* be useful is a different product.

Written down because "we decided not to" is only useful if the reasoning
survives, and because someone will propose both again.

## Keeper staking ([#15](https://github.com/CorvidLabs/arcron/issues/15))

**The proposal:** keepers post a bond, which is slashed for misbehaviour.

**Why it is closed: there is nothing to slash.**

Staking is a mechanism for punishing behaviour a protocol cannot prevent. It
earns its complexity where a participant can profitably do the wrong thing and
be caught afterwards — sign two conflicting blocks, report a bad price, go
offline while holding a duty.

None of that describes an Arcron keeper. A keeper has exactly one action,
`execute`, and it either satisfies the contract or it does not:

- **A wrong execution is impossible.** `call_args` is fixed at registration.
  A keeper cannot change what is called, when it is due, or what it pays.
- **A failed execution is already free — for everyone.** Algorand rejects it
  before it reaches a block, so it costs the keeper nothing and the creator
  nothing (measured in `keeper_e2e.py` stage 14). There is no damage a bond
  would compensate.
- **Not executing is not an offence.** A keeper owes nobody anything. That is
  what makes the network permissionless: the guarantee is that *anyone* may
  execute a due upkeep, never that a particular keeper will.

So a bond would have no trigger. It would add an owner-shaped thing — someone
who decides what counts as misbehaviour and who gets the slashed funds — to a
contract whose whole claim is that it has no owner.

**What it might really be reaching for.** Three adjacent problems are real,
and none of them wants staking:

| Problem | What actually addresses it |
|---|---|
| An upkeep goes unserviced | Fee escalation (#14). A neglected upkeep gets more attractive until somebody takes it — a market, not a bond. |
| Registry spam degrades keepers | Box MBR already prices it, and a keeper that cared would cache boxes rather than rescan. |
| Keepers race and waste work | Escalation desynchronises them, since pouncing the instant an upkeep is due stops being obviously optimal. |

If a future version wants keeper *reputation* — "this keeper has executed
4,000 upkeeps and missed none" — that is an observation over public block
history, not a bond. It needs an indexer, not a contract change.

## Keeper-supplied data ([#22](https://github.com/CorvidLabs/arcron/issues/22))

**The proposal:** let a keeper supply arguments to the call it executes.

**Why it is closed: it inverts the one guarantee Arcron makes.**

Everything Arcron promises rests on a single sentence: *the creator fixes what
is called, and the keeper only decides when.* That is why an upkeep can be
executed by a stranger without trusting the stranger, why a target can accept
a call from Arcron without authenticating the caller, and why "permissionless"
does not mean "unsafe".

Keeper-supplied data removes it. A keeper would choose *what your contract is
told*, which makes every keeper a trusted party and every target responsible
for validating input from an anonymous account it cannot identify. That is an
oracle network. Oracle networks are a legitimate thing to build and they look
nothing like this one — they need staking, aggregation, dispute periods and
usually a permissioned set, which is the opposite of the design here.

**The distinction that matters**, because it is easy to blur: declaring which
*resources* a call may touch ([#8](https://github.com/CorvidLabs/arcron/issues/8))
is safe, and keeper-supplied *data* is not. Availability lets a call reach an
account or an asset; it does not change what the call says. #24 measured that
a keeper can already supply availability, and the trust model is unchanged
precisely because the keeper still cannot alter a single byte of `call_args`.

**What to do instead.** Almost every case that looks like it needs
keeper-supplied data is a target that should pull the value itself:

- **Randomness** — `smart_contracts/rain/` fixes a future beacon round when it
  is scheduled, and whoever resolves the draw supplies the beacon reference.
  The scheduled call decides *when*; the beacon decides *what*.
- **Prices, balances, holdings** — a target reads them in its own inner
  transaction, with the keeper supplying availability.
- **Anything genuinely off-chain** — that is an oracle, and it should be a
  separate contract Arcron calls, so the trust in it is explicit rather than
  smuggled in through the keeper.

The pattern is the same one throughout the docs: **pull the resource**. A
scheduled call is a heartbeat, not a courier.
