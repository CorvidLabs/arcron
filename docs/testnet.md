# What is deployed on TestNet, and what it is doing

Everything Arcron runs on TestNet, in one place: which contracts exist, which
upkeeps drive them, how often, and how long each can pay for itself.

Read from the chain rather than remembered. Regenerate the tables with:

```bash
poetry run python -m scripts.keeper_bot --once --network testnet --app-id 769891898 --check
```

## The contracts

| what | app | state | what it is |
|---|---|---|---|
| **keeper** | [`769891898`](https://testnet.explorer.perawallet.app/application/769891898) | live, **not frozen** | Arcron itself. Upkeeps are boxes; anyone may execute a due one for its fee. |
| **pulse** | [`769891902`](https://testnet.explorer.perawallet.app/application/769891902) | live | A heartbeat counter that exists to be called. No state worth protecting, cannot fail, which is what makes it the right first target. |
| **rain (hub)** | [`770130162`](https://testnet.explorer.perawallet.app/application/770130162) | live, **immutable** | The rain hub: anyone opens a rain, one Arcron `draw` fires the due ones. No `update` and no `freeze` method exists, so the programs cannot be replaced. Deployed 2026-08-29. |
| ~~rain~~ | [`770029154`](https://testnet.explorer.perawallet.app/application/770029154) | superseded | The pre-hub single draw, gated on creator **and** a `corvid` unit-name prefix. Runs programs this tree no longer builds. Deployed 2026-08-27. |
| ~~rain (gate test)~~ | [`770030875`](https://testnet.explorer.perawallet.app/application/770030875) | superseded | The pre-hub gate test, on a collection we hold keys for. |
| ~~rain~~ | [`769988156`](https://testnet.explorer.perawallet.app/application/769988156) | superseded | The ungated version. Its last draw aged out and was abandoned, the prize returning to the pot, and the upkeep that drove it was cancelled and re-registered against the gated app. |

`not frozen` means the creator can still replace the programs. That is
deliberate while these iterate and it is a real power over anything escrowed;
see [`security.md`](security.md).

## The upkeeps

Read from the chain at round 66,820,047 on 2026-08-30.

| id | target | every | escrow | runway | runs | policy |
|---|---|---|---|---|---|---|
| 19 | `769891902` pulse | 12 h | 0.3326 ALGO | ~40 days | 9 | catch up |
| 20 | `769891902` pulse | 12 h | 0.3326 ALGO | ~40 days | 9 | skip ahead |
| 21 | `769891902` pulse | 12 h | 0.3783 ALGO | ~45 days | 9 | skip ahead |
| 22 | `769891902` pulse | 12 h | 0.3326 ALGO | ~40 days | 9 | skip ahead |
| 79 | `770029154` rain, **superseded** | 2 h | 7.7000 ALGO | ~62 days | 30 | skip ahead |
| 81 | `770041460` (our agent) | 58 min | 4.1000 ALGO | ~16 days | 58 | skip ahead |
| 82 | `769891902` pulse | 58 min | 3.0207 ALGO | ~12 days | 40 | skip ahead |
| 87 | `770082145` (our agent) | 58 min | 5.7500 ALGO | ~23 days | 22 | skip ahead |
| 91 | `770130162` rain hub | 58 min | 2.9480 ALGO | ~30 days | 18 | skip ahead |

Seven of twenty-eight, chosen because each one shows something: the `pulse`
set is both catch-up policies side by side, **91** is the dogfood, **79** is
still paying keepers to call an app superseded on 2026-08-29, and **87** is
overdue with 5.75 ALGO in it because its target reverts by its author's own
configuration, which no amount of escrow fixes. The other twenty are agent
registrations, twelve of them starved on a 20 round cadence. `fledge run
health` is the live view and says which of the two kinds of overdue each one
is.

**Upkeep 82 replaced upkeep 73 on 2026-08-28**, and the reason is worth
recording because nothing on chain shows it. 73 paid `MIN_UPKEEP_FEE` and also
offered an ASA bonus. The bonus transfer is a third inner transaction, so a
keeper spent 4,000 microAlgos to earn 4,000 and cleared exactly nothing. It was
serviced 39 times for free before `fledge run health` was written and said so.

A fee cannot be edited: like the method selector, it is fixed in the box at
registration, so correcting it meant cancelling and re-registering. 82 pays
10,000, which is what the console suggests and what 79 and 81 already pay, and
carries a **fee cap of 20,000**, which 73 did not. That second part matters
beyond this upkeep: the contract escalates only when `cap > fee`, so with a cap
of zero our own dogfood had never once exercised the escalating fee, which is
the mechanism [`prior-art.md`](prior-art.md) identifies as the thing no
comparable system has anywhere.

**Upkeep 81 was registered by an agent we are running**, not by an outside
party. It came from `A3OZPORJ...`, a fresh account funded once by the TestNet
dispenser, which deployed its own target contract and registered against it.

It is worth recording anyway, for what it does show. The whole sequence took
**29 seconds**: deploy, configure, call twice, then register on Arcron. Nothing
in it was hand-held, and nobody adjusted the docs to make it work. An agent
given the public repository was able to go from an empty account to a
serviced upkeep without asking us anything, which is the closest thing to a
test of [`integrating.md`](integrating.md) that exists so far.

What it is not is evidence of adoption. The beta gate in
[`releases.md`](releases.md) asks for an upkeep registered by somebody who is
not us, and an agent we dispatched is us. That item is still open. An earlier
version of this page claimed it was met, on the strength of the address not
matching two we had hardcoded, which is not the same question.

**Runway** is escrow divided by burn rate at TestNet's measured 2.695 s/round. It is
what the upkeep can pay for, not a promise about keeper availability.

**Short cadences come back, and they starve the same way every time.** Five
upkeeps were cancelled on 2026-08-27 for running every 28 seconds to 9 minutes,
which at the 4,000 µALGO floor costs 0.6 to 13 ALGO a day each. They were not
underfunded, they were misconfigured, and cancelling recovered their box
minimum balance. Twelve more, upkeeps 98 to 109, were registered on a 20 round
cadence and are starved today: carrying one to thirty days costs 192 ALGO, so
`fledge run topup` refuses to fund them and says to cancel instead. The lesson
did not stick, because nothing enforces it. A cadence is fixed in the box at
registration, so the only remedy is a cancel.

## rain: who can win, and how often

**Who may enter.** Holders of any asset minted by
`WGSHC4TYKYBS6EX5V5E377BQDLKWIIPBCFOLZQZIXCKHFIEKRPBFOMW25A` (`corvid.algo`).
The hub gates on the minting account only; the unit-name prefix that the
pre-hub apps also checked was removed on 2026-08-29 with the hub rewrite, so a
rain that needs a narrower collection needs a dedicated minting account. The
prize asset itself can never buy a ticket.

**What the prefix bought.** Apps `770029154` and `770030875` also required the
unit name to begin `corvid`, compared as bytes and so case-sensitive: `corvid1`
entered and `Corvid` did not. That second half was doing work. The creator
account holds 31 live assets on TestNet, of which 15 are the collection and the
rest are working-wallet leftovers called things like `asdf` and `Test`, and
gating on the creator alone sells a ticket to every one of them. The hub takes
that cost in exchange for one gate check that every rain can share.

**How a ONE draw runs.** Two phases, because the seed of a future round does
not exist when the draw is scheduled:

1. **`draw`** is the scheduled half. The upkeep calls it on cadence; it locks
   the drip and fixes a future round to read the seed of. It moves no money.
2. **`resolve`** is the other half, and **somebody has to send it**. It reads
   the committed round's block seed and picks the winner.

**The window is 800 rounds**, about 36 minutes. A block seed is only readable
for roughly 1,000 past rounds, so `SEED_WINDOW` stops short of that: a ONE rain
nobody resolves inside the window can never be resolved, and `abandon` returns
the locked drip to the pot.

That is not hypothetical. Draw 2 on the superseded app committed and then sat
**23,012 rounds** before anyone looked. It was abandoned on 2026-08-27 and its
1.98 ALGO went back to the pot. The scheduled half ran on time; the human half
did not happen. **Anyone running this needs something that sends `resolve`.**

**Odds, in ONE mode.** `draw` locks the drip against a future round's seed and
`resolve` picks one ticket uniformly, so with *n* tickets each has a 1/*n*
chance. SPLIT and WAVE have no odds: SPLIT credits every ticket an equal share
of the drip on each fire, and WAVE splits the drip across up to `wave_cap`
people who checked in that interval. A ticket is checked again at `claim` in
every mode, so passing the token on after entering does not pay.

## What is proved, and what is not

Proved on chain on 2026-08-27 against app
[`770030875`](https://testnet.explorer.perawallet.app/application/770030875),
using three assets minted from one account for the purpose: `corvid1` and
`corvid2` in the collection, and `asdf` as a decoy from the same creator.

| | |
|---|---|
| A collection token buys a ticket | **proved**: `corvid1` admitted as ticket #0 |
| A same-creator token with the wrong unit name does not | **proved**: `asdf` refused with "Wrong collection" |
| A token the creator never minted does not | **proved**: refused with "That asset is not from the collection" |
| A gated draw resolves and picks a winner | **proved**: draw 1, 2 tickets, 0.9811 ALGO prize, resolved at round 66729237 by beacon `600011887` |

The decoy is the point. `asdf` and `corvid1` came from the same account minutes
apart, and gating on the creator alone would have sold it a ticket.

**What this does not prove.** Both tickets in that draw were ours, so it
demonstrates the mechanism rather than a contested draw. And app
[`770029154`](https://testnet.explorer.perawallet.app/application/770029154) was
gated on the real `corvid.algo` collection, which nobody here holds a token
from, so its admit path was exercised only by whoever does. Both proofs are
against the pre-hub programs; the hub keeps the creator check and drops the
prefix.

## The seed is readable as soon as the round exists

`COMMIT_DELAY` is 8, so `draw` sets `commit_round` eight rounds out. Unlike the
beacon this replaced, `Block.blk_seed` answers the round after the commit
round is written: `resolve` asserts only `Global.round > commit_round`, so there
is no separate answer lag to wait out.

A resolver still has to exist, and it still has to retry on ordinary failures,
but it has 800 rounds rather than an unknown lag to work in. The draw before
the one above aged out because nobody sent `resolve` at all, not because it was
sent too early.

## Keeping this honest

The tables are generated. If a number here disagrees with the chain, the chain
is right and this file is stale; regenerate it rather than editing a figure.
