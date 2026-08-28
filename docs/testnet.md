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
| **rain** | [`770029154`](https://testnet.explorer.perawallet.app/application/770029154) | live, **not frozen** | A scheduled prize draw, gated on an NFT collection. Deployed 2026-08-27. |
| **rain (gate test)** | [`770030875`](https://testnet.explorer.perawallet.app/application/770030875) | live | The same contract gated on a collection we hold keys for, so the gate can be proved from both sides. Not the dogfood. |
| ~~rain~~ | [`769988156`](https://testnet.explorer.perawallet.app/application/769988156) | superseded | The ungated version. Its last draw aged out and was abandoned, the prize returning to the pot, and the upkeep that drove it was cancelled and re-registered against the gated app. |

`not frozen` means the creator can still replace the programs. That is
deliberate while these iterate and it is a real power over anything escrowed;
see [`security.md`](security.md).

## The upkeeps

Read from the chain at round 66,755,175.

| id | target | every | escrow | runway | runs | policy |
|---|---|---|---|---|---|---|
| 19 | `769891902` pulse | 11.5 h | 0.3486 ALGO | ~42 days | 5 | catch up |
| 20 | `769891902` pulse | 11.5 h | 0.3486 ALGO | ~42 days | 5 | skip ahead |
| 21 | `769891902` pulse | 11.5 h | 0.3943 ALGO | ~47 days | 5 | skip ahead |
| 22 | `769891902` pulse | 11.5 h | 0.3486 ALGO | ~42 days | 5 | skip ahead |
| 79 | `770029154` rain (gated) | 1.9 h | 7.9300 ALGO | ~64 days | 7 | skip ahead |
| 81 | `770041460` (not ours) | 58 min | 1.5300 ALGO | ~6 days | 15 | skip ahead |
| 82 | `769891902` pulse | 58 min | 3.5000 ALGO | ~14 days | 0 | skip ahead |

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

**Upkeep 81 is not ours.** It was registered on 2026-08-27 by
`A3OZPORJ…`, against a contract that account deployed itself, and it has been
serviced 14 times since. It is the first upkeep in this project's history
created by somebody other than us, and it is the beta gate item that had been
outstanding from the beginning. Nobody asked us for anything to make it work.

**Runway** is escrow divided by burn rate at TestNet's measured 2.695 s/round. It is
what the upkeep can pay for, not a promise about keeper availability.

Nothing here is short-cadence any more. Five upkeeps were cancelled on
2026-08-27 for running every 28 seconds to 9 minutes, which at the 4,000 µALGO
floor costs 0.6 to 13 ALGO a day each. They were not underfunded, they were
misconfigured, and cancelling recovered their box minimum balance.

## rain: who can win, and how often

**The comparison is case-sensitive, deliberately.** `corvid1` enters and
`Corvid` does not. Unit names are bytes and the AVM compares them as bytes, so
folding case would mean lowercasing on chain for the sake of one asset in a
collection of fifteen. Anyone minting into a gated collection needs to know that
capitalisation is part of the name, not decoration.

**Who may enter.** Holders of an asset minted by
`WGSHC4TYKYBS6EX5V5E377BQDLKWIIPBCFOLZQZIXCKHFIEKRPBFOMW25A` (`corvid.algo`)
whose unit name starts with `corvid`.

Both halves matter. That account holds 31 live assets on TestNet, of which 15
are the collection and the rest are working-wallet leftovers called things like
`asdf` and `Test`. Gating on the creator alone sold a ticket to every one of
them. The comparison is on bytes and so case-sensitive: `corvid1` enters,
`Corvid` does not.

**How a draw runs.** Two phases, because an Arcron inner call cannot reach the
randomness beacon:

1. **`draw`** is the scheduled half. The upkeep calls it on cadence; it snapshots
   the ticket count and fixes a future beacon round. It moves no money.
2. **`resolve`** is the other half, and **somebody has to send it**. It reads the
   beacon for the committed round and picks the winner.

**The window is 1,000 rounds**, about 45 minutes. The Foundation beacon retains
roughly 1,512, so a draw nobody resolves inside that window can never be
resolved: `abandon` reopens it and returns the prize to the pot.

That is not hypothetical. Draw 2 on the superseded app committed and then sat
**23,012 rounds** before anyone looked. It was abandoned on 2026-08-27 and its
1.98 ALGO went back to the pot. The scheduled half ran on time; the human half
did not happen. **Anyone running this needs something that sends `resolve`.**

**Odds.** One ticket per entry, uniform over the snapshot. With *n* tickets in a
draw every ticket has a 1/*n* chance. A ticket is checked again at `claim`, so
passing the token on after entering does not pay: `enter` asks whether you hold
one, and so does the payout.

## What is proved, and what is not

Proved on chain on 2026-08-27 against app
[`770030875`](https://testnet.explorer.perawallet.app/application/770030875),
using three assets minted from one account for the purpose: `corvid1` and
`corvid2` in the collection, and `asdf` as a decoy from the same creator.

| | |
|---|---|
| A collection token buys a ticket | **proved** — `corvid1` admitted as ticket #0 |
| A same-creator token with the wrong unit name does not | **proved** — `asdf` refused with "Wrong collection" |
| A token the creator never minted does not | **proved** — refused with "That asset is not from the collection" |
| A gated draw resolves and picks a winner | **proved** — draw 1, 2 tickets, 0.9811 ALGO prize, resolved at round 66729237 by beacon `600011887` |

The decoy is the point. `asdf` and `corvid1` came from the same account minutes
apart, and gating on the creator alone would have sold it a ticket.

**What this does not prove.** Both tickets in that draw were ours, so it
demonstrates the mechanism rather than a contested draw. And the dogfood app
[`770029154`](https://testnet.explorer.perawallet.app/application/770029154) is
gated on the real `corvid.algo` collection, which nobody here holds a token
from, so its admit path is exercised only by whoever does.

## The beacon is slower than the delay suggests

`BEACON_DELAY` is 8, so `draw` sets `commit_round` eight rounds out. **That is
when the round exists, not when the beacon will answer for it.** A `resolve` one
round past the commit failed inside the beacon's own assert; the same call
succeeded eleven rounds past it, nineteen rounds after `draw`.

So a resolver that fires once at the delay and gives up is a resolver that never
resolves anything. It has to retry, and it has the full 1,000-round window to do
it in. That is the difference between the draw above and the one before it,
which nobody retried and which aged out unresolvable.

## Keeping this honest

The tables are generated. If a number here disagrees with the chain, the chain
is right and this file is stale; regenerate it rather than editing a figure.
