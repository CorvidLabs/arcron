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
| Programs | 2,104 bytes across two pages, sha256 `0afab368…` |
| Governance | **not frozen**: the creator can still replace the programs |
| Registry | five upkeeps, one firing about every 70 seconds |

`poetry run python -m scripts.verify_build --network testnet --app-id 769891898`
proves the deployed programs are this source, byte for byte. Anyone can run it.

Three earlier deployments are superseded and must not be used: `769823086`,
`769802474`, and `769772891`.

## The contracts

Four independent audits covered all seven in August 2026. "Ships" below means
whether it is ready to hold value belonging to someone other than us.

| Contract | What it is | Ships | Notes |
|---|---|---|---|
| `keeper` | the network itself: escrow, scheduling, keeper payment, governance | **yes** | five review rounds plus an audit; no unresolved findings |
| `subscription` | recurring payments, an example target | **yes** | audited clean; the better of the two examples to copy |
| `watchdog` | flags a data feed that has gone quiet | **yes** | custodies no funds; [#97](../../issues/97) is a spec correction |
| `rain` | community giveaway, winner drawn from a randomness beacon | **yes** | one blocker found and fixed: a prize asset created `default_frozen` could be received and never sent |
| `embargo` | commits content to a future release round | **yes** | one blocker found and fixed: `schedule` had no caller check, so a stranger could hijack a fresh instance |
| `treasury` | scheduled distributions, an example target | **not yet** | two validation gaps, [#96](../../issues/96). Distribution maths is exact |
| `deadman` | escrow that passes to a beneficiary if the owner goes quiet | **not yet** | [#95](../../issues/95): `claim` can fire and then fail to pay |
| `pulse` | trivial demo target | n/a | exists to be called |

The audits also refuted one reported blocker. An extra program page is charged
to the creator account, not the app account; measured against both live
deployments the app account base is exactly 100,000 microalgo with a second
page in use.

## What we know, and what we do not

Proven, and not worth re-proving:

- A 20-stage end-to-end test passes on TestNet and finishes with the app
  account at exactly its minimum balance, so every escrowed microalgo was
  either paid to a keeper or refunded to its creator.
- A simulation ran 1,008 executions across 96 upkeeps with three competing
  keepers and came out exactly solvent.
- A losing keeper pays nothing. Algorand rejects failing transactions at
  validation rather than including them, so there is no fee to pay.

Genuinely unknown, and only answerable by other people:

1. **Can someone get an upkeep running from the docs alone?** Never tested.
2. **Does it survive unattended time?** The longest continuous run is minutes.
3. **Does keeping actually pay?** Nobody has run a keeper for a week and
   looked at whether it was worth the gas.

Those three are what the alpha tasks below exist to answer.

## What happens next

The stages and what each one freezes are in [releases.md](releases.md). In
order:

1. Run the dogfood upkeep unattended, with the notifier watching. This is the
   only evidence that accrues while nobody is looking.
2. Answer the three unknowns above through the alpha tasks.
3. Close [#95](../../issues/95) and [#96](../../issues/96) so every contract ships.
4. MainNet, deployed from a 3-of-5 multisig.

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

Everything is TestNet, so there is no real money anywhere in any of this.
Get test ALGO from <https://bank.testnet.algorand.network/>.
