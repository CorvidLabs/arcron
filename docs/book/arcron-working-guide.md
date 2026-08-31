---
title: "Arcron: The Working Guide"
subtitle: "A permissionless keeper network for Algorand, from your first upkeep to running the network"
author: "Compiled from the Arcron project documentation (CorvidLabs)"
date: "2026-08-27 · TestNet app 769891898 (alpha-3) · alpha, unaudited"
lang: en-US
---


# Preface

## What this book is

A smart contract can't wake itself up. Something off-chain has to call it. Arcron
lets anyone register a scheduled contract call with escrowed ALGO, and any keeper
at all can execute it for the fee. No allowlist, no stake, no token, no owner.

This book is one ordered path through everything the project documents: the idea,
the console, hooking up your own contract, running a keeper, what you're
trusting, what it costs, and the reference material. It's compiled from the
project's own docs and source. Where the project states a number, this book keeps
that number and says where it came from, so you can go check it.

Read it front to back if you're new. Dip into it later when you're building.

## The state of the thing, honestly

The project is unusually blunt about how finished it isn't, and this book
inherits that. Four facts frame everything else.

> - **It's alpha, unaudited, and TestNet only.** Use app `769891898` and nothing
>   else. Every other app id is either superseded or a look-alike, and the
>   console quarantines them (Chapter 9). Don't put MainNet value into any of it.
>   Alpha also means this app id can be replaced for any reason. Cancelling is
>   how you leave, and it refunds your escrow and your box deposit.
> - **The contract is upgradeable until its creator freezes it, and it is not
>   frozen today.** Until then the creator can replace the programs and reach
>   every escrow. That's readable on-chain and you can check it yourself
>   (Chapter 9).
> - **Every keeper running is the project's own.** Permissionless is true
>   architecturally and, so far, false in practice.
> - **Nobody outside the project has registered an upkeep yet.** The project has
>   staked its whole thesis on that changing (Chapter 11).

None of that is hidden, and none of it is a reason not to learn the system. It's
the reason to learn it before you rely on it.

## How to read this book

| Part | You are | Read it to |
|---|---|---|
| **I. Understanding Arcron** | anyone | get the idea, the roles, the mechanism |
| **II. Using Arcron as a creator** | scheduling a call | register an upkeep, hook up your contract |
| **III. Running a keeper** | earning fees | stand one up and operate it |
| **IV. What you're trusting** | deciding whether to rely on it | judge the safety and the cost honestly |
| **V. Reference** | building against it | look up signatures, encodings, commands |

Two short paths through it. If you want the idea, read Chapters 1 and 2, about
ten minutes. If you want to *use* it, read the four facts above, then Chapter 2,
then Chapter 4. That's enough to register your first upkeep, and you can come
back for the rest. If you came to break it or to judge it, read Part I and then
Part IV.

## Where each chapter comes from

This book restates the documents below rather than replacing them. If a chapter
matters to you, read its source too. That's the copy that gets corrected first,
and the copy to update when something changes here.

| Chapter | Canonical source |
|---|---|
| 1 · the problem, and the predecessors | [`docs/prior-art.md`](../prior-art.md), [`docs/why.md`](../why.md) |
| 2 · what Arcron is | [`README.md`](../../README.md), [`docs/why.md`](../why.md) |
| 3 · how it works | [`docs/arcron.md`](../arcron.md) |
| 4 · your first upkeep | [`docs/first-upkeep.md`](../first-upkeep.md) |
| 5 · integrating your contract | [`docs/integrating.md`](../integrating.md) |
| 6 · scheduling and fees | [`docs/design/scheduling-and-fees.md`](../design/scheduling-and-fees.md) |
| 7 and 8 · running and operating a keeper | [`docs/hosting.md`](../hosting.md) |
| 9 · security | [`docs/security.md`](../security.md), [`SECURITY.md`](../../SECURITY.md) |
| 10 · economics | [`docs/why.md`](../why.md) |
| 11 · is this the right idea | [`docs/prior-art.md`](../prior-art.md), [`docs/design/out-of-scope.md`](../design/out-of-scope.md) |
| A and B · API and box encoding | `smart_contracts/keeper/contract.py`, [`specs/keeper/keeper.spec.md`](../../specs/keeper/keeper.spec.md) |
| D · deploying and governing | [`docs/deploying.md`](../deploying.md), [`docs/releases.md`](../releases.md) |
| E · design decisions | [`docs/design/1.0.md`](../design/1.0.md) |

[`START-HERE.md`](../../START-HERE.md) is the repository's front door and branches
by what you came to do. This book is the linear read. Where the two differ in
emphasis they should never differ in fact.

## Conventions

Money on-chain is in microALGO (µALGO), and 1 ALGO is 1,000,000 µALGO. The
contract's floor fee is 4,000 µALGO, which is 0.004 ALGO.

Arcron schedules in **rounds**, not seconds. A round is roughly 2.70 to 2.8
seconds. "Rounds are not a clock" is a running theme and Chapter 6 explains why
it matters.

Indented callout boxes flag the things that cost people an hour, or that are
safety-critical. Commands are shown exactly as you'd run them, and anything that
writes to a chain says so.

## A note on the numbers, and what is canonical

**`docs/` is the source of truth and this book is derived from it.** Where the
two ever disagree, the doc wins and this book has a bug.

That ordering matters, because a book is the easiest place in a repository for a
figure to go quietly stale. This one already ran that risk once. The first draft
was written on 2026-08-24, and by 2026-08-27 seven of its load-bearing figures
had been superseded by corrections to [`docs/why.md`](../why.md) and
[`docs/first-upkeep.md`](../first-upkeep.md). It also carried two "the docs
disagree with themselves here" flags that the docs have since settled.

Two things hold that line now. Every figure below was re-derived against the
repository on 2026-08-27, and `tests/test_book.py` pins the load-bearing ones to
the files that own them, so the next drift fails CI instead of waiting for a
reader to notice.

The project's own docs warn: *"Do not trust this page's numbers. Several of them
were wrong last week and were corrected by a review. Recompute anything you
intend to rely on."* That applies here with more force, not less. Treat every
figure as a checkable claim and re-derive anything load-bearing against the live
chain.

The economics in Chapters 2 and 10 all sit on **one basis**, which is the first
thing to check if a number looks wrong: **2.752 s/round measured on MainNet**, so
a nominal-hour upkeep of 1,286 rounds fires every **59.0 minutes**, **732 times
a month**, priced at **ALGO $0.0907**. TestNet runs at 2.695. Two earlier drafts of `docs/why.md` mixed
two bases and produced a multiplier that didn't reproduce from their own tables.


# Part I. Understanding Arcron

## Chapter 1. A contract can't wake itself up

A smart contract is reactive. It runs when a transaction calls it, and never
otherwise. It has no thread, no timer, no `cron`. So if you want a method to run
at 09:00, or every hour, or the moment a prize window closes, something outside
the chain has to send the transaction that calls it.

On Algorand today that something is a bot you write, host, key, and monitor. You
spin up a small server, give it a hot wallet, and have it poll the clock and fire
the call. It works fine. It's also a server, a key, and an on-call rotation for
what should be one line: call this later.

### Algorand has no scheduling primitive

That's the whole claim. It's smaller than "agents need this" and larger than "we
built a bot":

> Every serious chain should let you say *call this later* without running a
> server. Algorand has no way to do that.

There's **no ARC** covering scheduled execution, automation, or keeper
incentives, and there never has been. Not even a submitted draft. So the
ecosystem hand-rolls it. The Foundation's own staking contracts (Réti) carry a
method marked `// Note: ANYONE can call this` with no reward attached, which is
permissionless as a liveness fallback with the economic half missing. The
documented answer to recurring work is still "run your own watcher on a cron."

### The predecessors, and what they teach

Arcron isn't the first to notice this gap. The failures of the ones who tried
are the most useful thing here, because every one of them failed at the same
seam, and it wasn't engineering. It was adoption. Chapter 11 goes deeper; this
is the short version.

**AlgoRhythm** (January 2026) was a draft scheduler pushed by the CTO of
AlgoNode, one of the people best placed in the ecosystem to specify this. Two
commits, same day, never touched again, ending at a TODO whose entries include
*"fee structure (anti-spam + incentives)."* The part it stopped at was the
economics. So the incentives are the unsolved problem, not the plumbing.

**BiatecCron** is a keeper network that actually shipped on Algorand (MainNet app
`1765620242`) and ran three tasks in two years, all of them its author's own.
Shipping isn't adoption.

**Keep3r** on Ethereum is the mature version, and at one snapshot 30 of its 42
registered jobs had never been worked, with active keepers peaking at six. A job
nobody is paid enough to run is a job that doesn't run.

The thread through all three isn't "can you build a keeper network." You can.
It's "can you make the incentives such that strangers actually run it, and
actually use it." That comes back in Chapters 10 and 11.

### Why not just use AWS Lambda?

You can, and plenty of people should. The project says so itself. Lambda plus
EventBridge Scheduler, at the volume one schedule needs, sits deep inside a
perpetual free tier, doesn't drift, and doesn't expire. If you're already on AWS
it defeats this comparison outright. GitHub Actions cron is free too, in a
private repo. Arcron is not competing on raw cost against the free options.

What a scheduler you host yourself can't give you is the thing Part IV builds
toward: a schedule that **outlives the process that created it**, needs **no hot
key of yours**, and needs **no operational attention**. Whether that's worth
anything is the open question the project has staked itself on. You can't judge
it until you understand the mechanism, which is the rest of Part I.

## Chapter 2. What Arcron is

Arcron is the shared version of the watcher-on-a-cron. Instead of everyone
running their own server to call their own contracts, anyone registers a
scheduled call once, with money attached, and any keeper can execute it for the
fee. One process can service the whole registry, so the network needs a handful
of keepers rather than a crowd. Chapter 7 does that arithmetic.

There's **no owner and no protocol rake**. The contract holds escrow for other
people and pays it out to whoever does the work. Nobody takes a cut.

### The three roles

Everything in Arcron is one of three parties. Keep them straight and the rest
follows.

| Role | Does | Cannot |
|---|---|---|
| **Creator** | registers an upkeep, funds it, cancels their own | touch anyone else's upkeep, change a registered call, or stop a keeper executing |
| **Keeper** | executes any due, funded upkeep and collects its fee | choose *what* is called, alter a schedule, or take more than the fee the box says |
| **Target app** | anything it likes with its own state, inside the call | reach the keeper's funds, re-enter Arcron, or change the upkeep that called it |

The single guarantee the whole design rests on: **a keeper decides *when* your
call happens, never *what* it says.** The call and its arguments are fixed by the
creator at registration. That's what makes keepers trustless. It's also why a
keeper can't inject fresh data, which Chapter 11 gets into. Arcron is a clock,
not an oracle.

### An upkeep, in one sentence

An **upkeep** is a standing instruction: *call this app with this exact data
every N rounds, paying R µALGO per execution, out of this escrow.* You register
it once. It runs until the escrow is empty or you cancel it. Cancelling gives you
back everything unspent, plus the storage deposit.

### The idea in one minute

1. A creator **registers** an upkeep and escrows ALGO into the contract.
2. Rounds pass. When the upkeep is **due**, any keeper may call `execute`.
3. `execute` performs the registered inner call to the target app, then pays the
   keeper its fee from the escrow. Both happen atomically, in one transaction, so
   a fee is only ever paid alongside a real execution.
4. The schedule advances. Repeat until the escrow runs low or the creator
   cancels.

That's the entire product. All the depth is in the corners: what happens after an
outage, how the fee behaves when an upkeep gets neglected, what a keeper can and
can't reach, and who can change the rules. Those corners are the rest of the
book.

### The honest cost case

The project has corrected its own numbers here more than once, so this book is
careful. Every row below uses the single basis named in the preface, 732
executions a month at ALGO $0.0907, because mixing two bases is exactly how the
earlier drafts went wrong. [`docs/why.md`](../why.md) is the canonical version of
this table.

| One hourly schedule, per month | Cost | What you give up |
|---|---|---|
| **Arcron at the 4,000 µALGO floor** | ~2.93 ALGO ≈ $0.27 | not nothing, see below |
| **Arcron at the suggested 10,000 µALGO** | ~7.32 ALGO ≈ $0.66 | as above |
| fly.io shared-cpu-1x | ~$2.02 | you write, host, key, and monitor the bot |
| Hetzner CX22 | ~$4.10 | as above |
| AWS Lambda + EventBridge | $0.00 | as above, but genuinely free at this volume |
| Oracle Always Free | $0.00 | free, but Oracle reclaims idle instances |
| GitHub Actions cron (private repo) | $0.00 | delayed under load, runs may be dropped |

So Arcron is **7.6x cheaper than the cheapest *paid* host at the floor fee, and
3.0x cheaper at the fee the console actually suggests**. It is **not cheaper at
all than the free options**. All three of those are true and the project says all
three. What you're paying Arcron for isn't the lowest possible cost. It's the
absence of a hot key, a host, and an on-call rotation, and a schedule that
outlives you.

> **Quote the fee with the multiple, always.** The floor row is the one that
> produces the flattering number, and it isn't the fee anyone should register at:
> at 4,000 µALGO a keeper clears 1,000 and won't reliably run your upkeep. The
> console suggests **10,000 µALGO**, where the multiple is 3.0x rather than 7.6x.
> An earlier draft of `docs/why.md` printed a single multiple for both fees and
> overstated the suggested one by two and a half times. Two independent reviews
> caught it. Chapter 10 has the full reasoning. Recompute against the fee you
> actually set.

### What it doesn't do

Arcron is **the clock, not the eyes**. It schedules on-chain calls. It can't
observe the world, can't fetch off-chain data, and can't let a keeper supply
values. If your automation needs *data*, you pair Arcron with an oracle: the
oracle holds the data, Arcron guarantees the timing. Chapter 5 (Lesson 7) and
Chapter 11 draw that line precisely, because it's exactly where the design says
no on purpose.

## Chapter 3. How it works, end to end

```
 creator                 keeper app (769891898)              target app
   |  register + escrow ALGO  |                                   |
   |------------------------->|  box "u"+id : the Upkeep struct   |
   |                          |                                   |
 keeper bot                   |                                   |
   |  execute(upkeep_id) ---->|  inner app call ----------------->|   (your hook runs)
   |                          |  inner payment (fee) ---> keeper  |
```

**One box per upkeep.** Each upkeep lives in its own box, named `b"u"` followed by
the upkeep's id. The registry is fully on-chain and readable with free algod box
queries, so **no indexer is required**. A keeper is a loop over boxes.

**`execute` is atomic.** The target call and the keeper's payment are inner
transactions of the *same* `execute` call. A fee is only ever paid alongside a
real execution. There's no path that pays a keeper for work it didn't do.

**The contract is passive.** "Arcron is running" always means somebody's bot is
running. There's no on-chain timer anywhere in this picture.

### The lifecycle of an upkeep

An upkeep moves through a handful of states, and learning them now makes every
later chapter easier.

1. **Registered.** The creator sends a transaction group that funds the box's
   storage deposit and the escrow, and names the target, the call data, the
   interval, the fee, and the missed-run policy. The contract stores the `Upkeep`
   struct in a fresh box and returns its id.
2. **Due, or not due.** The upkeep carries a `next_execution_round`. Before that
   round `execute` is refused with "Not due". At or after it, any keeper may go.
3. **Executed.** A keeper calls `execute`. The contract advances the schedule,
   deducts the fee from the escrow, performs the inner call to the target, and
   pays the keeper. It records the round it actually ran in.
4. **Dormant, or starved.** When the escrow falls below one fee, no keeper can
   execute it. It isn't broken. It resumes the instant anyone tops it up.
5. **Cancelled.** The creator calls `cancel`. The box is deleted, its storage
   deposit is released, and that comes back along with the remaining escrow and
   any unspent asset bonus. The id is never reused.

### What's stored, conceptually

Each upkeep box holds one `Upkeep` struct. You don't need the byte layout yet
(Appendix B has it), but you should know the fields, because they're the
vocabulary for the rest of the book.

| Field | What it means |
|---|---|
| `creator` | who may cancel it, and who refunds go to |
| `target_app`, `call_args` | *what* is called, fixed forever at registration |
| `interval_rounds` | the cadence, in rounds |
| `next_execution_round` | when it's next due |
| `fee_per_execution` | the base fee a keeper is paid |
| `balance` | the ALGO escrow remaining |
| `policy` | `CATCH_UP` or `SKIP_AHEAD`, what happens after a missed run |
| `fee_cap` | the most one run may ever pay (the escalation ceiling); 0 turns it off |
| `last_serviced_round` | the round it last ran; escalation is measured from here |
| `fee_asset`, `asset_fee`, `asset_balance` | an optional bonus paid in an ASA, *on top of* the ALGO fee |
| `times_executed` | a running count |

Two of those carry more subtlety than they look, `policy` and `fee_cap`, and
getting them wrong is the difference between an upkeep that survives an outage
and one that burns its whole escrow. Chapter 6 is about them.

### The money, in and out

Worth seeing once, every way ALGO enters and leaves the contract.

It comes **in** as the storage deposit and the escrow at registration, as later
top-ups, and as any ASA bonus escrow. It goes **out** as a keeper's fee on each
execution, or as a refund to the creator on cancel: the remaining escrow plus the
released storage deposit. A forfeited bonus just stays put until someone claims
it or the upkeep is cancelled.

Every ALGO that leaves does so as one of exactly two things, a keeper fee or a
creator refund. There's no third recipient, no owner withdrawal, and no rake. You
can check that invariant by reading every payment the contract is able to emit,
and it's the backbone of Chapter 9.


# Part II. Using Arcron as a creator

## Chapter 4. Your first upkeep

Everything in this chapter reads or writes **TestNet only**. The worst thing that
can happen is you lose a fraction of a TestNet ALGO, and even that comes back:
cancelling an upkeep returns its remaining escrow and its box storage deposit in
full.

It's worth doing by hand. Every *read* path in the console has been driven
against live TestNet, but far fewer *write* paths have been exercised by a real
wallet. `register`, `execute`, `cancel`, and `top_up` all need a signature, and
no automated test can produce one. So this is the cheapest bug-finding available.
For scale: a bug where every disabled button rendered at a 1.02:1 contrast ratio,
literally invisible, survived four agent reviews, an axe-core pass at zero
violations, and 91 unit tests. A human found it in about ninety seconds, because
none of those checks looks at rendered pixels.

### Before you start

> - **Use only app `769891898`.** The console's canonical address is a security
>   property, not a convenience: anyone can deploy a look-alike with the same
>   form. Any other app id is superseded or hostile. The console quarantines it
>   (Chapter 9) and you should too.
> - **This is alpha and unaudited.** The deployment is **not frozen**, so its
>   creator can still replace the programs and reach every escrow (Chapter 9).
>   The app id is alpha and can be replaced. Escrow only what you're willing to
>   have sitting on a throwaway TestNet contract.
> - **Switch your wallet (Pera) to TestNet.** Settings, then Developer Settings,
>   then Node Settings, then TestNet. Skip this and Pera hands the console a
>   MainNet address with no TestNet balance, and the Register button stays
>   disabled with no visible explanation.
> - **Get about 0.2 TestNet ALGO.** Fund your address at
>   <https://bank.testnet.algorand.network/> or the Lora dispenser
>   (<https://lora.algokit.io/testnet/>). Most of it comes back when you cancel.

Open the canonical hosted console and let its own URL pin the network and the app:

<https://corvidlabs.xyz/arcron/console/?network=testnet&app=769891898>

Check that address bar before you connect a wallet. If you'd rather run the
console locally, that's the second path: `cd web && bun install && bun run ng
serve`, then open
`http://localhost:4200/register?network=testnet&app=769891898`.

### The numbers you'll need

| | |
|---|---|
| Keeper app | `769891898` (alpha-3) |
| Target app (pulse) | `769891902` |
| Method signature | `tick()uint64` |
| Selector it produces | `0x4d4d5f0b` |
| Box deposit | 0.0621 ALGO, **refunded in full on cancel** |
| Minimum fee per run | 0.004 ALGO (the floor; the console suggests 0.010) |

`pulse` is a heartbeat counter that exists to be called. It has no state worth
protecting and it can't fail, which is what makes it the right first target.

### Lesson 1. Test the call before you connect anything

Fill in **TARGET APP ID** `769891902` and **METHOD SIGNATURE** `tick()uint64`.
The selector `0x4d4d5f0b` should appear as you type. Press **Test the call**.

This needs no wallet and costs nothing. It simulates the inner call Arcron will
make, with the sender set to the keeper application's own account, which has no
private key for anyone to hold. Checking the call before you expose a wallet to
the page is the right order to do things in.

Expect a graded result, never a flat pass. A "reference" here is an extra
account, asset, or app that the inner call has to name. Arcron already spends two
of the eight available slots (the upkeep box and your target), so six are left
for a keeper to fill. For `pulse.tick()` the grade should read `RESOURCES: NONE`,
meaning the call reached for nothing a keeper has to name. A `servable` grade
means it needs up to six references and a keeper can discover them (Chapter 5,
Lesson 6, explains how). It'll also tell you what it *can't* know: whether a
keeper will turn up, and whether the call's needs will grow later.

> **If it grades anything other than `NONE` or `servable`, stop.** A target that
> needs more than six references is permanently unexecutable once you've
> escrowed. The grades exist to catch that while your money is still yours.

### Lesson 2. Fill in the rest

| Field | Value | Why |
|---|---|---|
| INTERVAL (ROUNDS) | `215` | about 10 minutes at ~2.70 s/round |
| FEE PER EXECUTION | `0.010` | what the console suggests. Keepers spend ~0.003 in group fees, so this leaves them ~0.007. The 0.004 minimum leaves only ~0.001, which can't fund a machine. |
| FEE CEILING | `0` | off. Only raise it if an upkeep is actually going unserviced. |
| FUNDING | `0.03` | three runs at the suggested fee |
| IF A RUN IS MISSED | **Skip ahead** | see the box below |

> **Leave it on Skip ahead.** Catch up replays every missed interval at one fee
> each, and how many replays you get depends on how long nobody came, not on what
> you escrowed. Upkeep 18 on this same deployment ran Catch up into a real
> outage. It burned its whole escrow on 17 replays, advanced 41 rounds against a
> 23,478 round backlog, and starved. Money gone, schedule still broken. On a
> short cadence, catch-up after a real outage can't catch up. Chapter 6 covers
> when Catch up is the right call anyway.

### Lesson 3. Read the cost before you sign

Check the **UP-FRONT COST** tile. With 0.03 funding it should read **0.0951 ALGO**:

| | | |
|---|---|---|
| Box deposit | 0.0621 | returned in full when you cancel |
| Escrow | 0.0300 | spent one execution at a time; the remainder returns on cancel |
| Network fees | 0.0030 | three transactions, gone either way, even if the group fails |

The console sets escrow equal to your funding, so 0.03 funding gives
0.0621 + 0.0300 + 0.0030 = **0.0951**. Recompute that sum from the funding row
rather than trusting the total. [`docs/first-upkeep.md`](../first-upkeep.md) has
had this arithmetic wrong twice: once at 0.0771, and again at 0.0851 when the
suggested fee moved to 0.010 and the funding row to 0.03 but the total didn't
follow. Both times the console was right and the page was wrong.

> **Compare the tile against what your wallet actually asks you to approve.** The
> console figure was genuinely wrong once, reading 0.0741 against a real 0.0771
> debit, and this comparison is what caught it. If the tile and the wallet
> disagree, that's a bug and it's worth more than the upkeep. Report it.

### Lesson 4. Attest, connect, register

Tick **"I have tested this call against my own app and accept the risk."** That
box records human judgement, and it's deliberately not satisfied by the Test
button having passed. Arcron can't know whether calling this method on a schedule
is what you actually want.

In the CONNECT row, click your wallet and approve. Pera is what's used here, but
Defly, Lute, Exodus, and Kibisis all work the same way, and on LocalNet the
console signs through KMD with no extension at all. Watch that the console reads
your balance, and note that it distinguishes *unread* from *zero*. Then press
**Register upkeep** and approve.

You should land on `/u/<id>`, the upkeep's own page, rather than a confirmation
panel. That page shows what it calls, its cadence, its next run, its escrow, its
runway, and a plain sentence about what happens when the escrow runs out.

> **When will it actually run?** The project's only live keeper polls about every
> 30 minutes, so `RUNS` can sit at `0` for that long even though everything
> worked. That's a keeper cadence, not a failure, and losing a race to another
> keeper is free and normal too. If you don't want to wait, run the first
> execution yourself from the upkeep page.

### Lesson 5. Clean up

On the upkeep's page, press **Cancel**. It refunds the remaining escrow plus the
full 0.0621 box deposit to the account that registered, and cancelling is
creator-only. Leaving it running is fine too, and mildly useful: it's one more
upkeep on the network's uptime clock.

### If something goes wrong

**Register stays disabled.** You need both the attestation and a connected
account. The hint beside the button says which one is missing.

**Pera shows a MainNet account.** It's still on MainNet. Switch the node setting
and reconnect.

**"This is not the Arcron deployment."** The app id in the URL isn't
`769891898`. That panel is deliberate: anyone can deploy a contract with this ABI
and box layout, so a look-alike shows the same registry and accepts the same
form. Every money button stays disabled until you explicitly continue, and the id
isn't remembered. Chapter 9 explains the quarantine.

**An execution fails.** Losing a race to another keeper costs nothing, and the
chain rejects a failing transaction at validation rather than including it.
That's ordinary, not an error.

## Chapter 5. Hooking up your own contract

Integration is one method. This chapter is everything else you need around it, in
one pass. `examples/minimal_target.py` is a complete, compiling version of all of
it, and a test in the repo compiles that file on every run, so it can't rot.

### Lesson 1. The hook

Expose one NoOp ABI method that takes no arguments of its own:

```python
@abimethod()
def run(self) -> UInt64:
    ...
```

Arcron calls it with the method selector as the only application argument. That's
the simplest shape and the common one: `tick()`, `publish()`, `distribute()`, and
`sweep()` in this repo are all built that way. A method taking arguments works
too, since the creator fixes the whole argument list at registration, but start
here.

The design consequence is that **your hook works from your own state**. It isn't
handed parameters, so whatever it needs in order to decide has to already be
on-chain when it runs. That's a healthy constraint in practice. It means anyone
can verify what the scheduled call will do before it happens.

### Lesson 2. Authorization

Two choices, and most integrations should take the first.

**Restrict to the keeper app.** Arcron's inner call comes from the keeper
application's account, so that's the sender to check:

```python
assert Txn.sender == Application(self.keeper_app.value).address, "Only the keeper app"
```

Derive the address off-chain with
`algosdk.logic.get_application_address(769891898)`.

**Or leave it permissionless.** That's right when the hook is idempotent and its
timing is the only thing that matters. Your contract then still works if Arcron
disappears, and anybody can push it along. It's the wrong default when the hook's
effects depend on *when* it runs.

> **What restricting doesn't buy you.** The keeper app is permissionless, so
> "only the keeper app" means "only via a paid upkeep". It doesn't mean "only by
> someone I trust". Anyone can point their own upkeep at your hook on the
> shortest interval and pay the fees themselves. Read Lesson 4 before you lean on
> this check for anything that counts.

### Lesson 3. Make it durable

This is the part integrations get wrong. Four rules, each learned the hard way.

**Your hook is called whether or not there's work.** Arcron calls on every
cadence, forever. Make the no-op path cheap, and make it *return* rather than
fail:

```python
if self.pending.value == 0:
    return UInt64(0)   # right
    # assert False     # wrong, see below
```

**A hook that fails stops being serviced.** When a target rejects, the keeper bot
backs that upkeep off exponentially: 1, then 2, then 4, up to 8 of the upkeep's
own intervals, capped near an hour. That state survives restarts. Failing costs
the keeper nothing, because Algorand rejects the transaction before it reaches a
block, but it costs *you* the schedule. Fail soft. Record the problem in state
and return.

**You have more opcode budget than you think.** Budget pools across the app calls
in a group, and an Arcron execution contains two: Arcron's own call and the inner
call to you. Measured, a method called directly gets about 684 opcodes at entry
and the same method called through Arcron gets about 1,135, roughly 1.66x,
because it inherits the pool Arcron's call contributed to.

**Assume it may run more than once, in bursts.** After an outage `CATCH_UP`
replays missed periods, so your hook can be called several times in quick
succession. Make it idempotent, or make each call's effect depend only on current
state.

### Lesson 4. Authorization is not authorization of cadence

This is the subtlest trap in the chapter, so it gets its own lesson. The check
everyone reaches for:

```python
assert Txn.sender == Application(self.keeper_app.value).address, "Only the keeper app"
```

proves the keeper application called you. It does **not** prove your registered
interval has elapsed, because registering an upkeep is permissionless. Anyone can
point their own upkeep at your hook, on the shortest interval Arcron allows, and
pay the fees.

That's harmless for a hook whose effect depends only on current state, which is
most of them. It's not harmless for a hook that counts, meters, or accrues. A
billing hook that advances a period on every call can be fast-forwarded by
anybody willing to spend two minimum fees a call, and whoever benefits from the
count has every reason to do it.

If your hook counts, enforce the interval yourself, and **return, don't assert**:

```python
if Global.round < self.last_run.value + self.min_rounds.value:
    return self.count.value      # too soon: nothing to do, and no rejection
self.count.value += 1
self.last_run.value = Global.round
```

> **Why return and not assert?** Under `CATCH_UP` a backlogged upkeep calls again
> in the same round. An assert rejects that call, which fails the whole
> `execute`, which the keeper records as a failure and backs the upkeep off,
> until the schedule stops entirely. That's the exact outcome the never-fail rule
> exists to prevent, reached through the code that was supposed to protect you.
> Returning refuses the *work* without refusing the *call*: the griefer still
> pays the fee and still moves nothing.
> `smart_contracts/subscription/` is the worked example, and it had both bugs in
> turn.

### Lesson 5. The pull pattern

If you take one thing from this chapter, take this one.

> **Do the accounting in the scheduled call. Let counterparties collect in their
> own transactions.**

```
scheduled_hook()   snapshot state, credit allocations, emit an event.
                   Move nothing. Call nothing.
claim()            the counterparty sends this themselves, and is therefore
                   always an available resource.
```

It isn't a style preference. There are two concrete reasons.

The first is **resource availability**. An Arcron inner call reaches only what the
keeper's transaction makes available, and nothing tells a keeper what your hook
needs. A scheduled call that tries to pay an arbitrary account, read a balance,
or call another app can fail because those resources aren't available to it.
There's a mechanism for supplying them (Lesson 6), but pull sidesteps the
question entirely.

The second is **failure isolation**. A push payout to a closed or hostile account
fails the *whole* execution, which wedges the schedule for everyone your contract
serves. Pull confines that risk to the one claimant.

`smart_contracts/rain/` (a hub of scheduled drops) and
`smart_contracts/subscription/` (a metered service) are both shaped by this.

### Lesson 6. Reaching resources your hook can't name

A scheduled call can only touch what the executing transaction makes available,
and Arcron stores no foreign arrays. It turns out it doesn't need to.

Resource references attached to the *keeper's* `execute` transaction flow two
levels down, to Arcron's inner call and to your own inner transactions from it.
And a keeper doesn't have to be told which ones you need, because simulation
reports the resources a call would have required. So a keeper simulates first,
attaches whatever the simulation names, and then sends.

> **One nuance that'll bite you if you skip it.** "The keeper fills in the
> references for you" is only true of a keeper that simulates and names them
> itself. The reference bot (`scripts/keeper_bot.py`) and the console's client
> (`js/src/keeper-txns.ts`) both do exactly that, covering up to six references.
> But `algokit-utils`' *default* resource populator caps at four direct account
> references and refuses a fifth. So don't assume a keeper you didn't write will
> fill the last two. A hook that touches five accounts, tested with a naive
> algokit client, fails with `unavailable`.
>
> The practical rule: **size a hook at four references or fewer if you want any
> keeper to serve it, and at five or six only if you accept that some won't.**
> Still write the hook to reach for what it needs, and still prefer pull.

Two real ceilings to respect. **Six references is the limit**, and a hook needing
more than six can't be serviced by anyone, which is exactly what the pull pattern
exists to sidestep. And **simulation sees the state at simulation time**, so a
hook whose resource needs depend on state that changes between the simulate and
the send can still be mis-served. Keep what a scheduled hook touches predictable.

### Lesson 7. Calls with arguments

An execution carries up to three app args, counting the selector, which is enough
for an ARC-4 method of arity two:

```python
@abimethod()
def settle(self, market_id: UInt64) -> UInt64: ...
```

For anything wider, declare the arguments as a single struct or tuple. That's the
trick ARC-4 itself uses at arg 15, and it makes any arity reachable.

**Every argument is fixed at registration.** If your hook needs a value that
changes between runs, it has to derive it from its own state, from a resource it
pulls, or from the round. Arcron won't supply it, by design. That's the clock,
not eyes boundary, and Chapter 11 draws it in full.

### Lesson 8. An ASA bonus

An upkeep can pay a bonus in any asset **on top of** its ALGO fee, never instead
of it:

```
register(..., fee_asset=<asset id>, asset_fee=<base units>)
opt_in_asset(mbr_payment, upkeep_id, asset)   # 0.1 ALGO, permanent
top_up_asset(upkeep_id, asset_funding)
```

Can you pay keepers *only* in your token? In effect, yes. Set the ALGO fee at the
0.004 floor and it stops being a reward and becomes a cost reimbursement: at the
floor an asset upkeep hands the keeper back exactly the ~0.004 ALGO it spent, and
your token is the entire pay. What you can't do is remove the ALGO altogether,
and that's Algorand's constraint rather than Arcron's, since every transaction
costs ALGO and no contract can price your token without an oracle.

Three things to know. An asset upkeep at the floor **only attracts keepers who
want your asset**, because they break exactly even in ALGO, so pay more ALGO if
you want generic keepers too. The app must **opt in** before it can hold the
asset, which costs 0.1 ALGO of minimum balance permanently and is not refundable,
since there's no opt-out. And a keeper that is **not** opted in still executes,
takes the ALGO fee, and forfeits the bonus, which stays in your escrow and comes
back on cancel.

That last one was verified on TestNet with a fresh, never-opted-in account rather
than in mocks: upkeep 74 on app `769891898`, bonus asset `769987591`. The unopted
execution (`ANSUPUK6VSXZ72IVP76ZDICGJ7NVVVV7BBKLNF25S3ZSFRDTTMWQ`) shows two
inner transactions and an untouched bonus. An opted-in execution moments earlier
(`QQXW5G2OEJS5FXMA7M73YAEQFBOTR2RB3A7WUWAHVQ4YT6FTTYNA`) shows the third transfer
and the escrow falling by exactly the bonus.

### Lesson 9. The four things that'll cost you an hour

None of these is Arcron's doing. All four are the toolchain, and all four look
like your contract is wrong when it isn't.

**Returning a computed value trips mypy before Puya sees it.** An ARC-4 field's
`.native` is a `UInt64` at runtime but `Any` to mypy. Don't wrap it in
`UInt64(...)`, which Puya rejects outright. Bind through a typed local instead:
`balance: UInt64 = box.value.balance.native; return balance`.

**A zero-argument method has no generated `Args` class.** Call it with no `args`
at all.

**A create method with arguments is reached through `create` twice:**
`app, _ = factory.send.create.create(args=CreateArgs(...))`.

**An inner payment with `fee=0` is paid for by the caller.** That's the right way
to write it, since it keeps the contract from spending its own balance on fees,
but the calling transaction then has to cover both. Pass
`extra_fee=AlgoAmount(micro_algo=1_000)`.

### Lesson 10. Test it, then test it again

> **Unit tests won't catch the things that break integrations.**
> `algorand-python-testing` mocks *record* inner app calls without executing
> them, and they don't enforce minimum balances. So a hook that fails when
> actually invoked, or an app that can't pay what it owes, passes its unit tests
> and fails on a real chain. Anything that depends on either belongs in a
> LocalNet end-to-end test.

```bash
algokit localnet start
fledge lanes run local        # the whole suite, including the worked demos
```

### Lesson 11. Close the loop and get something to actually execute you

Writing a hook is half the job. You haven't integrated until a keeper has really
called it. Two stages.

**On LocalNet**, make your own deployment and then drive it:

```bash
algokit localnet start
fledge run deploy-localnet     # deploys the keeper (add --with-pulse for the demo target)
# ... register an upkeep against your target ...
poetry run python -m scripts.keeper_bot --once --network localnet --app-id <app>
```

> **LocalNet is silent until you move the chain.** LocalNet runs in dev mode: a
> block is produced only when a transaction is sent, so rounds don't advance on a
> timer and an upkeep looks "not due" forever if nothing else is happening. Send
> transactions, or use the scripts' `network.wait_for_round` helper, to push
> rounds along. Anyone who deploys on LocalNet, starts a bot and waits will
> correctly see nothing due. This is the surprise that gets everyone once.

**On TestNet**, register against the live keeper `769891898`:

```bash
cp .env.testnet.template .env.testnet          # add a funded throwaway DEPLOYER_MNEMONIC
poetry run python -m examples.register_upkeep
```

Then either wait for the project's keeper, on its roughly 30-minute cron, or run
your own against it (Part III).

## Chapter 6. Scheduling and fees

Two fields decide whether an upkeep survives contact with the real world:
`policy` and `fee_cap`. They were designed together, so this chapter takes them
together.

### Rounds are not a clock

A cadence is a round count, and a round is about 2.8 s nominally, 2.752 s
measured on MainNet (TestNet: 2.695 s). So "daily" means "every ~30,857
rounds", and the gap compounds.

| Cadence | Rounds | At 2.8 s | At measured 2.752 s | Drift/cycle |
|---|---|---|---|---|
| hourly | 1,286 | 1.0 h | ~0.98 h (59 min) | ~1 min |
| daily | 30,857 | 24.0 h | 23.6 h | ~25 min |
| weekly | 216,000 | 168.0 h | 165.1 h | ~2.9 h |

A "daily" upkeep slides about **12 hours** against the calendar over thirty
cycles, and which way it slides depends on how busy the network is. Even the
hourly row drifts: at the measured rate it fires every 59.0 minutes, so a nominal
month is **732 executions, not 720**. That's why every cost figure in this book
is computed at 732 (Chapter 10).

> **Arcron promises "not before this round". It never promises "at 09:00".** If a
> wall-clock moment matters, have the *hook* check the time and no-op when it's
> early, and schedule it often enough to catch the window. And don't set a
> cadence so tight that ordinary keeper lateness looks like a real condition. The
> minimum interval is 10 rounds, and the demos use a 30-round floor for anything
> that treats lateness as a signal.

### Lesson 1. CATCH_UP or SKIP_AHEAD

`policy` is a required argument to `register`. There's no safe default, and the
danger is that `CATCH_UP` is encoded as `0`, which is the value a caller passes
without deciding anything. So decide.

**`CATCH_UP` (0)** keeps the upkeep due for every missed interval after an
outage, catching up one interval per call. That's right for work where every
period genuinely owes something: a metering hook that bills per interval, a
distribution that mustn't skip a recipient.

**`SKIP_AHEAD` (1)** runs once and advances to the first slot still ahead,
keeping the schedule's phase and dropping the backlog. That's right for
everything whose value is "it happened recently" rather than "it happened every
time".

> **The measured cost of getting this wrong.** Upkeep 18 on `769891898` ran
> `CATCH_UP` into a real outage. It spent its entire escrow on 17 replays and
> advanced **41 rounds against a 23,478 round backlog**, then starved. Money
> gone, schedule still broken. On a short cadence, catch-up after any real outage
> can't catch up. The console pre-selects `SKIP_AHEAD` for exactly this reason,
> and anyone integrating directly should make the same choice on purpose rather
> than inherit it from a zero.

### Lesson 2. Fee escalation and the ceiling

`fee_cap` is the most one run will ever pay. Zero disables escalation entirely
and the fee is always `fee_per_execution`. With a ceiling set, a neglected
upkeep's fee rises linearly from the base to the cap across one missed interval,
then holds there. The effective fee is computed entirely from box state and paid
to whoever executes.

Escalation exists to clear a market: when an upkeep goes unserviced, a rising fee
recruits a keeper. Two consequences follow and both matter.

With several keepers competing, one takes the work early at a lower price and the
ceiling is rarely reached. With one keeper, which is Arcron's situation day to
day, the ceiling *is* the price and the cadence is roughly half what you asked
for, because a lone keeper is better off waiting for the fee to peak. (Two
keepers have raced for one due upkeep on this deployment and collided as
designed, the loser paying nothing. That was a deliberate exercise, not the daily
state.)

> **Leave the ceiling at zero unless an upkeep is genuinely going unserviced.** It
> buys reliability from a competitive keeper set and buys nothing from a single
> one. The project lists this as a known and accepted risk.

One subtlety the contract handles for you, worth understanding anyway. A ceiling
does **not** raise the balance at which an upkeep goes dormant. When the
escalated fee is more than the escrow holds, the fee falls back to the base so
the upkeep stays executable by anyone, rather than stranding a ceiling's worth of
escrow nobody can spend. Lateness only ever grows, so without that fallback an
escrow that once fell below the escalated fee could never reach it again. Budget
your runway against the ceiling anyway, since a late run can consume that much.

### Lesson 3. Escalation can't be farmed

The natural worry: could a lone keeper deliberately let an upkeep fall behind and
collect the ceiling over and over? No, and the reason is the cleverest piece of
economics in the contract.

Lateness is measured from `last_serviced_round`, not from the schedule, and that
field jumps to "now" on every run. So a replay of a backlog never escalates. Once
an upkeep is behind, `next_execution_round` is at or before `last_serviced_round`,
which the contract reads as "draining a backlog, pay base". A keeper collects the
ceiling once per genuine fall-behind, then base for every replay behind it.

> Measured before this guard existed: a patient keeper took **100% of a 400,000
> µALGO escrow across 34 runs**, 33 of them at the ceiling. With the guard, only
> the first run escalates. A dedicated test pins it and it's been re-proven on a
> real chain.

### Lesson 4. Funding and runway

An upkeep is funded escrow and executions are paid out of it:

```
funded runs = balance / fee_per_execution     # with no ceiling
funded runs = balance / fee_cap               # with one, budget against this
```

At the 4,000 µALGO minimum fee, 1 ALGO buys 250 executions, which is about 10
days of an hourly cadence or 8 months of a daily one.

| Escrow | Executions | Hourly | Daily |
|---|---|---|---|
| 0.1 ALGO | 25 | ~1 day | ~25 days |
| 1 ALGO | 250 | ~10 days | ~8 months |
| 100 ALGO | 25,000 | ~2.8 years | ~68 years |

Registering also costs the box deposit, 2,500 + 400 × (139 + len(encoded
call_args)) µALGO, so 62,100 µALGO for a bare 4-byte selector. That comes back in
full on cancel, so registering and cancelling costs only transaction fees in the
end.

Four operational facts worth internalising.

**Anyone can `top_up`.** Funding an upkeep that already exists is permissionless,
so a counterparty who wants your schedule to keep running can pay for it. Only
the creator can `cancel`.

**Running dry is silent.** The upkeep goes dormant and resumes the instant
someone tops it up. Nothing announces it.

**A top-up doesn't reset lateness.** Funding a long-dormant upkeep is charged the
ceiling on the very next run, because lateness is measured from the last
*service* and a top-up isn't one. Resetting it would let any creator cancel
escalation for one µALGO.

**Cancel when you're done.** A one-shot task that already ran keeps being called,
and keeps paying keepers to do nothing, until you cancel it.

To get warned before an escrow runs out, grep the health check rather than
relying on its exit code (Chapter 8 explains why):

```bash
poetry run python -m scripts.keeper_bot --check --network testnet \
  --app-id <app> | grep -q starved && echo "top up something"
```


# Part III. Running a keeper

## Chapter 7. Where to run a keeper

> **Read this first.** Every keeper running today is the project's own, and no
> stranger has registered an upkeep yet. At the current registry size **a
> keeper's fees do not fund a host.** Run one to learn the mechanics, and to be
> the first independent keeper. Don't run one because it pays for itself,
> because right now it doesn't. Chapter 10 has the arithmetic for when it would.

### A keeper is a plain process

A keeper watches rounds and calls `execute` on due upkeeps. It holds a hot key,
it needs to be up, and it earns fees. Nothing about it is special to any one
deployment: the network is permissionless, so these are the options for anybody.
The reference implementation is `scripts/keeper_bot.py`, one Python process that
services an entire registry.

The requirement is more forgiving than it looks. Upkeeps run on cadences of
hours, and a neglected upkeep's fee escalates toward its cap, so a keeper
checking every fifteen minutes services a six-hour upkeep perfectly well. Latency
only starts to matter when keepers compete for the same upkeep, which the live
network has done on purpose to prove it works, but not yet day to day.

### How many keepers this needs: two or three, not a crowd

This surprises people, so here's the arithmetic. One keeper is a loop over boxes
and it doesn't shard. Ten thousand upkeeps on an hourly cadence average 7.8 due
per round, which is one machine's work. Keeper count is a liveness question, as
in "is anyone watching", not a throughput one. The escalating fee exists to
recruit the second keeper when the first one stops, not to run an auction.

That is what a machine can compute, and `scripts/keeper_bot.py` does not
collect it. It sends `execute` inside its loop over due upkeeps and waits for
confirmation before the next one, so it serves about one upkeep per round.
Measured on TestNet on 2026-08-29: upkeeps 93 and 94 came due three rounds
apart and executed at rounds 66795899 and 66795901, three serves across three
distinct rounds, collisions 0. Algorand takes 16 transactions in a group and
the bot batches none of them. Anyone who needs the 7.8 has to write that
batching; the arithmetic says the work fits on one box, not that this loop
already does it.

That's a direct lesson from prior art. Keep3r on Ethereum, at peak, had six
distinct active keepers across the entire network. Designing for a large
competitive keeper market is designing for something that has never existed. Two
or three independent keepers is the target and it's enough.

### The options

| Where | Cost | Uptime | Key lives | Effort |
|---|---|---|---|---|
| **A server you already run** (recommended) | nothing extra | continuous | on your box | one script |
| GitHub Actions cron | free to ~$115/mo by cadence | best-effort | repo secrets | uncomment a line |
| A small always-on host | ~$2 to $5/mo | continuous | on that host | a container |
| A laptop | nothing | poor | on your laptop | one plist |

**A server you already run.** If you have a VPS doing anything else, put the
keeper on it. It's a small Python process and it won't notice. The repo ships a
packager that builds a ~392 KB tarball carrying no secrets, so the mnemonic never
rides over the wire in a file: you type it on the host, into a file the installer
creates `640 root:keeper`.

```bash
./deploy/vps/package.sh
scp /tmp/arcron-keeper.tar.gz <user>@<host>:/tmp/
ssh <user>@<host> 'sudo mkdir -p /tmp/arcron-install \
    && sudo tar -xzf /tmp/arcron-keeper.tar.gz -C /tmp/arcron-install \
    && sudo bash /tmp/arcron-install/deploy/vps/install.sh'
sudo -e /etc/arcron/keeper.env     # add KEEPER_MNEMONIC=
sudo systemctl start keeper-bot
```

**A container** (`deploy/Dockerfile`, `deploy/compose.yaml`) is what to reach for
if you need minute-level polling. `restart: unless-stopped` covers reboots, and
`docker compose down` sends SIGTERM, which finishes the scan in flight so a
redeploy never abandons a half-signed execution.

**GitHub Actions** (`.github/workflows/keeper-bot.yml`) runs `--once` on a
schedule. It's a stopgap, not the end state, and there are four things to watch.
Cron granularity is about 5 minutes and best-effort, so GitHub delays runs and
may drop them. Each run is a fresh process with no disk, so backoff state doesn't
persist and a persistently failing upkeep gets retried every run (Chapter 8). The
mnemonic lives in repository secrets. And a scheduled workflow in a **public**
repository is disabled after 60 days without activity, though a private
repository, which is where you'd run this since it's billed per minute there,
isn't auto-disabled. ([`docs/hosting.md`](../hosting.md) still states that rule
unconditionally; [`docs/why.md`](../why.md) carries the correction.)

The cost is the thing to check first, because it's billed per minute on a private
repo and a keeper runs constantly.

| Cadence | Runs/month | Minutes | Cost beyond the 3,000-min allowance |
|---|---|---|---|
| every 5 min | 8,640 | ~17,300 | **~$115/month** |
| every 15 min | 2,880 | ~5,800 | ~$22/month |
| every 30 min | 1,440 | ~2,900 | free (consumes the whole allowance) |

> **Match your keeper's cadence to the upkeeps it serves.** A half-hourly keeper
> suits work measured in hours. Point it at a ten-minute upkeep and it's three
> intervals late, and with `CATCH_UP` it replays every missed interval at one fee
> each. If you need minutes, run something that polls in minutes.

### The second keeper, and why it isn't just a backup

There **was** a second keeper workflow beside `keeper-bot.yml`, on the *same*
cron as the first, deleted on 2026-08-31. It existed to make Arcron's economic claim
happen on a real chain: that competition holds the fee below the ceiling, and
that losing a race costs nothing. Neither has been observed, and this did not
change that — `KEEPER_2_MNEMONIC` was never set, so for four days the job ran
on schedule, skipped itself in twenty seconds, and exited green. The Actions
history looked like two keepers. One was running.

> **An offset schedule doesn't race, it queues.** Two keepers thirty minutes
> apart never contend. The first takes every due upkeep and the second arrives to
> an empty registry. That looks like redundancy and is a queue.

That is why both workflows passed `--align 120`, holding the first scan until
the next whole two-minute mark in UTC. Runner clocks are NTP-synced, so an
absolute instant is the one thing two machines that have never met can agree
on. `--align` survives the deletion and `scripts/keeper_race.py` still proves
the collision on demand, with two real bots on LocalNet.

### What the account needs

A keeper pays 3,000 µALGO per execution and collects the upkeep's fee, so it's
profitable as long as fees exceed costs. It refuses to start below 103,000
µALGO: 100,000 to keep the account, plus one execution. Use an account that holds
no more than it needs. It's a hot key on an unattended machine whose whole job is
to spend small amounts constantly.

## Chapter 8. Operating the bot

### Before the first run

The bot needs three things set, and missing any of them is the usual reason a
first run does nothing.

> - **Python 3.13**, never 3.14, because a dependency has no wheels for it. Use
>   the Poetry venv (`poetry install`).
> - **A network and its config.** Pick the chain with `--network` or
>   `ARCRON_NETWORK`. It loads `.env.<network>` and checks the node's genesis id.
> - **A signing key and the app to service.** `KEEPER_MNEMONIC`, or
>   `DEPLOYER_MNEMONIC` as a fallback, signs and pays the fees. **The app id is
>   required**: pass `--app-id <id>` or set `KEEPER_APP_ID`. There's no built-in
>   default deployment.

### The commands

```bash
poetry run python -m scripts.keeper_bot --network testnet --app-id 769891898            # loop
poetry run python -m scripts.keeper_bot --network testnet --app-id 769891898 --once     # single scan (cron)
poetry run python -m scripts.keeper_bot --check --network testnet --app-id 769891898     # probe, signs nothing
```

### Reading the logs

`--log-format json`, which is the container default, emits one object per line.
The line you'll care about months later is `executed`:

```json
{"event": "executed", "round": 66629379, "upkeep_id": 9, "target_app": 1043,
 "fee_collected": 4000, "escrow_remaining": 8000, "next_due_round": 66629389,
 "tx_id": "F724IJ7A...UC6A"}
```

It carries the round, the fee collected, the escrow left, and the transaction id,
so any claim in it can be checked against the chain. Use `--log-format text` for
a human at a terminal.

### Health checks from outside

`--check` reads the registry and exits without signing anything, so it works as
an external probe. You don't need the keeper's account or its cooperation. It
draws one distinction that matters, and the exit code encodes it.

**Stalled** means an upkeep is funded, due, and nobody came. That's a keeper
problem, and `--check` exits 1.

**Starved** means an upkeep's escrow has fallen below one fee, so no keeper
*can* execute it. That's the creator's problem rather than a keeper's, so
`--check` exits 0.

> **Don't page on `--check`'s exit code for starvation.** Blaming keepers for a
> starved upkeep would make the signal useless, so a starved upkeep exits zero.
> To get alerted when an escrow runs dry, grep the output instead:
> `... --check ... | grep -q starved && echo "top up something"`.

### Knowing it's still alive

A keeper fails silently in two ways, and both take the network down with it.

**It dies.** Nothing on-chain says so, and upkeeps just quietly pile up as due.
Every twenty scans, and on every `--once` run, the bot emits a `heartbeat`. Alert
on its absence, not its content. A heartbeat that stops is the signal.

**It runs out of ALGO.** This one is nastier, because a keeper earns fees into
the same account it spends from: self-sustaining while the registry is busy, and
stuck the moment it's empty, with no way to earn its way back out. So the balance
is checked before the first scan and at every heartbeat. Below 103,000 µALGO it
refuses to start and exits `2`. Below `--min-balance`, which defaults to about
100 executions of headroom, it warns each heartbeat and keeps working.

### Races, and why losing is free

Multiple competing bots are safe. The contract re-checks due-ness atomically, so
exactly one keeper is paid per due round, and **the loser pays nothing**.
Algorand rejects a failing transaction at validation rather than committing it,
so it never enters a block and no fee is charged. That's unlike an EVM revert,
which still burns gas.

That isn't inferred, it's measured three ways: a losing `execute` broadcast
straight to algod with its balance unchanged, a race between two real bots on a
shared barrier where the loser's transaction is absent from any indexer and its
balance moved by exactly zero, and the ordinary in-pool race. A race leaves
almost no trace, since only the winner's transaction exists, so the losing
keeper's own log is the record. It's written to carry everything an outsider
needs to check it against the chain:

```json
{"event": "race_lost", "round": 66703234, "upkeep_id": 75,
 "winner": "NUGVPQGZ...QMBVU", "won_at_round": 66703238,
 "fee_forgone": 4000, "spent": 0, "registry_advanced": true,
 "tx_id": "KXTAGVSRJAYXTUGRGA5VY73SLRRH2YGUKIB7YIFOEUBWM4P7XDXQ"}
```

`spent: 0` is the whole argument for running a keeper. Losing costs nothing. To
produce a race on purpose rather than waiting for the schedule:

```bash
poetry run python -m scripts.keeper_race --network testnet \
    --app-id 769891898 --target-app 769891902
```

It registers a fast upkeep, starts two real bots against the same barrier, checks
the outcome against chain data, and exits non-zero if the two keepers didn't
actually collide. A run where they politely took turns proves nothing and
shouldn't read as a pass.

### Backoff: a failing target versus a lost race

The bot separates these two, because they mean opposite things.

**A failing upkeep**, meaning a target that rejects the call, backs off
exponentially. The wait doubles in the upkeep's own intervals up to 8x, capped
near an hour (1,286 rounds) in absolute terms. That state survives restarts, so a
`--once` cron doesn't re-attempt a doomed upkeep every run, and a success resets
it to zero. Once you've fixed a target, `--retry-now <id>` clears one upkeep's
backoff and `--clear-backoff` clears them all.

**A lost race never backs off.** Another keeper getting there first is the
common, healthy, free case, and a keeper that stopped trying everything it lost a
race for would service less and less of the registry.

> **The GitHub Actions exception.** Backoff persistence lives in a state file,
> under `XDG_STATE_HOME`, or wherever `--state-file` says, or nowhere at all with
> `--no-state`. A GitHub Actions run is a fresh process with no persistent disk,
> so backoff doesn't carry between runs there and a doomed upkeep is re-attempted
> on every scheduled run. That's one more reason the cron keeper is a stopgap.

The two signals that separate a failing target from a lost race aren't equally
trustworthy. The error text arrives first, but a target has some say in it,
because algod disassembles the failing program into the message. The registry
itself is the honest signal: if the upkeep's box moved on between the scan that
picked it and the call that failed, somebody executed it, and nothing a target
writes can fake that.

### Announcing what happened

A network whose work is invisible looks dead even when it's running fine.
`scripts/notifier.py` watches the registry and says what changed, either to a
Discord channel or to the terminal when no webhook is set:

```bash
poetry run python -m scripts.notifier --network testnet          # prints here
DISCORD_WEBHOOK_URL=https://... poetry run python -m scripts.notifier --network testnet
```

Three properties are worth knowing. It **holds no keys and can't sign**, which is
enforced by a test that fails if anything key-shaped appears in the module rather
than promised in a comment. It **needs no indexer**, even for "which keeper",
because it knows the rounds between its last scan and this one and reads those
few blocks directly. And **restarting is quiet**, because the last announced
state is persisted, so a restart replays nothing.

It surfaces failures deliberately. An upkeep out of funds, or funded and due with
nobody servicing it, is the network not working, and saying so builds more trust
than a feed of good news.


# Part IV. What you're trusting

## Chapter 9. The security model

Arcron is unaudited. What follows is the project's own analysis, written down so
it can be argued with rather than taken on trust. Where this book adds a check of
its own it says so, and says what kind of check it was.

### Who can do what

The three parties are the same ones from Chapter 2, creator and keeper and target
app, and the boundaries between them are the whole safety story. The rules that
protect money: a keeper can never take more than the fee the box records, a
creator can never touch another creator's upkeep, and a target can never reach
the keeper's funds or re-enter Arcron. The rest of this chapter is how those
hold.

### The one real admin power: upgradeable until frozen

This is the biggest thing to understand, and the project says it first.
**Whether there's an admin key over your escrow depends on one flag.**

A deployment starts **unfrozen**. Until its creator calls `freeze`, that creator,
and only that creator, can replace the app's programs with `update` and thereby
reach every escrow in the app. They could redirect payouts, raise fees, or drain
escrow. No statement of intent removes that power. The honest way to put it is
that "no admin key" describes the deployment you're heading towards, not the one
in front of you.

`freeze` gives that up permanently, and one way. Nothing sets `frozen` back to 0,
and no later call can restore an update path, because the only call that could is
an update, which is now refused. And the flag is global state, so the promise can
be checked rather than believed:

```bash
poetry run python -m scripts.govern status --network testnet --app-id <id>
poetry run python -m scripts.verify_build --network testnet --app-id <id>
```

The first says whether the creator can still change the rules. The second says
whether the deployed bytecode is the source it claims to be. Together they're the
whole trust question.

> **Freezing doesn't remove risk. It exchanges one risk for another.** An unfrozen
> deployment can be repaired by someone who could also rob you. A frozen one can
> be robbed by nobody and repaired by nobody either, so its safety rests entirely
> on the bytecode being right the first time. Read alongside the MainNet gate,
> which is self-review with no paid audit, a frozen MainNet deployment is three
> things at once: no admin key, no third-party review, and no way to patch. Each
> is defensible on its own. The combination is the actual risk.

Why does the window exist at all? Because being unable to fix a bug is expensive
while nobody depends on the deployment yet. Two earlier deployments were abandoned
rather than repaired, stranding 243,000 µALGO of box deposits and forcing every
creator to cancel and re-register by hand. `DeleteApplication` is refused always,
frozen or not, because deleting an app with escrow in it would strand every
µALGO.

### What "alpha" means

The stage isn't a colour word. The project runs a ladder, alpha then beta then rc
then mainnet, and the current TestNet deployment is **alpha**, which carries a
specific promise and a specific non-promise.

Alpha, beta and rc all live on TestNet. An **alpha** app id can be replaced for
any reason, so expect to `cancel` and re-register if it is. Beta and rc add
sustained-uptime and self-audit requirements before MainNet is even considered.
MainNet itself is gated on the 1.0 contract staying *unchanged* for a sustained
period, where any struct change restarts the clock, plus a continuously serviced
dogfood with the notifier's record as evidence, plus a rigorous self-audit and no
paid audit, behind a 2-of-3 multisig.

So "alpha, unaudited" on the title page is a contract, not a disclaimer. Use only
`769891898` and treat it as replaceable.

### The threat model, checked

The project enumerates its adversaries and how each one is stopped. The claims
below were also read against the contract source and its compiled bytecode while
this book was being compiled. That's a source-and-bytecode read, **not an audit**.

**An adversarial keeper.** Can it take more than the fee? No: the fee is computed
entirely from box state and paid to the caller, confirmed by reading the payout
paths. Can it serve slowly to be paid more? With a fee ceiling, yes, and that's
escalation working as designed, bounded by `fee_cap`. What it can't do is farm
the ceiling off a backlog, because a replay never escalates (Chapter 6, Lesson
3). Measured before the guard: 100% of a 400,000 µALGO escrow across 34 runs.
After it, the first run only. Can it drain one upkeep to pay another? No. Each
box carries its own balance, checked before payment, and the app's spendable
balance always covers the sum of every escrow. The refund on cancel exactly
matches the escrow plus the released deposit, and inner-transaction fees are set
to zero via fee pooling, so the escrow is never touched for fees. The keeper's
own transaction group pays them.

**An adversarial creator.** Can it strand the app account? No: `register` collects
exactly what the box costs, derived from the encoded box rather than restated,
and `cancel` returns exactly that. Can it register an upkeep that traps its own
funds? Not any more. Three states used to register happily and then fail forever:
an argument list longer than the fan-out, a `fee_cap` the escrow could never
reach, and a `fee_asset` with a zero bonus. All three are now rejected at
registration, and escrow always leaves by `cancel` if nothing else.

**A malicious target app.** Can it re-enter Arcron? No, and not because of
ordering: the AVM refuses outright with `attempt to re-enter <app>`, and
independently the contract writes box state before submitting any inner
transaction. Two lines of defence. Can it spend the keeper's ALGO? No. Arcron's
inner transactions carry a zero fee and draw on the group's pooled fee, which the
keeper sized, and a target's own inner transactions are paid by the target.

> **What a read for this book found.** While compiling this book, its author read
> the contract source and compiled bytecode looking specifically for a path that
> loses money, a way to lock an upkeep's funds without the creator's consent, and
> a griefing win. That pass found none. The refund accounting is conservative,
> the one dangerous multiply (fee escalation) is bounded by the input caps and
> can't overflow, and a creator can always cancel and recover, even against a
> hostile bonus asset, because the ASA transfer is best-effort while the ALGO
> refund is not. This is a source read, **not an audit**, and the project remains
> unaudited. Treat it as one more reason to go and check for yourself, not as a
> clean bill of health.

### The console's one defence: the quarantine

The contract is permissionless, so anyone can deploy a look-alike with the same
ABI and box layout. That makes the console's address a security property: a link
carrying a different app id could point a stranger at a hostile clone that shows
the same registry and accepts the same register form.

The defence is **quarantine**. A link naming an app that isn't the published one
lands as *foreign*, and three things follow, none of them optional. Every money
button is dead, because `canCommitMoney` refuses independently of the display
logic. The id is never written to browser memory, so the poison can't outlive the
visit. And the console says so, names both ids, and offers one click back.
LocalNet is treated as *unverifiable* rather than *foreign*, because there's no
published deployment there and the node is your own machine, so a link can't aim
it at anything an attacker controls.

> **A caveat this book adds.** The console's *primary* mitigation, that the
> `?app=` parameter is inert outside developer mode, is weaker than it reads,
> because developer mode is itself switched on by a URL parameter (`?dev=1`). The
> quarantine still holds, and a foreign app's money buttons stay dead behind an
> explicit "continue anyway" click, so this is defence-in-depth erosion rather
> than a bypass. But treat the quarantine as the real barrier, not the inert
> parameter, and never click "continue anyway" on an app id you didn't choose.

### Known and accepted risks

These are real, understood, and shipped anyway.

**A lone keeper is paid the ceiling.** With no competition, escalation isn't a
worst case, it's the price. The mitigation is that the default is no escalation
at all (`fee_cap = 0`).

**A top-up doesn't reset lateness**, so funding a long-dormant upkeep is charged
the ceiling on its next run. The console warns you where the money is about to go.

**An upkeep can be stranded by its own target.** If a target becomes unexecutable
and the creator is gone, the escrow is stranded, because only the creator can
recover it. Prefer immutable targets, or ones you control.

**A refund can fail if the creator's account is empty**, because Algorand rejects
a payment that would leave the receiver below the 100,000 µALGO account minimum.

**Overpaid box deposit is not returned.** Send the exact amount. The console
computes it for you.

**Registry spam degrades keepers.** The box deposit is refundable, so a spammer's
real cost is only transaction fees and locked capital. A keeper that cared would
cache boxes and re-read on change.

### Reporting a bug

A live-funds vulnerability goes to a private draft security advisory, not a public
issue. `SECURITY.md` is the authoritative policy. Anything already public can be
a normal issue.

## Chapter 10. What it costs, honestly

### Cheaper than a paid host, not cheaper than free

Chapter 2 gave the table. Here's what it means. Against paid hosts you'd
otherwise run a bot on, Arcron **at the 4,000 µALGO floor** is **7.6x** cheaper,
$0.27 against fly.io's $2.02. At the fee the console actually suggests, 10,000
µALGO and about $0.66 a month, the gap narrows to **3.0x**. Smaller, still real.
Against the free options, Lambda plus EventBridge or GitHub Actions in a private
repo, it isn't cheaper at all, and the project says so.

> **One multiple does not cover both fees.** An earlier draft of
> [`docs/why.md`](../why.md) printed 7.7x for the floor *and* the suggested fee.
> The floor's real ratio against $2.02 is 7.6x and the suggested fee's is 3.0x,
> so reusing one number overstated the suggested fee by two and a half times.
> Quote the fee alongside the multiple, or quote neither.

### The multiple is a bet on the ALGO price

The ratio is denominated in ALGO on one side and dollars on the other, so it
moves *against* Arcron precisely when Algorand succeeds:

| against | parity at ALGO |
|---|---|
| $4.10/mo | $1.40 |
| $2.02/mo | **$0.69** |

**ALGO hasn't traded near either price in years.** It last closed above $0.70 on
**2022-04-28** and above $1.42 on 2022-01-13, and its high over the last two
years is **$0.6135**, below even the lower parity point. Parity needs roughly a
sevenfold rise from spot.

That correction makes the argument weaker rather than stronger, which is why it's
here. An earlier draft of `docs/why.md` claimed ALGO had traded above both parity
prices within the last two years, and it hadn't. What survives is narrower: a
fiat-denominated competitor and a crypto-denominated one can't be compared with a
fixed multiple, and the multiple isn't a property of the design.

### Where running your own bot wins

Above about **10 concurrent hourly upkeeps**, running your own bot on the
cheapest paid host is cheaper. One process services any number of targets from
one key, so "ten contracts means ten bots" is false, and false in a way this
repository disproves: the reference bot is a single process servicing the whole
registry.

The crossover depends entirely on which host you compare against, which is how an
earlier draft came to print 26. That's the figure for a $5 host, on a page whose
own table quotes $2.02.

| against | crossover |
|---|---|
| fly.io, $2.02 | **10 upkeeps** |
| Hetzner, $4.10 | 21 |
| a $5 host | 25 |

> **Where the crossover comes from**, so you can check it. Self-hosting doesn't
> make the chain free: calling your own target still costs the 1,000 µALGO outer
> fee. So the *incremental* cost of Arcron at the floor is 4,000 - 1,000 = 3,000
> µALGO per execution, or 732 x 3,000 = 2.20 ALGO, about $0.199 a month per
> schedule. A $2.02 host divided by that is 10.1, hence 10.

The asymmetry that survives is narrower and real: **no hot key, and no
operational attention.** That's worth something, and it isn't a process count.

### The uncomfortable structural finding

This is the part the project flags as not comfortable, and it's the most
important thing in the chapter.

At the 4,000 µALGO floor a keeper nets about 1,000 µALGO per execution, so one
keeper needs roughly 75 concurrent hourly upkeeps to fund a $5 host. But a
*creator* crosses over to self-hosting at **10** against the cheapest paid host.
Those numbers are the wrong way round. The ratio that closes the gap is
`(fee - 1000) / (fee - 3000)`:

| Fee | Creator pays/mo | Creator crossover vs $2.02 | Keeper funds a $5 host at |
|---|---|---|---|
| 4,000 µALGO (the floor) | $0.27 | 10 | **75** |
| 10,000 µALGO (suggested) | $0.66 | 3 | **11** |
| 20,000 µALGO | $1.33 | 2 | 4 |

**The floor is priced for the creator and sits below the cost of supplying it.**
Raising the fee closes that. Around 10,000 µALGO the two converge at about ten
upkeeps and the network pays for itself, still 3.0x cheaper than the cheapest paid
host. That's the sustainable operating point, and it isn't the one the minimum
advertises. The contract half-admits it already: *"A creator who wants keepers who
do not care about their token should set a fee above this floor."*

> **The takeaway for a creator.** Don't register at the floor and expect a
> stranger to keep your upkeep alive for free. Price it where a keeper actually
> profits, which is 10,000 µALGO for an hourly upkeep and is what the console
> suggests, or run the keeper yourself. This is an economics problem, not a safety
> one, but it's the one to understand before you rely on the network.

### Rounds drift, and that costs you too

A cadence is a round count and rounds run slightly faster than nominal, so an
"hourly" upkeep fires every 59.0 minutes and slides ~12 hours against the calendar
over a month. That also means it fires **732 times, not 720**, so at the floor it
costs 2.93 ALGO a month rather than the naive 2.88. Every figure in this chapter
is computed at 732. Budget against your real cadence, not the nominal one.

## Chapter 11. Is a keeper network the right idea?

### The argument that doesn't need agents

Strip away the hype and the claim is small and durable: a smart contract can't
wake itself up, and Algorand has no productized way to give it a heartbeat.
That's true today and checkable, with no ARC and no shipped fully-permissionless
alternative, and it survives being wrong about everything else on the page.

### The argument that does need agents, at its real strength

The payments world is about to have a "pay" verb for autonomous agents, through
x402 and the agent-to-agent work, and no "wake up" verb. x402's own spec puts
recurring payments and open-ended allowances explicitly out of scope. So the gap
is real. Be precise about how much it proves.

**The weak form is wrong.** "Agents need scheduling, so they need Arcron" doesn't
follow. An agent alive enough to hold funds and make decisions is alive enough to
call its own contract, and doing that is cheaper.

**The strong form is the interesting one.** Arcron wins when the schedule should
*outlive the agent that created it*, when autonomy shouldn't be only as durable
as somebody's running process. That's liveness that survives its author, and no
agent framework provides it, because every framework assumes the agent is
running.

Whether anyone wants that is stated, honestly, as an open question rather than a
claim.

### The boundary the design refuses to cross

Arcron is the clock, not the eyes. It won't let a keeper supply *data*, and
that's a deliberate closed door rather than a missing feature. Issue #22 is
closed, not deferred. The reasoning is worth internalising, because it defines
the product:

> Letting a keeper choose *what a contract is told* makes every keeper a trusted
> party. That's an oracle network, which is a different product. Declaring which
> *resources* a call may touch is safe, because the creator still fixes what is
> called. Letting a keeper supply *data* inverts the one guarantee Arcron makes.

The supported answer for data-driven automation is **oracle pairing**. A reporter
pushes values into an oracle, an Arcron upkeep triggers `settle()` on a cadence,
and settlement reads the stored value. Arcron supplies the timing guarantee, so
settlement can't be stalled, delayed, or selectively timed by an interested
party, and it supplies nothing else. One case needs no oracle trust at all: a
**staleness check** comparing a feed's last-updated round against the current
round, because comparing round numbers can't be lied to.

For the same reason **keeper staking is closed too** (issue #15). A keeper has
exactly one action, `execute`. A wrong execution is impossible, because the call
is fixed. A failed one is already free. And not executing isn't an offence. So a
bond would have nothing to slash, and it would add an owner-shaped thing to an
ownerless contract.

### The lessons from those who tried

Every predecessor failed at the same seam, and it was adoption, not engineering.
AlgoRhythm stopped exactly at the incentive design. BiatecCron shipped and ran
three tasks in two years, all of them its author's own. Keep3r's active keepers
peaked at six and most of its jobs were never worked. The engineering isn't the
hard part. Being a well-built thing somebody needs is.

### The one test that settles it

The project has written down the falsifiable version, and it's refreshingly
narrow:

> If this is real infrastructure, somebody outside the project registers an upkeep
> for something they actually wanted scheduled, within a few months of it being
> visible. If a year passes and every upkeep is still theirs, the design was fine
> and the demand was not there.

That's the number that settles it. Not keeper count, not throughput, and nothing
Ethereum measures. As of this writing the count of upkeeps registered by
strangers is **zero**, and the project says that louder than any critic would.
The mechanism holds up. Whether the world wants it is genuinely unknown, and that
honesty is the most trustworthy thing about the project.


# Part V. Reference

Look-up material for building against Arcron. Everything here is stated
elsewhere in the book; this part collects it in one place. Where a figure is a
measured claim, treat it as checkable against the live chain.

## Appendix A. The public API

All methods are ARC-4 ABI methods on the keeper app
(`smart_contracts/keeper/contract.py`). The exact signatures matter, because a
selector is `sha512_256(signature)[:4]`, and a table of parameter *names* isn't
enough to compute one. Getting it wrong yields `logic eval error: err opcode
executed` with no mention of the method, which is among the least debuggable
failures in the system.

```
register(pay,pay,uint64,byte[][],uint64,uint64,uint64,uint64,uint64,uint64)uint64
execute(uint64)uint64
top_up(uint64,pay)uint64
cancel(uint64)uint64
opt_in_asset(pay,uint64,uint64)uint64
top_up_asset(uint64,axfer)uint64
freeze()void
update()void
```

> Note that `opt_in_asset` takes the asset as a plain `uint64`, **not** the ARC-4
> `asset` reference type. The natural guess is wrong. The machine-readable source
> of truth is the ARC-56 spec at
> `smart_contracts/artifacts/keeper/Keeper.arc56.json`.

| Method | Callers | Purpose |
|---|---|---|
| `register(...) → uint64` | anyone | Create an upkeep; returns its id. Full 10-argument signature in the code block above; each argument is detailed under *register preconditions* below. |
| `execute(upkeep_id) → uint64` | anyone | Fire a due, funded upkeep; pays the caller the effective fee; returns the next due round. |
| `top_up(upkeep_id, funding_payment) → uint64` | anyone | Add escrow; returns the new balance. |
| `cancel(upkeep_id) → uint64` | creator only | Delete the upkeep; refund escrow **plus** box deposit **plus** any unspent bonus. |
| `opt_in_asset(mbr_payment, upkeep_id, asset) → uint64` | anyone | Let the app hold an upkeep's bonus asset. 0.1 ALGO, permanent, non-refundable. |
| `top_up_asset(upkeep_id, asset_funding) → uint64` | anyone | Add to an upkeep's ASA bonus escrow. |
| `freeze() → void` | creator only | Give up the update path permanently. |
| `update() → void` | creator only | Replace the programs. Refused once frozen. |

**The register preconditions that are in no signature.** Each one fails as a bare
`assert` that names nothing, so getting one wrong costs an hour of staring at a
program counter.

- **Both payments go to the keeper application's own account**, the address
  derived from its app id, not to the creator and not to a keeper.
- **Both payments must be sent by the same account that sends the app call.** A
  third party can't fund somebody else's *registration*. `top_up` is the
  opposite: funding an upkeep that already exists is a permissionless gift.
- **The group order is `[mbr_payment, funding_payment, app call]`.**
- **The box deposit is a minimum, not an exact amount.** Overpaying is accepted
  and isn't refunded, so pay the formula.
- **The funding payment must cover at least one execution** at the price this
  upkeep can be charged, which is `fee_cap` when a ceiling is set and
  `fee_per_execution` otherwise.
- **The call must carry a box reference for `b"u" + itob(n)`**, where `n` is the
  app's global `next_upkeep_id`. Read that global first to predict the id. A
  typed algokit-utils client does this for you.

**The execute precondition.** Your `execute` transaction must itself carry the box
reference for `b"u" + itob(upkeep_id)` and a foreign-app reference to the target.
Without the box you get `invalid Box`; without the app, `unavailable`. Arcron
spends two of the eight reference slots on your behalf in its own accounting, but
it doesn't attach these two to your transaction for you.

**Constraints asserted on-chain:**

- `10 ≤ interval_rounds ≤ 1,000,000,000` rounds.
- `4,000 ≤ fee_per_execution ≤ 1,000,000,000` µALGO.
- **App-arg count 1 to 3** (`0 < count ≤ 3`, counting the selector), and the
  encoded argument list must be `≤ 1,024` bytes. The lower bound is on the
  *count*, not the byte length: an empty argument list is rejected on count.
- `policy` is `CATCH_UP` (0) or `SKIP_AHEAD` (1). `fee_cap` is either 0 or between
  `fee_per_execution` and 1,000,000,000 µALGO. `fee_asset == 0` or `asset_fee > 0`.
- Executions are NoOp inner app calls carrying every stored app arg.
- The ASA bonus is paid **on top of** the ALGO fee, never instead of it.

## Appendix B. The Upkeep box encoding

Each upkeep is one box, named `b"u" + itob(upkeep_id)` (9 bytes), holding an
ARC-4 head/tail encoding of the `Upkeep` struct. The head is always 130 bytes,
and the contract always writes 130 as the tail offset at bytes `[40:42]`.

| Bytes | Field |
|---|---|
| `[0:32]` | creator address |
| `[32:40]` | target app id |
| `[40:42]` | offset to the `call_args` tail (always 130) |
| `[42:50]` | interval_rounds |
| `[50:58]` | next_execution_round |
| `[58:66]` | fee_per_execution |
| `[66:74]` | balance |
| `[74:82]` | times_executed |
| `[82:90]` | policy |
| `[90:98]` | fee_cap |
| `[98:106]` | last_serviced_round |
| `[106:114]` | fee_asset |
| `[114:122]` | asset_fee |
| `[122:130]` | asset_balance |
| `[130:]` | tail: ARC-4 `byte[][]` |

> **The tail rule that'll bite you.** The tail is a `uint16` count, then one
> `uint16` offset per argument, then each argument as a `uint16` length followed
> by its bytes. **Every offset is measured from just after the count, so add 2
> before indexing into the tail.** Omitting the +2 doesn't raise. It yields a
> plausible wrong value: one real box decodes to `["0004"]` instead of
> `["40d7be68"]`, and a keeper built that way would mis-read every upkeep in the
> registry and never find out. Both reference decoders reject any box whose head
> isn't 130 bytes rather than reading past the end of a shorter, older box.

**Box deposit (minimum balance):** 2,500 + 400 × (139 + len(encoded call_args))
µALGO, so **62,100 µALGO for a bare 4-byte selector**. Fully refunded on cancel.

**Global state:** `next_upkeep_id` is the id `register` will assign next, so read
it to predict your box name. `frozen` is 0 while the creator can still replace
the programs and 1 once `freeze` has been called; an app predating governance
carries no `frozen` key at all, and a missing flag reads as frozen, because such
an app has no update path.

**Reference decoders**, pinned to the same recorded box so they can't drift
apart:

- Python: `scripts/keeper_bot.py::_decode_upkeep`
- TypeScript, which the console imports: `js/src/upkeep.ts`

## Appendix C. Command cheat-sheet

`fledge` is the project's task runner. Prefer it over calling tools directly.

**Build, test, CI:**

```bash
fledge lanes run ci          # build + unit tests + strict spec check (keep this green)
fledge lanes run local       # ci + the LocalNet end-to-end (needs algokit localnet)
fledge lanes run endurance   # build + tests + spec + a soak + a scenario (a longer suite)
poetry run python -m smart_contracts build      # Puya compile + typed clients
poetry run pytest tests/ -q                       # unit tests (mocked chain)
specsync check --strict                            # spec-drift check
poetry run python -m scripts.keeper_e2e --network localnet   # full e2e
```

**The console:**

```bash
cd web && bun install && bun run ng serve     # dev server at :4200
bun test                                       # console unit tests
fledge run web-render                          # the rendered-page measurement audit
fledge run web-build-hosted && fledge run web-verify-hosted   # hosted build
```

**Register and operate.** Choose the network with `--network` or `ARCRON_NETWORK`.
`--app-id` is required, because there's no default deployment.

```bash
poetry run python -m scripts.keeper_bot --network testnet --app-id 769891898         # keeper loop
poetry run python -m scripts.keeper_bot --once --network testnet --app-id 769891898  # single scan
poetry run python -m scripts.keeper_bot --check --network testnet --app-id 769891898 # health probe
poetry run python -m scripts.keeper_race --network localnet                          # prove a race
poetry run python -m scripts.notifier --network testnet                              # registry watcher
cp .env.testnet.template .env.testnet          # then, for a worked registration:
poetry run python -m examples.register_upkeep
```

**LocalNet:**

```bash
algokit localnet start        # start the local chain
fledge run deploy-localnet    # deploy the keeper (add --with-pulse for the pulse target); idempotent
algokit localnet reset        # empty chain
```

> **Rules of the road.** Poetry venv, Python **3.13**, never 3.14, because
> coincurve has no wheels for it. `.env.<network>` holds per-network config and is
> gitignored; never commit a mnemonic. The TestNet deployer is a throwaway and
> must never be reused on MainNet. And LocalNet is dev mode, so rounds only
> advance when a transaction is sent.

## Appendix D. Deploying and governing a deployment

Deployment is deliberate on every network. Nothing is automated.

```bash
fledge run deploy-localnet     # LocalNet only
fledge run deploy-testnet      # needs .env.testnet with DEPLOYER_MNEMONIC
fledge run deploy-mainnet      # needs .env.mainnet AND ARCRON_ALLOW_MAINNET=1
```

**Two program pages.** The contract compiles to just over 2,048 bytes, so it
allocates an extra program page. That extra page costs the **creator (deployer)
account** 100,000 µALGO of minimum balance permanently, and the app account has
its own 100,000 µALGO base minimum on top, so budget 0.2 ALGO locked in total.
Note *which* account: the page MBR is locked on the deployer, not the app. Pages
can't be added by an update either, since it's create-only.

**Governance:**

```bash
fledge run govern -- status  --network testnet --app-id <id>   # frozen? which bytecode?
fledge run govern -- update  --network testnet --app-id <id>   # replace programs (unfrozen only)
fledge run govern -- freeze  --network testnet --app-id <id>   # give up update, forever
```

`status` prints the creator, the program sizes, the **combined** approval plus
clear `sha256`, and `frozen`. Always compare the *combined* digest: an
approval-only hash would let a hostile clear program ship beside an honest
approval. A pre-governance app prints `frozen absent`, which is the stronger
guarantee, because it has no update path at all.

**Multisig, for MainNet.** Set `ARCRON_MULTISIG_THRESHOLD` and
`ARCRON_MULTISIG_ADDRESSES` and the creator becomes a multisig;
`scripts/deploy.py` then refuses to run from a single key. `govern update` and
`govern freeze` write an unsigned transaction for holders to sign wherever their
keys live. Always `show` a file before you `sign` it:

```bash
fledge run govern -- update --network testnet --app-id <id> --out update.json
fledge run govern -- show   --file update.json --app-id <id>
SIGNER_MNEMONIC="..." fledge run govern -- sign --file update.json --app-id <id>
fledge run govern -- submit --file update.json --app-id <id>
```

The MainNet plan is three keys with a threshold of two, so one can be lost and
one compromised without losing control. Member order is part of a multisig
address, so the same keys in a different order are a different account holding
nothing. Post-quantum Falcon accounts can't be multisig members, because their
address is a hash rather than a curve point, and `scripts/multisig.py` refuses
them with a real curve-membership test.

**Verifying a deployment you didn't make.** `verify_build` rebuilds from the
working tree and compares the compiled **bytecode**, not the TEAL text, which
loses comments and formatting on assembly, against what algod reports for that
app.

## Appendix E. The design decisions, in brief

Why the system is shaped the way it is, distilled from the project's design docs.

**1.0 scope (decided 2026-08-24).** The `Upkeep` struct can't change in place,
because an update replaces code and not the shape of boxes that already exist. So
a struct change means a new app id, an empty registry, and every creator
cancelling and re-registering by hand. Four struct-touching features were
therefore batched into one final release (per-upkeep catch-up policy #7, fee
escalation #14, resource declaration #8, and the ASA-fee capability #9), and the
surface is then frozen.

**Why CATCH_UP and SKIP_AHEAD had to ship with escalation.** Combining catch-up
replay with escalation measured *from the schedule* would make every replay pay
the escalated fee, measured at 58% of an escrow across 20 intervals. Measuring
escalation from `last_serviced_round` instead drops that to 22%: the first run of
a burst clears the market and the rest pay base. The two features "multiply into
something the creator cannot have modelled", so they were designed as one.

**Why three call args, not sixteen.** The obvious multi-argument loop is silently
wrong, because Puya hoists the inner transaction out of the loop and keeps only
the last arg. So each argument count needs its own static branch, and program
size grows super-linearly. Three, meaning the selector plus two ABI args, is what
fits on one program page alongside governance, and any arity is still reachable
by packing arguments into a single ARC-4 struct.

**Why the ASA fee is a bonus, not a denomination.** A keeper's real costs are
ALGO, and no contract can price your token without an oracle. Keeping a mandatory
ALGO floor and adding the ASA on top lets the contract guarantee a keeper is
never out of pocket without ever knowing what the ASA is worth. *"A capability is
only a capability if the ALGO default is still complete on its own."*

**Why staking (#15) and keeper-supplied data (#22) are closed, not deferred.**
Staking has nothing to slash: a keeper's only action is `execute`, a wrong
execution is impossible, and a failed one is already free. Keeper-supplied data
inverts the one guarantee, that the creator fixes *what* is called, and the
useful version of it is an oracle network, which is a different product. *"A
scheduled call is a heartbeat, not a courier."*

## Appendix F. Glossary

| Term | Meaning |
|---|---|
| **Upkeep** | A registered standing instruction: call this app with this data every N rounds, from this escrow. One box per upkeep. |
| **Keeper** | An off-chain process that calls `execute` on due upkeeps and collects fees. |
| **Creator** | The account that registered an upkeep; the only one who can cancel it. |
| **Target app** | The app an upkeep calls. Chosen and fixed by the creator. |
| **Round** | Algorand's block unit, about 2.70 to 2.8 s. Arcron's unit of time. |
| **Escrow / balance** | The ALGO an upkeep holds to pay for its executions. |
| **Box deposit** | The minimum-balance cost of an upkeep's box; refunded on cancel. |
| **CATCH_UP / SKIP_AHEAD** | Missed-run policy: replay every missed interval, or drop the backlog and keep phase. |
| **fee_cap / escalation** | An optional ceiling; a neglected upkeep's fee rises toward it. 0 = off. |
| **Effective fee** | The fee actually paid: base, or escalated when late and a ceiling is set. |
| **Starved / stalled** | Starved = escrow below one fee (creator's problem). Stalled = funded, due, unserviced (keeper's problem). |
| **Frozen** | Whether the creator has permanently given up the power to replace the programs. |
| **µALGO** | MicroALGO; 1 ALGO = 1,000,000 µALGO. The floor fee is 4,000 µALGO. |
| **Pull pattern** | Do accounting in the scheduled call; let counterparties collect in their own transactions. |

## Appendix G. The numbers, in one place

**Deployment (TestNet, as of August 2026):**

| | |
|---|---|
| Keeper app, **use only this one** | `769891898` (alpha-3) |
| Pulse demo target | `769891902` |
| Console | `corvidlabs.xyz/arcron/console/` |
| CORVID asset (candidate, not wired in) | `3225439167` |

> **Use only `769891898`.** Every other app id is either a superseded earlier
> deployment, with an older box shape or no governance, or an outright
> look-alike. You never need their ids: the console quarantines any app id that
> isn't `769891898`, and the current tooling refuses to decode an old box rather
> than misread it. If you're holding an older link, treat it as dead. The
> project's record of which deployments those were lives in `docs/releases.md`
> and `docs/status.md`.

**Limits and constants:**

| | |
|---|---|
| Minimum fee | 4,000 µALGO (0.004 ALGO) |
| Console-suggested fee | 10,000 µALGO (0.010 ALGO) |
| Maximum fee / cap | 1,000,000,000 µALGO |
| Interval | 10 – 1,000,000,000 rounds |
| Max app args | 3 (including selector) |
| Max call data | 1,024 bytes |
| ASA opt-in deposit | 100,000 µALGO (permanent) |
| Box deposit (bare selector) | 62,100 µALGO (refundable) |
| Keeper cost per execution | ~3,000 µALGO (4,000 with an ASA bonus) |
| Keeper start floor | 103,000 µALGO |

**Drift (rounds are not a clock; MainNet measured 2.752 s/round):**

| Cadence | Rounds | At 2.8 s | At 2.752 s | Drift/cycle |
|---|---|---|---|---|
| hourly | 1,286 | 1.0 h | ~0.98 h (59 min) | ~1 min |
| daily | 30,857 | 24.0 h | 23.6 h | ~25 min |
| weekly | 216,000 | 168.0 h | 165.1 h | ~2.9 h |

**Runway (at the 4,000 µALGO floor):**

| Escrow | Executions | Hourly | Daily |
|---|---|---|---|
| 0.1 ALGO | 25 | ~1 day | ~25 days |
| 1 ALGO | 250 | ~10 days | ~8 months |
| 100 ALGO | 25,000 | ~2.8 years | ~68 years |

**Economics, the one basis every cost figure in this book uses.** Change any of
these and every number in Chapters 2 and 10 moves, so they're the first thing to
recompute. [`docs/why.md`](../why.md) owns them.

| | |
|---|---|
| Round time (MainNet measured) | 2.752 s |
| Nominal-hour upkeep | 1,286 rounds = 59.0 minutes |
| Executions per month | **732** (not 720) |
| ALGO price used | **$0.0907** |
| Monthly cost, hourly upkeep at the floor | 2.93 ALGO ≈ $0.27 |
| Monthly cost, hourly upkeep at the suggested fee | 7.32 ALGO ≈ $0.66 |
| Cheaper than the cheapest paid host by | **7.6x** at the floor, **3.0x** suggested |
| Creator crossover to self-hosting (vs fly.io $2.02) | 10 upkeeps at the floor, 3 at the suggested fee |
| Keeper funds a $5 host at | 75 upkeeps at the floor, 11 at the suggested fee |
| Parity with a $2.02 host at | ALGO $0.69 (two-year high: $0.6135) |

---

*End of the guide. The source of truth is always the repository:
`smart_contracts/keeper/contract.py` for the contract, `specs/keeper/` for its
ABI, and the live chain for anything this book states as a number.*


