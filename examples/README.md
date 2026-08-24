# Examples

Two ways to use Archon, the keeper network, depending on which side you're on.

## Automate your app (register an upkeep)

`register_upkeep.py` — the minimal client flow: attach to the canonical
TestNet keeper app, escrow ALGO, point it at a method on your app.

```bash
cp .env.testnet.template .env.testnet   # add DEPLOYER_MNEMONIC (TestNet throwaway)
poetry run python -m examples.register_upkeep
```

Edit the constants at the top of the script for your app id, method, interval
and funding. Requirements for your method (v1 call shape):

- NoOp ABI method, called with exactly one application argument — the method
  selector. Methods that take no args of their own (e.g. `tick()uint64`) fit
  naturally.
- It will be called by the keeper app, so authorize
  `Txn.sender == <keeper app address>` (or leave it permissionless, like the
  Pulse demo target).

Keep the escrow topped up: each execution pays the keeper
`fee_per_execution` from the balance. Anyone can call `top_up`; only you (the
creator) can `cancel` and reclaim the remainder.

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
