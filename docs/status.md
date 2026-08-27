# Where Arcron stands

Last updated 2026-08-25. This page is the one place to look if you want to
know what exists, what state it is in, and what happens next. If it disagrees
with anything else in this repo, this page is probably the stale one: check
[the release table](releases.md), which is generated against live app ids.

## The short version

The keeper network is live on TestNet, holds real escrow, and has been through
five rounds of adversarial review plus four independent contract audits. It is
**upgradeable**, which means bugs found now are fixable in place rather than
requiring everyone to cancel and re-register. Nothing but our own money has
ever been escrowed in it.

The honest summary is that correctness is in good shape and **usability is
untested**. Every interaction the system has ever had was with someone who
already knew how it worked.

## What is live

| | |
|---|---|
| Keeper | TestNet app [`769891898`](https://testnet.explorer.perawallet.app/application/769891898) |
| Demo target | Pulse [`769891902`](https://testnet.explorer.perawallet.app/application/769891902) |
| Programs | 2,219 bytes across two pages, sha256 `c94c6e0c…` (alpha-3) |
| Governance | **not frozen**: the creator can still replace the programs |
| Registry | ten upkeeps, the fastest firing about every ten minutes |
| Console | [corvidlabs.xyz/arcron/console/](https://corvidlabs.xyz/arcron/console/), **live, and running a build older than this tree** |

`poetry run python -m scripts.verify_build --network testnet --app-id 769891898`
proves the deployed programs are this source, byte for byte. Anyone can run it.

Three earlier deployments are superseded and must not be used: `769823086`,
`769802474`, and `769772891`.

## The contracts

Four independent audits plus three re-scores covered seven contracts in
August 2026, and most of their findings landed in four of them: `treasury`
had two validation gaps, `deadman` had a total-loss trap on its default
deploy path, `embargo` let a stranger hijack a fresh instance, and `watchdog`
documented an error its own assert ordering made unreachable. None of that
was the product. Those four were cut from the repository on 2026-08-26 so
review attention concentrates on the contract that actually holds other
people's money; see the commit for the reasoning. "Ships" below means whether
it is ready to hold value belonging to someone other than us.

| Contract | What it is | Ships | Notes |
|---|---|---|---|
| `keeper` | the network itself: escrow, scheduling, keeper payment, governance | **yes** | five review rounds plus an audit; no unresolved findings |
| `subscription` | recurring payments, an example target | **yes** | audited clean; the integration example the docs recommend copying |
| `rain` | community giveaway, winner drawn from a randomness beacon | **yes** | one blocker found and fixed: a prize asset created `default_frozen` could be received and never sent; becoming the first public use, and part of the dogfood |
| `pulse` | trivial demo target | n/a | exists to be called; the heartbeat target for the dogfood's uptime clock |

The audits also refuted one reported blocker. An extra program page is charged
to the creator account, not the app account; measured against both live
deployments the app account base is exactly 100,000 microalgo with a second
page in use.

## What we know, and what we do not

Proven, and not worth re-proving:

- A 20-stage end-to-end test passes on TestNet and finishes with the app
  account at exactly its minimum balance, so every escrowed microalgo was
  either paid to a keeper or refunded to its creator.
- A simulation ran 1,008 executions across 96 upkeeps with three keepers and
  came out exactly solvent. Read that as a busy registry rather than as
  competition: the three take turns, and until 2026-08-26 all three were
  signing as the same account, because `scripts/scenario.py` funded three and
  then let the bot take its signer from the environment.
- A losing keeper pays nothing. Algorand rejects failing transactions at
  validation rather than including them, so there is no fee to pay.
  Established twice over: by construction in `scripts/keeper_e2e.py` stage 14,
  and, since 2026-08-27, between two keepers that genuinely collided on
  TestNet.

  **The first real race**, staged by `scripts/keeper_race.py`: two keeper bots
  aligned to the same barrier both scanned round 66703234 and both found
  upkeeps 71 and 75 due. They split the registry, each winning one and losing
  the other, which is the first time anything but a queue has happened here.
  On upkeep 75 the winner's `SFOP56PA…` is in block 66703238; the loser's
  `KXTAGVSR…` is in no block and no indexer, and the loser's balance moved by
  zero. A second run at round 66703289 reproduced it the other way round, with
  `RWZRK7WB…` winning and `OMTXGJQT…` thrown away.

Genuinely unknown, and only answerable by other people:

1. ~~**Can someone get an upkeep running from the docs alone?**~~ **Tested
   2026-08-26, and the answer was no.** An agent given only `README.md`,
   `docs/arcron.md` and `docs/integrating.md`, with no access to this
   repository, did get an upkeep registered, executed by a stranger account and
   cancelled on LocalNet. It needed twelve guesses and had to disassemble the
   deployed approval program to recover the ARC-4 selectors, because no
   document contained them. `next_upkeep_id` was undocumented, which makes
   `register` a deadlock from raw algosdk. The box tail description was wrong in
   a way that returns a plausible incorrect value rather than raising. Those are
   fixed. What the exercise did not settle is whether the docs now work, since
   they were repaired by reading that agent's report: **rerun it against a fresh
   agent before treating this as answered.**
2. **Does it survive unattended time?** The longest continuous run is minutes,
   and the TestNet dogfood was found dark on 2026-08-26 after roughly a day: the
   cron keeper was skipping for a missing secret and the local bot was pointed
   at a superseded app. The clock starts from a keeper running somewhere that is
   not a laptop.
3. **Does keeping actually pay?** Nobody has run a keeper for a week and
   looked at whether it was worth the gas.

Those are what the alpha tasks below exist to answer.

## What happens next

The stages and what each one freezes are in [releases.md](releases.md). In
order:

1. **Close every finding that a MainNet create would make permanent**, and
   reach a consensus of about 90 to 95 percent confidence across independent
   reviewers. Not every open finding: an earlier version of this page said
   that, and it contradicted the line below, because one of the open findings
   is "the console has no MainNet entry" and closing that publishes the path
   to the app.
2. Run the dogfood upkeep unattended, with the notifier watching. This is the
   only evidence that accrues while nobody is looking.
3. Answer the three unknowns above through the alpha tasks.
4. MainNet, deployed from the 3-of-5 multisig, with the app id unpublished
   until it is frozen.

### Why 90 to 95 and not 100

Because the deployment is upgradeable, and that is a deliberate trade rather
than a stage we have not finished yet.

A frozen contract has to be right the first time. Its only remedy for a bug is
telling every creator to cancel and re-register by hand, so the bar before
freezing is as close to certainty as a review process can get, which in
practice means a paid audit and months of unchanged bytecode.

An unfrozen one can be fixed in place. That does not make bugs acceptable; it
makes the cost of the last few percent of confidence wildly disproportionate
to what it buys, because the failure mode it protects against is one we have a
remedy for.

The honest counterpart, said plainly so nobody has to work it out: **that
allowance is ours and does not transfer.** The reason we can accept 90 rather
than 100 is that the creator can still reach every escrow, and while that is
true, anyone escrowing here is trusting a keyholder rather than bytecode.
Which is exactly why the app id stays unpublished until freeze, and why an
unexpected upkeep before then is a person to freeze for rather than a schedule
to finish.

The one rule held throughout: **do not freeze, and do not invite outside
escrow until it has run unattended for a while.** Not for a calendar, but
because "we can fix it" stops being a complete answer the moment someone
else's money is involved.

With one correction, which an outside review made and which matters:
**there is no such thing as not inviting people, on chain.** `register` is
permissionless, so anyone who learns the app id can escrow into it during
that window, and an explorer listing, a bot log, a README or a status post is
the invitation. The protection during the unattended period is not the
calendar and not our intent: it is that the MainNet app id is not published
anywhere. If an upkeep we did not create appears before freeze, that is a
real person who has trusted us, and the answer is to freeze then rather than
to wait out the remaining time.

## How to help

Three tasks, each about half an hour to an hour, each answering one of the
unknowns above:

- [#92](../../issues/92): register an upkeep using only the docs
- [#93](../../issues/93): run a keeper for an hour and say whether it was worth it
- [#94](../../issues/94): point Arcron at a contract you wrote

None of them ask you to break it. That has been done five times. What has
never happened is somebody simply **using** it.

Start at **https://corvidlabs.xyz/arcron/console/**. That address is also the
answer to "is this the real thing": the contract is permissionless, so anyone
can build a front end for Arcron, and where a console is served from is the
only thing that separates ours from a copy asking you to sign something.
Nothing else about a page proves anything, so check the address rather than
the page.

Everything is TestNet, so there is no real money anywhere in any of this.
Get test ALGO from <https://bank.testnet.algorand.network/>.
