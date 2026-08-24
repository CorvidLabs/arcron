# Examples

Two ways to use Arcron, the keeper network, depending on which side you're on
— plus `minimal_target.py`, the smallest contract Arcron can drive, for
copying into your own project. The full integration guide is
[`../docs/integrating.md`](../docs/integrating.md).

## Automate your app (register an upkeep)

`register_upkeep.py` — the minimal client flow: attach to the canonical
TestNet keeper app, escrow ALGO, point it at a method on your app.

```bash
cp .env.testnet.template .env.testnet   # add DEPLOYER_MNEMONIC (TestNet throwaway)
poetry run python -m examples.register_upkeep
```

Edit the constants at the top of the script for your app id, method, interval
and funding.

**Writing the method it calls?** See
[`../docs/integrating.md`](../docs/integrating.md) — the hook shape,
authorization, the failure modes that quietly stop your upkeep being serviced,
escrow sizing, and the pull pattern. `minimal_target.py` here is a complete
contract to copy.

The short version: a NoOp ABI method taking no arguments of its own, which
returns rather than fails when there is nothing to do, and authorizes
`Txn.sender == <keeper app address>` unless being permissionless is a
deliberate choice. Keep the escrow topped up — anyone can `top_up`, only the
creator can `cancel` and reclaim the remainder.

## Earn fees (run a keeper bot)

`../scripts/keeper_bot.py` — scans the registry and executes due upkeeps,
collecting the per-execution fee:

```bash
poetry run python -m scripts.keeper_bot --once   # single scan (cron-friendly)
poetry run python -m scripts.keeper_bot          # loop block-by-block
```

Signs as `KEEPER_MNEMONIC` if set, else `DEPLOYER_MNEMONIC`. Each execution
costs the bot 3,000 µALGO — a 1,000 µALGO outer txn fee plus the 2,000 µALGO
`extra_fee` that covers the two inner transactions through fee pooling — and
pays `fee_per_execution` (≥ 4,000 µALGO), so servicing a due upkeep nets
≥ 1,000 µALGO.
