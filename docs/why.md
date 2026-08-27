# Why this exists

Every serious chain should have a way to say *"call this later"* without
requiring a server. Algorand does not have one.

That is the whole claim. It is smaller than "agents need this" and larger than
"we built a bot", and it is the version that survives being wrong about
everything else on this page.

## The argument that does not need agents

A smart contract cannot wake itself up. Something off-chain has to call it.
Today, on Algorand, that something is a bot you write, host, key and monitor.

The cost comparison usually quoted is the wrong one. It is tempting to compare
Arcron's 0.004 ALGO fee against the 0.001 ALGO of calling your own contract
directly and conclude that a keeper is a 4x markup. That compares a fee to a
fee. The comparison anyone actually faces is against the cost of *owning a bot*.

Here it is honestly, which is less flattering than the first draft of this page:

| one hourly schedule, per month | cost | what you give up |
|---|---|---|
| **Arcron** at the 4,000 µALGO floor | ~3.03 ALGO ≈ **$0.28** | see below — it is not nothing |
| **Arcron** at the suggested 10,000 | ~7.58 ALGO ≈ **$0.69** | as above |
| fly.io shared-cpu-1x | ~$2.02 | you write, host, key and monitor the bot |
| Hetzner CX22 | ~$4.10 | as above |
| AWS Lambda + EventBridge | **$0.00** | as above, but genuinely free at this volume |
| Oracle Always Free | $0.00 | free, but Oracle reclaims idle instances — see below |
| GitHub Actions cron | $0.00 | delayed under load, and runs may be dropped |

**Every figure on this page uses one basis**, because two earlier drafts mixed
two and produced a multiplier that did not reproduce from their own tables. The
basis: 2.66 s/round measured, so a nominal-hour upkeep of 1,286 rounds fires
every 57.0 minutes, **758 times a month**, at ALGO $0.0907. Recompute rather
than quote.

**About 7.2x cheaper than the cheapest paid host** at the floor, and **2.9x** at
the suggested fee. An earlier draft said 7.7x in both places; against fly.io's
$2.02 the first is 7.2x and the second is 2.9x, and repeating one number for
both overstated the suggested fee by two and a half times. Two independent
reviews caught it.

**Against the free options it is not cheaper at all**, and the honest thing is
to say which ones actually work. AWS Lambda plus EventBridge Scheduler at 720
invocations a month sits deep inside a perpetual free tier, does not drift, and
does not expire; it defeats this comparison outright for anyone already on AWS.
Oracle's Always Free tier is genuinely free for the life of the account, but it
reclaims instances whose CPU, network **and** memory 95th percentiles all sit
under 20% over seven days, which is the exact profile of a polling keeper.
GitHub Actions is free and does not auto-disable in a **private** repository —
the 60-day rule an earlier draft stated unconditionally applies only to public
ones.

## Where this stops being true

**Above about 10 hourly upkeeps, running your own bot on the cheapest paid host
is cheaper.** The crossover depends entirely on which host you compare against,
and an earlier draft quoted 26 — the figure for a $5 host — on a page whose own
table quotes $2.02:

| against | crossover |
|---|---|
| fly.io, $2.02 | **10 upkeeps** |
| Hetzner, $4.10 | 20 |
| a $5 host | 24 |

One process services any number of targets from one key: `scripts/keeper_bot.py`
in this repo is a single process servicing the entire registry, so "ten
contracts means ten bots" — which an earlier draft asserted — is false, and
false in a way this repository disproves.

The real asymmetry is narrower and survives: **no hot key, and no operational
attention.** Arcron needs neither. That is worth something and it is not a
process count.

**And the ratio is a bet on the ALGO price, not a property of the design.** It
moves *against* Arcron precisely when Algorand succeeds:

| against | parity at ALGO |
|---|---|
| $4.10/mo | $1.35 |
| $2.02/mo | **$0.67** |

**ALGO has not traded near either price in years, and an earlier draft of this
page said it had.** It last closed above $0.70 on 2022-04-28 and above $1.42 on
2022-01-13; its high over the last two years is **$0.6135**, below even the
lower parity point. Checked against Kraken's ALGOUSD history, not recalled.

That correction makes this argument weaker, not stronger, which is why it is
here. Parity needs roughly a sevenfold rise from spot. The point that survives
is narrower: a fiat-denominated competitor and a crypto-denominated one cannot
be compared with a fixed multiple, and the ratio moves against Arcron precisely
when Algorand does well. This page should not be read as if the multiple is a
property of the design.

That is the third correction to the numbers on this page, after a 19x that was
really 7.2x and a crossover quoted against a host the page does not recommend.
Every one was found by a reviewer who recomputed instead of quoting. Do that.

## The floor price is below the cost of supplying it

This is the structural finding, and it is not comfortable.

At the 4,000 µALGO minimum a keeper nets 1,000 per execution, so **one keeper
needs roughly 73 concurrent hourly upkeeps to fund a $5 host.** A creator
crosses over to self-hosting at 10 against the cheapest paid host. Those numbers
are the wrong way round: the floor is priced for the creator and sits below the
cost of supplying it.

Raising the fee closes it, because the ratio is `(fee − 1000)/(fee − 3000)`:

| fee | creator pays/mo | creator crossover vs $2.02 | keeper funds a $5 host at |
|---|---|---|---|
| 4,000 µALGO (the floor) | $0.28 | 10 | **73** |
| 10,000 µALGO | $0.69 | 3 | **10** |
| 20,000 µALGO | $1.38 | 2 | 5 |

**Around 10,000 the two converge at about 10 upkeeps and the network pays for
itself**, which is roughly what the registry holds. It is still 2.9x cheaper
than the cheapest paid host — not the 7.7x an earlier draft claimed here, which
was the floor's ratio against a $5 host reused for a different fee against a
different host. The contract half-admits the underlying point already: *"A
creator who wants keepers who do not care about their token should set a fee
above this floor."*

## What you give up

The `plus: nothing` in an earlier draft was false. Concretely:

- **Wall-clock drift, which accumulates.** Arcron schedules in rounds, and
  rounds are not a clock. At the measured 2.66 s/round an upkeep set to a
  nominal hour fires every 57 minutes and slides **roughly 36 hours against the
  calendar over a month**. A cron fires at :00 forever and never gains phase. On
  the exact axis this page uses to dismiss GitHub Actions, Arcron is worse, and
  worse without bound. [`arcron.md`](arcron.md) has the drift table.
- **Liveness now depends on someone else's server**, and today every keeper is
  one of ours.
- **An upgradeable contract holds your escrow** until its creator freezes it.
- **`CATCH_UP` replay after an outage** costs one fee per missed interval. See
  [`integrating.md`](integrating.md) for the measured incident.
- **A hard 4x floor over calling it yourself**, 2,000 µALGO of which is
  irreducible protocol overhead. No amount of keeper competition removes it.

What you gain, and what the arithmetic above under-sells: no hot key, no host,
no monitoring, no on-call, and a schedule that survives you. That is the actual
product, and it does not need a 19x to be worth having.

## The argument that does need agents, stated at its real strength

x402 and the agent-to-agent payment work give agents a way to **pay**. Nothing
in that stack gives them a way to **wake up**. An ecosystem is about to have a
payment verb and no scheduling verb.

That is not a reading of the tea leaves. x402's own specification says so, in a
section headed **Out of Scope**:

> - **Recurring payments**: Automatic periodic charges without new authorizations
> - **Open-ended allowances**: Authorizations without time bounds or single-use constraints

And the Algorand work is real and current: the Foundation ships
[x402 demos](https://github.com/algorandfoundation/x402-demo) and a
[tutorial](https://dev.algorand.co/resources/x402-on-algorand/), Algorand's
`exact` scheme is
[merged into the upstream spec](https://github.com/x402-foundation/x402)
alongside evm, svm, aptos, hedera and stellar, and an xGov proposal funded a
multi-chain facilitator. Every one of those flows begins with a live client
making a request. Nothing in the stack fires on its own.

That gap is real, and it is worth being precise about how much it proves:

**The weak form is wrong.** "Agents need scheduling, so they need Arcron" does
not follow. An agent alive enough to hold funds and make decisions is alive
enough to call its own contract, and doing so is cheaper.

**The strong form is the interesting one.** Arcron wins when the schedule
should *outlive the agent that created it* — when autonomy should not be only
as durable as somebody's process. That is liveness that survives its author,
and no amount of agent tooling provides it, because every agent framework
assumes the agent is running.

Whether anyone wants that is an open question. It is stated here as a question
rather than a claim.

## What is missing from the ecosystem

There is **no ARC covering scheduled execution, automation or keeper
incentives**, and there never has been, not even as a submitted draft.

The nearest attempt is [AlgoRhythm](prior-art.md), pushed by the CTO of AlgoNode
in January 2026: two commits, same day, never touched again, ending in a TODO
list whose entries include "fee structure (anti spam + incentives)". The person
best placed in the ecosystem to specify this sat down, wrote that the incentive
design was the unsolved part, and stopped.

The Foundation's own [Réti](prior-art.md) contracts hand-roll half of it, with
`epochBalanceUpdate` marked *"Note: ANYONE can call this"* and no reward
attached. Permissionless as a liveness fallback, with the economic half missing.

## What would prove this wrong

The engineering argument above is strong. The **evidence** is thin, and those
are different things.

What exists empirically: one predecessor that ran three tasks in two years, all
its author's own. A Keep3r where 30 of 42 registered jobs were never worked.
And Arcron, where **nobody outside CorvidLabs has registered an upkeep yet**.

That is not evidence the thesis is wrong. BiatecCron had four specific defects
this project chose differently on, so it is confounded, and Arcron has not been
seen by anyone yet, so it is untested rather than failed. But it is thin, and
the cost arithmetic above should not be allowed to paper over it.

**So, falsifiably:** if this is real infrastructure, somebody outside CorvidLabs
registers an upkeep for something they actually wanted scheduled, within a few
months of this being visible. If a year passes and every upkeep is still ours,
the design was fine and the demand was not there — the same ending as
BiatecCron, reached more carefully.

That is the number that settles it. Not keeper count, not throughput, and
nothing Ethereum measures.
