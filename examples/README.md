# Examples

Two ways to use Arcron, the keeper network, depending on which side you're on.
`minimal_target.py` is here too, the smallest contract Arcron can drive, for
copying into your own project. The full integration guide is
[`../docs/integrating.md`](../docs/integrating.md).

## Worked demos

Each is a contract, a script that drives it on LocalNet against a real keeper,
and a write-up of the design decision it exists to make.

| Demo | The question it answers |
|------|-------------------------|
| [Recurring subscriptions](subscription.md) | How do you bill many subscribers when the scheduled call cannot open their boxes? |
| [Timed release](embargo.md) | How do you publish something at a moment nobody can bring forward? |
| [Daily rain](rain.md) | How do you draw a winner fairly when the keeper picks the moment? |
| [Dead man's switch](deadman.md) | What does it cost to have somebody watching, and what happens when the escrow runs dry? |

`smart_contracts/watchdog/` and `smart_contracts/treasury/` have scripts
(`watchdog_demo.py`, `treasury_demo.py`) but no write-up yet. `fledge lanes run
local` runs all six.

## Automate your app (register an upkeep)

`register_upkeep.py` is the minimal client flow: attach to the canonical
TestNet keeper app, escrow ALGO, point it at a method on your app.

```bash
cp .env.testnet.template .env.testnet   # add DEPLOYER_MNEMONIC (TestNet throwaway)
poetry run python -m examples.register_upkeep
```

Edit the constants at the top of the script for your app id, method, interval
and funding.

**Writing the method it calls?** See
[`../docs/integrating.md`](../docs/integrating.md) for the hook shape,
authorization, the failure modes that quietly stop your upkeep being serviced,
escrow sizing, and the pull pattern. `minimal_target.py` here is a complete
contract to copy.

The short version: a NoOp ABI method taking no arguments of its own, which
returns rather than fails when there is nothing to do, and authorizes
`Txn.sender == <keeper app address>` unless being permissionless is a
deliberate choice. Keep the escrow topped up. Anyone can `top_up`; only the
creator can `cancel` and reclaim the remainder.

## Earn fees (run a keeper bot)

`../scripts/keeper_bot.py` scans the registry and executes due upkeeps,
collecting the per-execution fee:

```bash
poetry run python -m scripts.keeper_bot --once   # single scan (cron-friendly)
poetry run python -m scripts.keeper_bot          # loop block-by-block
```

Signs as `KEEPER_MNEMONIC` if set, else `DEPLOYER_MNEMONIC`. Each execution
costs the bot 3,000 µALGO: a 1,000 µALGO outer txn fee plus the 2,000 µALGO
`extra_fee` that covers the two inner transactions through fee pooling. It
pays `fee_per_execution` (≥ 4,000 µALGO), so servicing a due upkeep nets
≥ 1,000 µALGO.
