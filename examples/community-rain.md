# A rain for your holders

Gate entry to an NFT collection, pay in your own token, fill the pot, forget
it. Nobody runs it, and anybody can keep it funded.

`smart_contracts/rain/` with `scripts/community_rain_demo.py`.
`fledge run smoke-community-rain`.

## What a project creates

One hub, many rains. After `bootstrap` and (for an ASA prize)
`opt_in_prize_asset`:

```python
rain.send.create_rain(
    args=CreateRainArgs(
        mbr_payment=...,
        label=b"holders".ljust(32, b"\x00"),
        gate_creator=<the account that minted your collection>,
        prize_asset=<your token, or 0 for ALGO>,
        drip=<how much leaves the pot each fire>,
        interval_rounds=<daily is 30_857>,
        mode=0,          # SPLIT: everyone who entered
        wave_cap=0,
    )
)
```

Then register one upkeep against `draw()uint64`. WAVE (`mode=2`, `wave_cap=10`)
is Discord rain: the first ten to check in this interval split the drip. ONE
(`mode=1`) is one random ticket.

## The gate is on the creator, not the asset

A collection on Algorand is not one asset. It is many assets that share a
minting account. The entrant names an asset they hold; the contract checks
who made it.

Holding any one of the collection qualifies. Holding somebody else's NFT
does not.

## Anyone can keep it running

Holding your token costs the hub 100,000 microAlgos of minimum balance,
permanently. `opt_in_prize_asset` takes that from whoever calls it. Refilling
the pot is open through `deposit` / `deposit_asset`.
