# Arcron console

The web front end for the Arcron keeper network: a public dashboard of the
upkeep registry, plus a keeper console for registering, funding, executing and
cancelling upkeeps.

Angular (standalone components, signals, zoneless) + Bun + algosdk, styled
entirely on the CorvidLabs design system, a private repository vendored here
under `web/public/brand/`,
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

bunx playwright install chromium   # once per machine
bunx playwright test               # what the page actually renders
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

## Three destinations

| Route | What it is |
|---|---|
| `/` | The registry and the keeper board, as two tabs. |
| `/u/:id` | One upkeep: everything about it, and every action anyone can take on it. |
| `/register` | The register form. Registering ends on `/u/:id` for the upkeep just created. |

Three, not five. An earlier plan copied NFDomains' five destinations onto a
registry of five rows; `docs/console-plan.md` defers the other four until
something needs a fourth reading. `routes.test.ts` asserts the count, because
the count is the decision.

`?network=` and `?app=` describe which chain and which registry the page is
showing, so they belong to every destination. The router preserves them on
every link by default (`routes.ts`), and `app.ts` keeps the address bar in
step with what is on screen, so the URL is always the shareable link.

Because the console routes on the path rather than on a hash, a static host
needs `index.html` as its 404 page or a cold load of `/u/21` will not resolve.
`fledge run web-build-hosted` writes that copy.

Focus moves to the routed region on every navigation but the first. A router
that does not move focus is a router that breaks screen readers.

## Two views of the same state

**Registry** is for someone watching their own upkeeps: every upkeep, its
cadence, escrow and runway, with execute per row and a link to each upkeep's
own page.

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

## Quarantine: a link naming an app that is not this deployment

The console is meant to spread by people sending each other links, and a link
is the only attack this product has. The ABI and the box layout are public, so
anyone can deploy a look-alike keeper: it shows the same registry, accepts the
same register form, and keeps whatever is escrowed in it. `?app=` is a query
parameter, so a link can point the console at one.

The console knows the app id it ships pointing at, per network
(`defaultAppId` in `js/src/networks.ts`). Any other id on a network that has
one is **quarantined**, which is three things and not a paragraph:

1. An unmissable panel above everything, naming both ids, with one button back
   to the real deployment.
2. Every money button dead until the visitor explicitly continues. That is one
   gate, `canCommitMoney` in `arcron.service.ts`, which every button and
   `KeeperService.send` already key on.
3. The id is **never written to `localStorage`** (`entry.ts::storeAppId`), so
   the poison cannot outlive the visit. Continuing anyway is not written down
   either: it says "show me this app now", not "open here next time", and a
   reload asks again. Whatever the visitor had remembered before the link
   arrived is left alone rather than overwritten.

**LocalNet is not quarantined.** It records no published deployment, the app
is whatever you just deployed, and the node is on your own machine, so a link
cannot aim it at anything an attacker controls. It gets the trust banner's
"no published app is recorded" warning and keeps working. A developer running
their own TestNet deployment continues once per visit; that escape hatch is
deliberate rather than silent, which is the whole design.

`web/src/app/core/quarantine.test.ts` pins all of it, against the real
predicate and the real writer rather than copies of them.

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
src/app/
  routes.ts          the three destinations, and the query-parameter policy
  pages/             one component per destination
src/app/core/
  entry.ts           where the console opens: link, then memory, then default
  quarantine.ts      whether the console can vouch for the app it is pointed at
  wallets.ts         the wallet catalogue (KMD on LocalNet, five public wallets)
  wallet.service.ts  connect/disconnect/sign, use-wallet's store as signals
  arcron.service.ts  polling registry state as signals; measures the round rate
  keeper.service.ts  the four calls as UI state
  payer.service.ts   the connected account's balance, and the node's minimum fee
  affordability.ts   whether that balance covers a total, and by how much it does not
  target-test.service.ts  running the Test button, and discarding a stale verdict
  explorer.ts        block-explorer links, absent on LocalNet by design
  quarantine.ts      whether a linked app is the published one, and what that permits
src/app/pages/       one component per route: registry, upkeep, register
src/app/components/  network bar, stat tiles, registry table, keeper board, register form, activity log, trust banner, quarantine panel
e2e/
  console.pw.ts      the rendering audit: 5 page states x 4 viewports x 2 themes
  matrix.ts          which widths, which themes, which pages
  chain.ts           a keeper network that never moves, stubbed at the HTTP boundary
  collect.ts         what the browser is asked for: colours, boxes, line boxes
  audit.ts           measurements in, ranked findings out
  baseline.ts        what is already wrong, and what counts as worse
  baseline.json      each accepted finding, its measurement and why it stands
  report.ts          the consolidated report, and the stale-baseline check
scripts/
  dev.ts             poke rounds / seed hour- and day-cadence upkeeps on LocalNet
  localnet-txns.ts   drive the transaction builders headlessly against LocalNet
  wallet-kmd-e2e.ts  drive a real transaction through use-wallet, headlessly
  serve-static.ts    serve a built console with the single-page fallback
  write-baseline.ts  re-record e2e/baseline.json from the last run
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

axe-core is necessary and nowhere near sufficient. It reported zero violations
on a console whose disabled Register and Execute buttons were rendering at
1.02:1, because it does not resolve a CSS custom property through a cascade
and ask what colour a control ended up. That is what the suite below is for.

## What the page actually renders

`bunx playwright test` (or `fledge run web-render`) builds the console, serves
it, and audits it in a real browser at 390, 768, 1280 and 1920, in both themes,
across all three routes plus the Keeper board tab and the quarantined state.
Forty page states, about fifteen seconds including the build.

It needs nothing running. `e2e/chain.ts` answers algod at the HTTP boundary
with a fixed registry (one upkeep due, one scheduled, one starved, one
escalating, one with an ASA bonus), so the round number never moves and a
TestNet outage cannot turn it red. Everything above that boundary is the real
code: algosdk, the poll in `ArcronService`, the ARC-4 box decoder, the
escalation arithmetic.

It asserts **properties**, not pixels. Diffing screenshots produces a suite
that fails on every legitimate change and teaches people to press "accept";
these are measurements with a name and a number attached:

| rule | what it measures |
| --- | --- |
| `overflow` | `scrollWidth` against `clientWidth`, plus every element reaching past the viewport outside a scroller |
| `contrast` | the WCAG ratio from `getComputedStyle`, for every interactive control **in every state including disabled**, walking up for the real painted background |
| `text-size` | every rendered text node below a 14px floor |
| `touch-target` | controls under 44x44 CSS px at phone widths (WCAG 2.5.5) |
| `clip` | content cut off by an ancestor that hides its overflow |
| `overlap` | two controls that both take clicks in the same place |
| `table-cell` | a `td` the table is not laying out as a cell |

The ratio arithmetic is `src/app/core/contrast.ts`, unit-tested under
`bun test`, so the browser suite and the unit tests share one implementation.

Screenshots and a ranked `findings.md` land in `e2e/__screenshots__/` on every
run, passing or failing, and are attached to failures. They are the part that
catches what no rule thought to look for: the broken last column of the
registry table was found by looking at one, and only then turned into the
`table-cell` rule.

**`e2e/baseline.json`** records what is wrong today and is not being fixed yet,
which is the type scale and the touch targets, each with its measurement and
the reason it stands. A new finding fails the run, a recorded one getting worse
fails the run, and a recorded one that stopped happening fails the run too,
because a licence nothing uses any more is a licence to regress. Regenerate it
with `bun run scripts/write-baseline.ts`, and only after reading what changed.
