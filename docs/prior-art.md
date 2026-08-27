# Prior art

Somebody built a permissionless keeper network on Algorand before this one. It
shipped to MainNet, it was funded by an Algorand Foundation grant, and it is
effectively dead. Arcron is the second attempt, not the first, and the useful
thing about the first is what it did differently.

Researched 2026-08-26 from public sources. On-chain figures come from AlgoNode's
MainNet indexer and have not been cross-checked against a second provider, so
treat them as indicative rather than settled.

## BiatecCron, the direct predecessor

Built by [Ludovít Scholtz](https://github.com/scholtz), an established Algorand
developer, under Algorand Foundation grant
[xGov-90](https://github.com/algorandfoundation/xGov/blob/main/Proposals/xgov-90.md).
Its [README](https://github.com/scholtz/BiatecCron) states the idea in terms
that could be lifted from this repository:

> Anyone can execute the tasks. Successful executor will receive reward set in
> the smart contract.

The architecture is close to this one. A singleton task manager holds a box per
task with escrowed funds and a fee, and `executeTask` has no access control at
all: it pays the fee to whoever sent the transaction.

**Its record on MainNet** (app `1765620242`): 439 transactions in total, from
2024-04-20 to 2026-04-09, and nothing since. Three tasks were ever registered,
in two years, and all three belong to the author's own accounts. 431 of the 439
transactions came from a single sender. Development stopped after one month. The
npm package has 981 downloads in over two years. The forum post announcing it
received no substantive replies.

### What it did differently, and why that matters here

Four differences stand out, and Arcron already chose the other way on each. That
is not hindsight: they were chosen for other reasons, and this is the first
evidence that they were the right ones.

**The fee was paid in a token.** BiatecCron's MainNet deployment pays executors
in ASA `1241944285`, a tokenised gold ASA issued by the same person running the
network, worth about eight cents per execution. To keep, you had to opt into a
niche asset, hold it, and value it. Arcron pays ALGO. This is the clearest
difference and probably the most important one.

**There was no fee escalation.** Changing a task's fee is creator-only and
manual, so an underpriced task simply never runs, forever, unless its creator
notices. Arcron's fee climbs toward a creator-set ceiling the longer an upkeep
goes unserviced. No comparable system has this, on Algorand or off it: Chainlink
charges a gas markup, Gelato charges a subscription, Keep3r's fee is set by the
job.

**Every task was its own deployed application**, generated from a visual builder
through a compiler. Expensive to create, a bespoke bug surface each time, and
onboarding a protocol meant extending a component library. An Arcron upkeep is a
box in a registry naming a method that already exists.

**The operator kept large powers with no way to give them up.**
`updateApplication` is creator-gated with no freeze path, and the creator can
move funds with an arbitrary sender. Arcron starts upgradeable too, which is
disclosed on every page of the console, and `freeze` exists to end it.

## AlgoRhythm, the ARC that was never submitted

In January 2026, Paweł Pierścionek, CTO of AlgoNode, which runs the free public
Algorand API infrastructure much of the ecosystem depends on, pushed
[a draft ARC](https://github.com/algorandecosystem/algorhythm) titled
"AlgoRhythm, a Decentralized Task Scheduler on Algorand" to the Algorand
Foundation's own ecosystem organisation. Two commits, same day, never touched
again. It ends in a TODO list whose entries include:

> research decentralized competition
> fee structure (anti spam + incentives)

It was never submitted to the ARCs repository. The person best placed in the
ecosystem to specify this sat down to do it, wrote that the incentive design was
the unsolved part, and stopped.

**There is no ARC covering scheduled execution, automation or keeper
incentives**, and there never has been, not even as a submitted draft. The
nearest neighbour is
[ARC-58](https://github.com/algorandfoundation/ARCs/pull/269), plugin-based
account abstraction, which has the permission half of this primitive and none of
the economic half: a plugin can be callable by anyone, at most once every N
rounds, until round X. It *does* have escrow — an Escrow Factory, and Flat,
Window and Drip allowances, added since this page first described it as having
none. But that escrow funds what the plugin spends on the user's behalf.
**Nothing pays the account that calls it.** Every value-moving path in the
reference `AbstractedAccount` is admin- or plugin-gated, and the example
`SubscriptionPlugin` pays a hardcoded receiver, never `Txn.sender`. It has been
a draft for two years and seven months.

## xGov-116, the one that had the economics right

The claim that nobody on Algorand built the economic half is **wrong**, and this
is the counterexample.

[xGov-116, "Subscription Payments"](https://github.com/algorandfoundation/xGov/blob/main/Proposals/xgov-116.md),
by Kyle Beeding of Akita, was **approved and funded for 50,000 ALGO** in Period
3, January 2024 — months before BiatecCron reached MainNet. Its own description:

> The subscription acts as an escrow... The contract charges a 4% fee with 0.5%
> going to the account that triggers the payment during a valid payment window.
> ... the payment can be triggered by anyone (during the valid window)

Escrow, a permissionless trigger, and a cut to whoever triggers it. That is the
economic half, specified and Foundation-funded, two and a half years ago. One of
its milestones was literally "Automatic Contract Calls".

**It appears never to have shipped.** There is no public repository under the
author's account. What did survive is its design: the author went on to
co-write ARC-58, whose escrow and allowance system reads as this proposal's
descendant — carrying the escrow across and dropping the payment to the
triggerer, which is the half that makes it permissionless infrastructure rather
than a wallet feature.

It also complicates the line elsewhere on this page that Subtopia is the
ecosystem's only subscription platform. It was the only one that *shipped*.

**Nothing in 2026 changed this.** Algorand 5.0, the August 2026 consensus
upgrade, brought post-quantum Falcon accounts, big transactions and AVM v13.
None of it lets a contract wake itself up. The dev portal's own answer to
recurring work is still `algokit-subscriber`, which documents running your own
watcher on a cron.

## The ecosystem hand-rolls half of this already

[Réti](https://github.com/algorandfoundation/reti), the Algorand Foundation's
staking pool contracts, carries this above `epochBalanceUpdate`:

> Note: ANYONE can call this.

with a guard preventing two calls in one epoch. There is no reward, so the
caller pays the fee and gets nothing, and the expected caller is the validator's
own manager account, which the contract keeps funded by skimming commission.
Permissionlessness there is a liveness fallback rather than an incentive. That
gap is what Arcron generalises, and it is worth knowing that the Foundation
built half of it by hand rather than reaching for shared infrastructure.

Folks Finance has permissionless liquidation, which works because the incentive
is in the protocol and needs no keeper network. Subtopia, the ecosystem's only
subscription platform, has no renew or charge method at all: it makes a
subscription an expiring token the subscriber re-acquires, sidestepping
automation entirely. The default answer to "recurring" on Algorand has been to
restructure the problem so that nothing has to fire on a schedule.

## What the wider industry did

Measured against the definition used here, any account, no allowlist, no stake,
no registration, no owner and no token, **none of the four best-known systems
qualifies, and two have shut down.**

Chainlink Automation gates on an active-transmitter set and advertised "no node
competition" as a feature, to avoid users bidding against each other; its v1.x
and v2.1 both sunset in 2026. Gelato's entry points check a single immutable
address and it now sells a fiat subscription. OpenZeppelin Defender shut down in
July 2026 with Actions given no successor. Keep3r's contracts are alive and
skeletal: 42 registered jobs of which 30 have never been worked, 76 registered
keepers of which 50 have never earned anything, and today one job served by two
keepers.

The Keep3r number worth carrying is this: sampling its whole history, **distinct
active keepers peaked at six.** On Ethereum, at a peak TVL of $630M, with a
token that traded in the four figures.

## How many keepers does this actually need?

Fewer than the word "network" suggests, and the reason is arithmetic rather than
modesty.

**A keeper is a loop over boxes, so it does not shard.** One keeper polling
every 2.5 seconds services roughly 1,286 executions an hour at one due upkeep
per round. Ten thousand upkeeps on a one hour cadence average **7.8 due per
round**, which is still one machine's work. Keeper count is not a throughput
constraint and never becomes one.

**It is a liveness constraint.** One keeper and the network works until that
machine dies. Two or three independent operators covers what actually goes
wrong: a box that fails, a cloud region that goes down, and an operator who
loses interest. Past that, another keeper adds redundancy nobody is paying for.

So Keep3r's six was never a capacity ceiling. Six was what the money supported.
Reading it as a shortfall is reading a liveness number as a throughput number,
and the comparison flatters nobody: Ethereum's keeper counts reflect a gas
auction where keepers compete on latency and burn margin doing it. Algorand has
no priority auction, and a losing keeper here pays exactly zero, so that dynamic
is absent.

**This is what the escalating fee is for.** Not price discovery in a crowded
market, which has never existed anywhere. It is the signal that recruits the
second keeper when the first one stops, which is the failure that actually
happens: to Keep3r, which is down to two, and to BiatecCron, which went quiet
when one executor stopped.

## What this means for Arcron

**Keeper diversity is small everywhere.** BiatecCron's single executor is not
evidence that Algorand rejects the idea, because six was the ceiling on Ethereum
at its richest. Designing for a competitive keeper market is designing for
something that has never existed. Designing so that one or two keepers plus a
credible open door is enough is designing for what happens.

Read that way, the escalating fee is most valuable not as a competition
mechanism but as the thing that recruits a second keeper when the first one
stops, which is the failure that actually occurs.

**The number to defend is not keeper count. It is users who are not us.**
BiatecCron ran three tasks for two years and all three were the author's. That
is the outcome to avoid, and it is a distribution problem rather than an
engineering one. Arcron's own open question, in
[`status.md`](status.md), is the same one: nobody who is not us has registered
an upkeep yet.

**Nothing here is a claim to be first, and xGov-116 is why.** Somebody
specified escrow plus a permissionless trigger plus a cut to the triggerer, and
was funded to build it, in January 2024. The idea has been had. What has not
happened is somebody shipping it and keeping it running.

The defensible claims are narrower and survive contact with this research: the only maintained permissionless keeper
network on Algorand, fees in ALGO rather than a bespoke token, upkeeps as
registry entries rather than generated contracts, and an escalating fee, which
no comparable system has anywhere.
