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
| ~~rain~~ | [`769988156`](https://testnet.explorer.perawallet.app/application/769988156) | superseded | The ungated version. Its last draw aged out and was abandoned; the prize returned to the pot. |

`not frozen` means the creator can still replace the programs. That is
deliberate while these iterate and it is a real power over anything escrowed;
see [`security.md`](security.md).

## The upkeeps

Read from the chain at round 66728345.

| id | target | every | escrow | runway | runs | policy |
|---|---|---|---|---|---|---|
| 19 | `769891902` pulse | 11.4 h | 0.3566 ALGO | ~42 days | 3 | catch up |
| 20 | `769891902` pulse | 11.4 h | 0.3566 ALGO | ~42 days | 3 | skip ahead |
| 21 | `769891902` pulse | 11.4 h | 0.4024 ALGO | ~48 days | 3 | skip ahead |
| 22 | `769891902` pulse | 11.4 h | 0.3566 ALGO | ~42 days | 3 | skip ahead |
| 73 | `769891902` pulse | 57 min | 0.9240 ALGO | ~9 days | 19 | skip ahead |
| 76 | `769988156` rain (superseded) | 1.9 h | 1.9600 ALGO | ~39 days | 10 | skip ahead |

**Runway** is escrow divided by burn rate at the measured 2.66 s/round. It is
what the upkeep can pay for, not a promise about keeper availability.

Nothing here is short-cadence any more. Five upkeeps were cancelled on
2026-08-27 for running every 28 seconds to 9 minutes, which at the 4,000 µALGO
floor costs 0.6 to 13 ALGO a day each. They were not underfunded, they were
misconfigured, and cancelling recovered their box minimum balance.

## rain: who can win, and how often

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

| | |
|---|---|
| The gate refuses an asset the collection did not mint | **proved on chain**, 2026-08-27, rejected with "That asset is not from the collection" |
| The gate refuses a same-creator asset whose unit name is wrong | proved in tests, each verified to fail with the check disabled |
| The gate admits a real collection token | **not yet proved on chain** — needs a `corvid` NFT in an account we hold keys for |
| A gated draw resolving and paying a holder | **not yet done** |

## Keeping this honest

The tables are generated. If a number here disagrees with the chain, the chain
is right and this file is stale; regenerate it rather than editing a figure.
