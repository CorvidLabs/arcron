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
fee. The real comparison is against the cost of *owning a bot*:

| one hourly schedule, per month | cost | plus |
|---|---|---|
| **Arcron** | 2.88 ALGO ≈ **$0.26** | nothing |
| cheapest VM | ~$5.00 | you write and maintain the bot |
| GitHub Actions cron | $0.00 | drifts 5–15 min, auto-disabled after 60 days idle |

**About 19x cheaper than the cheapest server, and the bot still has to be
written.**

The asymmetry compounds, which matters more than the ratio. "Write your own
bot" is not one cost, it is a cost *per project*. Ten contracts needing
schedules is ten bots, ten hot wallets and ten things to monitor — or one
scheduler you now maintain as a product you did not set out to build. Ten
Arcron upkeeps are ten box entries.

This is the ordinary case for shared infrastructure, and it was true before
anyone said the word "agent".

## The argument that does need agents, stated at its real strength

x402 and the agent-to-agent payment work give agents a way to **pay**. Nothing
in that stack gives them a way to **wake up**. An ecosystem is about to have a
payment verb and no scheduling verb.

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
