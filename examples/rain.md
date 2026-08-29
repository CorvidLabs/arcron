# Rain: a hub of scheduled drops

Anyone opens a rain. Arcron fires the ones that are due. Holders pull what
they are owed.

`smart_contracts/rain/` is the hub. Corvid Labs uses it for Corvid NFT and
Corvid ASA rains; anyone else uses it for theirs.

Gating and paying in a token: [a rain for your holders](community-rain.md).

## The shape

```
bootstrap()     creator funds the app floor, once
create_rain()   anyone opens a rain: gate, prize, drip, interval, mode
enter()         one ticket per account per rain
gm()            WAVE check-in, first N this interval
deposit()       anyone adds ALGO (or deposit_asset)
draw()          ZERO ARGS, the call Arcron makes. Fires due rains. Moves nothing.
resolve()       ONE mode, after the committed round's block seed
claim()         pull credited rain
```

Three modes, picked at create:

| Mode | Who is paid each fire |
|---|---|
| SPLIT (0) | Everyone who entered, equal share of `drip` |
| ONE (1) | One random ticket, from that round's block seed |
| WAVE (2) | Up to `wave_cap` people who checked in this interval |

The scheduled call does accounting only. That is not an aesthetic choice:

- An Arcron inner call reaches only what the keeper's own transaction makes
  available. `draw` opens a few rain boxes and writes them. It does not pay
  anyone, and it does not read a randomness beacon.
- A push to a closed account would fail the whole execution.

So money is **pulled** by the party who wants it.

## Cadence

Rounds are about 2.8 seconds:

| Cadence | Interval |
|---|---|
| hourly | 1,286 rounds |
| daily | 30,857 rounds |
| weekly | 216,000 rounds |
| monthly | 925,714 rounds |

Register the hub's `draw()uint64` upkeep at least as often as the shortest
rain you care about, `SKIP_AHEAD`. Each rain still enforces its own interval.

## Running it

```
fledge run smoke-rain
```
