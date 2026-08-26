# Demo notes

Written 2026-08-26, from the live state at round 66702708. Check the numbers
before you present; the registry changes on its own.

## Read this first

**Do not demo `corvidlabs.xyz/arcron/console/`.** The published build is seven
merged pull requests behind `main`. On that build every disabled money button
renders at a 1.02:1 contrast ratio and is invisible, deep links 404, and a
poisoned `?app=` link works fully. Demo from `main`, locally:

```bash
git checkout main && git pull
cd web && bun run ng serve
```

Then <http://localhost:4200/>.

**Have a second terminal ready** with the snapshot, because the strongest thing
you can do in this demo is show the chain agreeing with the screen:

```bash
poetry run python -m scripts.testnet_snapshot --network testnet --app-id 769891898
```

## The one-line story

A permissionless keeper network on Algorand. You escrow ALGO to have your
contract called on a schedule, and anybody at all can earn that fee by making
the call. No token, no allowlist, no company in the middle. The contract is
live on TestNet as app `769891898`.

## What is true today, and worth saying

- The contract is deployed, verified, and **`verify_build` matches byte for
  byte**. A daily job checks that the live app is still the bytecode the
  release record claims.
- A keeper runs every thirty minutes in GitHub Actions and has been paid real
  fees.
- **An upkeep has been registered from the console by a wallet, executed by a
  stranger, and cancelled**, with the refund exact to the microalgo.
- The economics landed exactly as documented: a keeper nets **+0.001 ALGO** per
  execution, 0.004 earned against 0.003 in group fees.

## A five minute run

**1. The registry, no wallet.** Open the console. It reads box state straight
from algod: no indexer, no backend, no account. Say that out loud; it is the
unusual part.

**2. The freeze notice.** Point at it rather than skipping past it. It says the
creator can still replace the programs and that this is a power over your money.
Most projects hide this. Showing it is the pitch.

**3. The keeper board.** Same boxes, read as a keeper: what each upkeep pays
**net of what it costs to run**. That number is the whole economic argument.

**4. Test the call.** On `/register`, target `769891902`, method
`tick()uint64`. Press **Test the call** with no wallet connected. It simulates
the exact inner call a keeper would make, from the keeper app's own account,
and grades what it found. It never says a flat PASS.

That grading is the best story in the demo. A naive version returns "pass" on a
target that can never execute, because a standalone simulation does not pay
Arcron's own two-slot resource tax. Somebody would escrow money against an
upkeep that is permanently stuck. It was measured on a chain, not reasoned
about: `scripts/spike_simulate_test_button.py`.

**5. The cost.** Fill the form and show the up-front cost breakdown: box
deposit, escrow, network fees, separated, with the refundable part named. If you
sign, compare it against what the wallet asks. It matched to the microalgo when
this was last done, and it was wrong the same morning.

## What will go wrong, and what to say

**A 403 from the node.** The public algod endpoint rate limits, and it happened
twice while writing these notes. The console says so plainly and tells you not
to escrow anything until it recovers. If you hit it: *"that is a public node
rate limiting us, and the console is refusing to show stale numbers as if they
were current"*. Reload and it clears.

**Four upkeeps read STARVED** (18, 23, 51, 52). Do not hide it, it is a good
story. Upkeep 18 is a `catch-up` upkeep on a 70 second cadence that went
unserviced for seventeen hours: it burned its entire escrow replaying missed
intervals, one fee each, and advanced 41 rounds against a 23,478 round backlog
before running out. That is the contract doing exactly what `catch-up` promises,
and it is the clearest possible argument for why the register form now warns
about that policy.

**The layout.** Text is small, it does not use the full width, and it is not
mobile responsive. Known, measured, being worked. Say so before somebody asks.

**Deep links.** `/u/71` works locally. It 404s on the published site until the
nginx fallback lands.

## Do not claim

- **Not audited.** Nobody outside has reviewed the contract. Four independent AI
  reviews are not an audit and should not be described as one.
- **Not on MainNet, and not soon.** The gates are 30 days of beta then 60 days
  at rc with the bytecode unchanged. Roughly 90 days minimum, and the clock
  started today.
- **Not frozen.** The creator can still replace the programs. That is deliberate
  and it is on screen.
- **No outside users yet.** Every upkeep in that registry is ours.
- **No SLA.** There cannot be one; that is what permissionless means. A
  neglected upkeep's fee escalates, and that is the whole mechanism.

## If asked

**"What stops a keeper stealing the escrow?"** Escrow leaves only as a keeper
fee or a creator refund. Every payment in the contract has one of two
receivers, and the e2e drains one upkeep on chain and asserts its neighbours are
untouched.

**"What if nobody runs a keeper?"** Then nothing happens and the money stays
yours. A neglected upkeep's fee escalates toward a ceiling the creator sets, so
it gets more attractive the longer it waits.

**"What does a keeper earn?"** +0.001 ALGO per execution at the minimum fee.
Thin on purpose: competition is what holds the price down, and the creator sets
the ceiling.

**"What if two keepers race?"** The loser pays nothing. Algorand rejects a
failing transaction at validation rather than including it. Worth being precise:
this is proven by the e2e and has **never been observed between two real
keepers**, because until today only one keeper existed. That is being fixed.

**"Why is it upgradeable?"** Because it is alpha and a bug should be fixable
without asking everyone to cancel and re-register. It was exercised for real
today: alpha-3 went out as an update in place, and every box survived. Freezing
is a decision that has to be recorded before release candidate, either way.
