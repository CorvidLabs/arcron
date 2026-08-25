# Arcron console

The web front end for the Arcron keeper network: a public dashboard of the
upkeep registry, plus a keeper console for registering, funding, executing and
cancelling upkeeps.

Angular (standalone components, signals, zoneless) + Bun + algosdk, styled
entirely on the [CorvidLabs design system](https://github.com/CorvidLabs/design-system)
vendored in `public/brand/`.

## Running it

```bash
bun install
bun run ng serve      # http://localhost:4200
bun test              # decoder, ABI and formatting tests
```

It opens on **LocalNet** and needs `algokit localnet start` plus a deployed
keeper app. Running `poetry run python -m scripts.keeper_e2e --network localnet`
from the repo root deploys one and prints its id.

## How it talks to the chain

- **Reads** are permissionless: the registry is box state, so the dashboard
  works with no wallet connected on either network.
- **Signing on LocalNet** goes through KMD, which the browser can reach
  directly. Keys never leave KMD: transactions are sent there to be signed,
  so no mnemonic is ever typed into the page.
- **Signing** goes through [`@txnlab/use-wallet`](https://github.com/TxnLab/use-wallet):
  Pera, Defly, Lute, Exodus and Kibisis, none of which needs any
  configuration. The generic WalletConnect entry is the only one that wants a
  project id, so it is offered only when `window.__ARCRON__.walletConnectProjectId`
  is set, the same pattern the other CorvidLabs front ends use.
- **On LocalNet**, KMD is offered as a wallet too, so a browser can sign with
  nothing installed. Keys never leave KMD.

## Two views of the same state

**Registry** is for someone watching their own upkeeps: every upkeep, its
cadence, escrow and runway, with register/top-up/cancel.

**Keeper board** is for someone deciding what to work on: what is claimable
right now and what it pays *net of the 3,000 µALGO an execution costs*,
sortable by reward, lateness, runway or cadence, with one-click execute for a
connected wallet. Upkeeps that are stuck (escrow below one fee, so no keeper
can execute them) are shown rather than hidden, because they are the
network's failures and concealing them helps nobody.

Both work with no indexer and no backend.

### The leaderboard, and why it is not here yet

More is derivable from box state than you might expect. The board already
shows total executions, ALGO paid to keepers (`Σ times_executed ×
fee_per_execution`) and median lateness, all without a byte of transaction
history.

What is *not* derivable is **which keeper** earned it. `times_executed` is
stored per upkeep, not per keeper, so a leaderboard needs another source. So
does the number that matters most for a permissionless network: how many
distinct keepers are active. The options, and the decision:

| Option | Cost |
|--------|------|
| **Public indexer, leaderboard only** | a third-party read dependency, and rate limits |
| On-chain per-keeper stats | a box write on every execution, making the network more expensive for everyone to power a UI |
| Client-side accumulation | no dependency, but only sees rounds this browser was open for |

**Decision: a public indexer, used only for the leaderboard, and optional.**
The property worth protecting is that *we* run no backend. A public indexer
preserves that, and the board must keep working untouched when the indexer is
missing, slow or rate-limited. The leaderboard then degrades to an honest
empty state rather than breaking the page.

On-chain keeper stats are rejected for now: taxing every execution to power a
UI is a bad trade. It becomes a reasonable one only if
[#15](https://github.com/CorvidLabs/arcron/issues/15) proceeds, since staking
would give keeper reputation a mechanical use rather than a decorative one.

## Layout

```
src/app/core/
  networks.ts        LocalNet/TestNet config, genesis ids, nominal round time
  wallets.ts         the wallet catalogue (KMD on LocalNet, five public wallets)
  wallet.service.ts  connect/disconnect/sign, use-wallet's store as signals
  upkeep.ts          the Upkeep box decoder (mirrors scripts/keeper_bot.py)
  keeper-abi.ts      method signatures, checked against the ARC-56 artifact
  keeper-txns.ts     register / top_up / cancel / execute over algosdk
  arcron.service.ts  polling registry state as signals; measures the round rate
  kmd.service.ts     LocalNet signing
  keeper.service.ts  the four calls as UI state
  format.ts          ALGO amounts and rounds-as-time
src/app/core/
  board.ts           what a keeper is offered: classification, sorting, network stats
src/app/components/  network bar, stat tiles, registry table, keeper board, register form, activity log
scripts/
  dev.ts             poke rounds / seed hour- and day-cadence upkeeps on LocalNet
  localnet-txns.ts   drive the transaction builders headlessly against LocalNet
  wallet-kmd-e2e.ts  drive a real transaction through use-wallet, headlessly
```

## Units and time

Amounts read in **ALGO**; the exact µALGO figure is in the title attribute
where rounding could mislead. Round counts are also shown as human time, using
the rate measured from the chain, or Algorand's nominal 2.8 s/round before
there's enough to measure. An upkeep every 1,286 rounds reads as "every ~1 h".

LocalNet runs in dev mode, where a block is produced per transaction rather
than on a timer, so there is no rate to measure: the console labels its
schedules with the nominal rate and says `nominal` in the header.

## Accessibility

The console is checked with axe-core and must stay at zero violations:

```bash
cp node_modules/axe-core/axe.min.js public/    # gitignored
bun run ng serve
```

Then in the browser console: `await axe.run(document)` after loading
`/axe.min.js`. Check it with the registry populated and an account connected,
not just the empty state.
