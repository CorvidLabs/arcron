# Arcron console

The web front end for the Arcron keeper network: a public dashboard of the
upkeep registry, plus a keeper console for registering, funding, executing and
cancelling upkeeps.

Angular (standalone components, signals, zoneless) + Bun + algosdk, styled
entirely on the [CorvidLabs design system](https://github.com/CorvidLabs/design-system)
vendored in `public/brand/`.

Its address is **https://corvidlabs.xyz/arcron/console/**, which is canonical
rather than merely convenient: the contract is permissionless, anyone can build
a front end for it, and where a console is served from is the only thing that
tells one apart from a copy.

## Running it

```bash
bun install
bun run ng serve      # http://localhost:4200
bun test              # decoder, ABI and formatting tests
```

It opens on **LocalNet** and needs `algokit localnet start` plus a deployed
keeper app. Running `poetry run python -m scripts.keeper_e2e --network localnet`
from the repo root deploys one and prints its id.

## Building it for the site

`bun run ng build` is the local build and assumes it owns the domain root. The
hosted one does not, and the difference is one attribute:

```bash
fledge run web-build-hosted    # ng build --base-href /arcron/console/ --output-path dist/hosted
fledge run web-verify-hosted   # serve dist/hosted/browser at /arcron/console/, fetch every file
```

Every asset reference the console emits is relative, so the `<base href>` is
the only thing that knows the path, and a wrong one is invisible until the page
is served from somewhere other than `/`. `web-verify-hosted` is what makes that
visible, and both are in the `ci` lane.

Staging it into a checkout of `CorvidLabs/site`, which is what deploys it:

```bash
fledge run site-console -- --site ../../site
```

That writes into the site's `public/arcron/console/` and stops. It does not
commit and does not push, because publishing is a person's decision, not a
script's. See [`scripts/publish_console.py`](../scripts/publish_console.py).

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

Everything framework-independent lives one directory up, in `js/`, and the
console consumes it as a package. Only the Angular half is here.

```
../js/src/
  networks.ts        LocalNet/TestNet config, genesis ids, nominal round time
  upkeep.ts          the Upkeep box decoder (mirrors scripts/keeper_bot.py)
  keeper-abi.ts      method signatures, checked against the ARC-56 artifact
  keeper-txns.ts     register / top_up / cancel / execute over algosdk
  target-test.ts     the Test button: simulate the inner call, grade its resource use
  board.ts           what a keeper is offered: classification, sorting, network stats
  format.ts          ALGO amounts and rounds-as-time
src/app/core/
  entry.ts           where the console opens: link, then memory, then default
  wallets.ts         the wallet catalogue (KMD on LocalNet, five public wallets)
  wallet.service.ts  connect/disconnect/sign, use-wallet's store as signals
  arcron.service.ts  polling registry state as signals; measures the round rate
  keeper.service.ts  the four calls as UI state
  payer.service.ts   the connected account's balance, and the node's minimum fee
  affordability.ts   whether that balance covers a total, and by how much it does not
  target-test.service.ts  running the Test button, and discarding a stale verdict
  explorer.ts        block-explorer links, absent on LocalNet by design
src/app/components/  network bar, stat tiles, registry table, keeper board, register form, activity log
scripts/
  dev.ts             poke rounds / seed hour- and day-cadence upkeeps on LocalNet
  localnet-txns.ts   drive the transaction builders headlessly against LocalNet
  wallet-kmd-e2e.ts  drive a real transaction through use-wallet, headlessly
```

## Registering, and what the Test button will not claim

The register form quotes the **whole** debit: the box deposit, the escrow, and
the three fees of the group `register` builds, each named and each saying
whether it comes back. It reads the connected account's spendable balance and
refuses rather than opening a wallet for a registration that cannot be paid
for, with a re-check control so an account funded from elsewhere is never stuck
behind a stale read.

Before any of that it can **simulate the call**, from the keeper application's
own account, exactly as a keeper's inner call arrives. Free, unsigned, and
possible before any upkeep box exists. What it certifies is narrow and stated
on the page:

- the method exists on the target,
- the target accepts a call arriving from the keeper app's account,
- it stays inside the opcode budget a single call gets (a real execution is
  handed more, never less).

It deliberately **never shows a flat pass**, because a standalone simulate has
all 8 of the AVM's references to itself while a real execution has already
spent two on the upkeep box and the target app. A target needing seven
references passes a naive simulate and is then permanently unexecutable, after
the creator has escrowed. So resource use is graded instead:

| Extra references | Reading |
|---|---|
| 0 | any keeper can service it |
| 1 to 4 | inside what `scripts/keeper_bot.py` attaches today |
| 5 to 6 | allowed by the protocol; refused by the reference bot |
| more than 6 | can never execute; use [the pull pattern](../docs/integrating.md#the-pull-pattern) |

The recipe is measured, not reasoned about: `scripts/spike_simulate_test_button.py`
against `smart_contracts/sim_probe/`. Three details are load-bearing and are
commented as such in `../js/src/target-test.ts`. In particular
`extraOpcodeBudget` stays at **zero**: raising it makes a budget-exhausting
target pass here and fail on chain.

The **attestation checkbox** beside the submit button is a separate thing. It
records the person taking the risk, not the console granting permission, so it
gates submitting and never gates on the test having run or passed.

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
