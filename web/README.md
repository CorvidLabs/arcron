# Archon console

The web front end for the Archon keeper network: a public dashboard of the
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
keeper app — `poetry run python -m scripts.keeper_e2e --network localnet` from
the repo root deploys one and prints its id.

## How it talks to the chain

- **Reads** are permissionless: the registry is box state, so the dashboard
  works with no wallet connected on either network.
- **Signing on LocalNet** goes through KMD, which the browser can reach
  directly. Keys never leave KMD — transactions are sent there to be signed,
  so no mnemonic is ever typed into the page.
- **Signing** goes through [`@txnlab/use-wallet`](https://github.com/TxnLab/use-wallet):
  Pera, Defly, Lute, Exodus and Kibisis, none of which needs any
  configuration. The generic WalletConnect entry is the only one that wants a
  project id, so it is offered only when `window.__ARCHON__.walletConnectProjectId`
  is set — the same pattern the other CorvidLabs front ends use.
- **On LocalNet**, KMD is offered as a wallet too, so a browser can sign with
  nothing installed. Keys never leave KMD.

## Layout

```
src/app/core/
  networks.ts        LocalNet/TestNet config, genesis ids, nominal round time
  wallets.ts         the wallet catalogue (KMD on LocalNet, five public wallets)
  wallet.service.ts  connect/disconnect/sign, use-wallet's store as signals
  upkeep.ts          the Upkeep box decoder (mirrors scripts/keeper_bot.py)
  keeper-abi.ts      method signatures, checked against the ARC-56 artifact
  keeper-txns.ts     register / top_up / cancel / execute over algosdk
  archon.service.ts  polling registry state as signals; measures the round rate
  kmd.service.ts     LocalNet signing
  keeper.service.ts  the four calls as UI state
  format.ts          ALGO amounts and rounds-as-time
src/app/components/  network bar, stat tiles, registry table, register form, activity log
scripts/
  dev.ts             poke rounds / seed hour- and day-cadence upkeeps on LocalNet
  localnet-txns.ts   drive the transaction builders headlessly against LocalNet
  wallet-kmd-e2e.ts  drive a real transaction through use-wallet, headlessly
```

## Units and time

Amounts read in **ALGO**; the exact µALGO figure is in the title attribute
where rounding could mislead. Round counts are also shown as human time — an
upkeep every 1,286 rounds is "every ~1 h" — using the rate measured from the
chain, or Algorand's nominal 2.8 s/round before there's enough to measure.

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
