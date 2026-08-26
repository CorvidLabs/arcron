# A draw for your holders

Gate entry to an NFT collection, pay the prize in your own token, and let a
keeper fire it on whatever cadence you like. Nobody runs it, and anybody can
keep it funded.

`smart_contracts/rain/` with `scripts/community_rain_demo.py`.
`fledge run smoke-community-rain`.

## What a project configures

```python
rain.send.configure(
    args=ConfigureArgs(
        beacon_app=<the randomness beacon for your network>,
        gate_creator=<the account that minted your collection>,
        prize_asset=<your token, or 0 for ALGO>,
    )
)
```

Then register an upkeep against `draw()uint64` at whatever interval you want.
Rounds are about 2.8 seconds, so:

| Cadence | Interval |
|---|---|
| hourly | 1,286 rounds |
| daily | 30,857 rounds |
| weekly | 216,000 rounds |
| monthly | 925,714 rounds |

Arcron's ceiling is 1,000,000,000 rounds, so a yearly draw is expressible too.
A draw is best registered with `SKIP_AHEAD`: nobody wants yesterday's draw run
today, and catching up a missed month would open several draws in a row.

## The gate is on the creator, not the asset

The part worth understanding. **A collection on Algorand is not one asset.** It
is many assets that share a minting account, so "do you hold my collection"
cannot be answered by comparing an asset id.

So the entrant names an asset they hold, and the contract checks who made it:

```python
assert Txn.sender.is_opted_in(gate_asset), "Hold a token from the collection"
assert gate_asset.balance(Txn.sender) > 0, "Hold a token from the collection"
assert gate_asset.creator == gate, "That asset is not from the collection"
```

Holding any one of the collection qualifies. Holding somebody else's NFT does
not, which the demo proves by minting an impostor from a second account and
watching the entry fail.

This works because the entrant sends the transaction and supplies the asset
reference themselves. A scheduled call could not: it reaches only what the
keeper's transaction made available, and nothing tells a keeper which asset a
given entrant would name. That is why the gate lives on `enter` and the
scheduled call stays pure accounting.

## Anyone can keep it running

Two costs, and neither falls on the creator by design.

Holding your token costs the app 100,000 microAlgos of minimum balance,
permanently. `opt_in_prize_asset` takes that from whoever calls it. Refilling
the pot is open to anyone through `deposit_asset`.

A draw only its creator can fund stops the day they lose interest, and a
schedule that depends on one person is the thing this is supposed to replace.
In the demo, a passer-by pays for the opt-in **and** fills the pot.

## The one asymmetry worth knowing

An ALGO pot pays for its own bookkeeping. Each draw reserves one allocation
box out of the pot and returns it on claim.

An asset pot cannot: it is counted in token units, and the box costs ALGO. So
for an asset draw the box comes from the app account and the freed minimum
balance stays there rather than being recycled into a pot it cannot be added
to. Nothing is stranded, but **the app account needs enough ALGO to cover one
allocation box per unclaimed prize**.

This was a real bug before it was a design note: `draw` originally took the
reserve out of the pot unconditionally, which for a 5,000 token pot meant
comparing 5,000 tokens against 18,900 microAlgos and quietly declining to draw.

## What it does not do

No per-holder entry limit. Buying two tickets doubles your odds and costs two
box minimum balances, which is the honest version of "one entry per person" on
a chain where making another account is free.

Gating on a collection narrows that, but not to "one entry per NFT you hold",
which is what this said before and what the contract never enforced. A ticket
is a box that never expires, and the gate is only asked when the ticket is
bought, so **one NFT walked through ten accounts buys ten permanent tickets**.

What stops that being worth anything is that the gate is asked a second time
at `claim`: the winner has to still hold a token from the collection. Whoever
walked the NFT through ten accounts only holds it in the last one, so the
other nine tickets cannot be collected on.

The rule that follows is worth stating to your community up front, because it
is a real one: **you must still hold a token from the collection when you
collect.** Someone who wins and then sells before claiming forfeits, and the
contract cannot tell that from someone who sold to dodge the gate.
