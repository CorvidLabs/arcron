# arcron

[![CI](https://github.com/CorvidLabs/arcron/actions/workflows/ci.yml/badge.svg)](https://github.com/CorvidLabs/arcron/actions/workflows/ci.yml)
[![Keeper bot](https://github.com/CorvidLabs/arcron/actions/workflows/keeper-bot.yml/badge.svg)](https://github.com/CorvidLabs/arcron/actions/workflows/keeper-bot.yml)
[![Release drift](https://github.com/CorvidLabs/arcron/actions/workflows/release-drift.yml/badge.svg)](https://github.com/CorvidLabs/arcron/actions/workflows/release-drift.yml)

> **New here?** [`START-HERE.md`](START-HERE.md) is the front door. It is for
> people trying it, for agents attacking it, and for anyone deciding whether the
> idea is any good.

**Arcron** is a permissionless keeper network for Algorand. Anyone registers
a scheduled contract call, and any keeper executes it for the fee. By
[CorvidLabs](https://github.com/CorvidLabs), built with Algorand Python
(Puya) and AlgoKit.

*ARC and cron: Algorand's standards, and the scheduler everyone already knows.
The job matters, and whoever runs it does not.*

**Why this exists:** every serious chain should have a way to say *"call this
later"* without requiring a server, and Algorand does not have one. There is no
ARC for scheduled execution and never has been. [`docs/why.md`](docs/why.md)
makes the case at about a seventh the cost of the cheapest paid host, says
where it stops being true (above ~10 upkeeps, run your own bot), and states
plainly what would prove it wrong. [`docs/testnet.md`](docs/testnet.md) is what
is deployed and what the registry is doing, read from the chain rather than
remembered.

> [!WARNING]
> **Unaudited, unfrozen, and TestNet only.** No third party has reviewed this
> contract. There have been five rounds of adversarial review, four of them by
> language models rather than by people, which is a useful thing and is not an
> audit; nobody has paid a firm to look at this. The
> deployment is also still **upgradeable**: `frozen` is `0` today, so its
> creator can replace the programs under escrow that is already there. That
> cuts both ways. A bug can be fixed in place, and the rules can be changed
> after you have committed funds. Calling `freeze` gives up both, permanently,
> and `frozen` is global state, so anyone can check which of the two any
> deployment is:
> ```
> poetry run python -m scripts.govern status --network testnet --app-id 769891898
> ```
> **What that means if you are thinking of using it:** escrow only what you can
> afford to lose, on TestNet, and re-read that status before you scale up.
> Nothing here is a promise yet. [`docs/security.md`](docs/security.md) has the
> threat model, the accepted risks, and what happens if a bug is found.
>
> Apps [`769823086`](https://testnet.explorer.perawallet.app/application/769823086),
> [`769802474`](https://testnet.explorer.perawallet.app/application/769802474)
> and [`769772891`](https://testnet.explorer.perawallet.app/application/769772891)
> are **superseded** and should not be used. `769823086` is alpha-1: it predates
> governance, it has no update path, and it is the one most likely to be linked
> from somewhere stale. The other two predate the 1.0 contract, and their box
> encoding is a different shape that current tooling refuses to decode rather
> than misread.
>
> Check what any deployment is actually running. It compares compiled
> bytecode, not source text:
> ```
> poetry run python -m scripts.verify_build --network testnet --app-id 769891898
> ```

## What is in this repository

One product: a keeper registry, the targets and instruments that prove things
about it, and the tooling to run, verify, govern and document it.

| Contract | What it is | Status |
|----------|-----------|--------|
| [`smart_contracts/keeper`](smart_contracts/keeper/contract.py) | The Arcron network: upkeep scheduling with ALGO escrow and keeper rewards | **Live on TestNet**, app [`769891898`](https://testnet.explorer.perawallet.app/application/769891898) |
| [`smart_contracts/pulse`](smart_contracts/pulse/contract.py) | The demo target: a heartbeat counter that cannot fail, called with and without arguments | Live on TestNet, app [`769891902`](https://testnet.explorer.perawallet.app/application/769891902) |
| [`smart_contracts/subscription`](smart_contracts/subscription/contract.py) | The teaching example: recurring billing done as pull payment, which is the shape [`docs/integrating.md`](docs/integrating.md) argues for | Never deployed on a public network. Exercised on LocalNet only |
| [`smart_contracts/resource_probe`](smart_contracts/resource_probe/contract.py) | An instrument: a target that deliberately reaches for an account, an asset and an app nobody handed it, to establish what an argument-free inner call may touch | LocalNet only |
| [`smart_contracts/sim_probe`](smart_contracts/sim_probe/contract.py) | An instrument: targets that pin where `simulate` stops predicting a real `execute`, which is what the console's Test button rests on | LocalNet only |

Around them:

| | What it is |
|---|---|
| [`web/`](web/) | The console: registry dashboard plus keeper controls. Published at [`corvidlabs.xyz/arcron/console/`](https://corvidlabs.xyz/arcron/console/) |
| [`web-keeper/`](web-keeper/) | A local dashboard for somebody running a keeper. Never published; `tests/test_keeper_ui_stays_local.py` keeps it that way |
| [`web-govern/`](web-govern/) | A local page for the creator account. Never published: it is the one surface that can reach MainNet |
| [`js/`](js/) | `@corvidlabs/arcron`: box decoder, ABI, transaction builders, the keeper's view of the board |

**Rain, the first thing built on this, is no longer here.** It moved to
[CorvidLabs/arcron-rain](https://github.com/CorvidLabs/arcron-rain) on
2026-08-31 with its contract, spec, tests, bot, client and page. It is still
the best answer to "what is this for": a hub of scheduled prize draws, woken by
upkeep 113 on this registry, whose whole schedule is somebody else's problem.
We wrote it, so it is a dogfood rather than adoption, but it is the largest
thing that has had to follow [`docs/integrating.md`](docs/integrating.md) all
the way through. The reasoning for the split, and what it cost this repository,
is in
[`docs/design/split.md`](docs/design/split.md).

**Building on it?** [`docs/integrating.md`](docs/integrating.md) is the whole
integration story in one pass: the hook shape, authorization, the failure
modes that stop your upkeep being serviced, and the pull pattern everything
here is built on. Integration is usually one zero-argument method.

## The keeper network

Smart contracts can't wake themselves. Everything time-based on Algorand
(vesting unlocks, subscription charges, prize settlements, limit orders,
"execute this after the vote") needs *someone* to poke the chain. EVM chains
have Chainlink Automation and Gelato; Algorand had nothing productized. This
is that missing piece.

**How it works:**

1. **Register.** Anyone calls `register` with a target app, call data
   (typically a method selector), a round interval, and a per-execution fee,
   escrowing ALGO in the contract (one box per upkeep).
2. **Execute.** Once the due round passes, *any* account can call `execute`.
   The contract performs the registered call as an inner app call and pays
   the keeper from the escrow. Both happen atomically, so the fee is only
   paid if the upkeep actually ran.
3. **Top up / cancel.** Anyone can add funding; only the creator can cancel,
   reclaiming the remaining escrow plus the box MBR the deletion releases.

Permissionless, no protocol rake, no token required. Escrow is plain ALGO, so
any group can use it. Upkeep records are `arc4.Struct`s in boxes, so reading
the registry is a free algod query. **Not ownerless yet**: the creator can
`update` and `freeze`, and this deployment's `frozen` is `0`, so it becomes
ownerless when `freeze` is called and not before. See the warning above.

**Constraints (v1):** registered calls are NoOp app calls carrying up to three
app args, counting the selector, which is enough for an ARC-4 method of arity
two. The zero-argument "tick/settle/harvest" hook is still the common shape.
An upkeep declares no foreign arrays, and does not need to: a keeper that
simulates before executing discovers what the inner call touches and attaches
the references. The Python bot does this by simulating the call and naming the
references itself, because algokit-utils' default populator caps at four
*account* references per transaction and refuses a fifth, and
the TypeScript client (`js/src/keeper-txns.ts`, which the console also
imports) does it too, so an upkeep whose target reaches an account, asset or
app beyond the target itself is servable from either.
Fees ≥ 4000 µALGO (keepers pay ~3000 µALGO in group fees per execution; the
console suggests 10,000). A post-quantum keeper pays the same today, because
Algorand's per-byte fee is zero; see [`docs/hosting.md`](docs/hosting.md).
Interval ≥ 10 rounds.

### Who actually runs it?

Nobody, and that is the point. There is no on-chain timer. A smart contract
cannot wake itself, so every execution is a transaction somebody sent. What
Arcron adds is that "somebody" can be *anyone*, and that they are paid
atomically for it: the fee moves only alongside a real execution, so keepers
need no trust and creators need no relationship with them.

The practical consequence: **an upkeep runs only while at least one keeper is
watching the registry.** Reliability here is economic rather than technical.
A due upkeep is claimable revenue, so keepers compete for it. Today that means
running `scripts/keeper_bot.py` yourself, or relying on someone who does.

Funding depth and keeper liveness are separate concerns. 100 ALGO at the
4,000 µALGO minimum is ~25,000 executions, so escrow is rarely the binding
constraint. A well-funded upkeep with nobody watching still does not run.

**What happens to missed executions is the upkeep's own choice, made at
registration.** Under `CATCH_UP` (policy 0) scheduling advances from the
*scheduled* round, so an upkeep left unattended stays due and replays one
interval per execution rather than skipping its history — which also means an
outage can spend the whole escrow on replays nobody wanted. Under `SKIP_AHEAD`
(policy 1) the contract snaps to the first slot strictly in the future, keeping
the schedule's phase and dropping the backlog. Most of the live registry is
`SKIP_AHEAD`: 30 of the 32 upkeeps at round 66,901,001, with only 19 and 116 on
`CATCH_UP`. Pick deliberately; it cannot be changed afterwards.

Cadences are counted in rounds, not wall-clock time, so "daily" means "every
~30,857 rounds" and slides against the calendar. TestNet measured 2.695 s a
round over 1,000,000 rounds on 2026-08-28, against the nominal 2.8, so an
"hourly" upkeep there fires about 2.2 minutes early every hour — roughly
**27 hours a month**. [`docs/arcron.md`](docs/arcron.md) has the table and the
MainNet figure, which is smaller.

### What Arcron can and cannot do

Arcron is the clock, not the eyes.

**It cannot** fetch anything off-chain: no APIs, no RSS, no web pages, no
price feeds. Smart contracts have no network access, and `execute` fires an
inner app call to another app on the same chain. Arcron can wake your contract
up; it cannot tell it what is happening in the world. `call_args` is frozen at
registration, so keepers have no discretion over what gets called, which is
exactly why they need no trust.

**It can** call any Algorand app on a schedule, permissionlessly and without a
server: deadlines, unlocks, settlements, expiries, accrual, draws, or any
other on-chain state machine that has to advance on time.

**Paired with an oracle** it does the half that is otherwise hard. A reporter
pushes data into an oracle contract, Arcron triggers `settle()` on a cadence,
and settlement reads the stored value. Arcron does not supply the data. It
supplies the guarantee that settlement cannot be stalled, delayed or
selectively timed by an interested party.

## What is proven, and what is not

The registry has evidence. Some of the things around it do not, and the two
are worth keeping apart.

**The keeper registry has been used, and all of the use is still our own.**
Read from the chain on 2026-09-01, at round 66,900,984:

- **32 live upkeeps**, registered by **seven distinct addresses**. Our deployer
  registered 6; the other 26 came from six addresses that are not it.
- **That is not 26 outside upkeeps.** Five of those six addresses were funded
  by a single account, and [`docs/testnet.md`](docs/testnet.md) records one of
  the five (`A3OZPORJ…`) as an agent we dispatched. Treat the five as one
  operator wearing five costumes: 20 of the 26. Only the sixth address is even
  a candidate for outside registration.
- **1,069 executions** are recorded across those 32 boxes. **Two addresses
  executed** in the preceding 30,857 rounds (about a day): `NUGVPQGZ…` 231 and
  `GCQL3M7A…` 57. **Both are ours** — the long-running keeper and the GitHub
  Actions cron keeper in
  [`.github/workflows/keeper-bot.yml`](.github/workflows/keeper-bot.yml). All
  time, fifteen addresses have sent an `execute`, and `GCQL3M7A…` is the
  largest keeper this registry has had: 689 of 1,399 executions ever sent,
  against `NUGVPQGZ…`'s 636. The other thirteen have fourteen or fewer each,
  and eleven of them are the end-to-end suite rather than a keeper.
- `CEPY52VZRWFL…` is ours as well, and it was the hardest to place. Funded once
  by the public TestNet dispenser and by nothing else, it deployed its own
  target apps, registered upkeeps 110, 111, 112, 114, 115 and 116 at four
  different cadences, and runs its own keeper — taking no top-up from the
  account that funded the other five. That combination reads exactly like a
  stranger, and it was recorded here as unattributed for a day on that basis.
  It is an agent that funded itself the way any developer would. **The count of
  upkeeps registered by somebody who is not us is zero**, and every keeper that
  has executed here is one we started.
- Reproducing it: `poetry run python -m scripts.verify_build --network testnet --app-id 769891898`
  proves the deployed programs are this source, byte for byte. `fledge run
  health` prints the per-upkeep and per-keeper lines — runs, net-to-keeper,
  runway, keeper solvency — but **not** the creator attribution above, which
  has no column in it; that came from decoding every box with
  `scripts.keeper_bot._decode_upkeep` and querying the indexer. `health` also
  reads those boxes back to back, which the public TestNet endpoint will
  rate-limit into an HTTP 403; the clients retry it now
  ([`scripts/node_retry.py`](scripts/node_retry.py)), and pointing
  `ALGOD_SERVER` at a node you control is the real fix.

**What that does not prove.** Nobody has escrowed anything but test ALGO. No
third party has audited the contract, and the creator can still replace its
programs. Usability is the least tested thing here: every interaction the
system has had was with someone who already knew how it worked, which is why
[`START-HERE.md`](START-HERE.md) asks for the moment you had to guess.

**`subscription` has no such evidence, and the shape of its tests hides
exactly that.** It is a teaching example, and it is honest about being one:

- It has **never been deployed on TestNet or MainNet**. It has no
  `deploy_config.py`, so nothing in this repository's build or deploy path can
  put it on a public network at all.
- Its unit tests run against `algorand-python-testing` mocks, which **record
  inner transactions without executing them and do not enforce minimum
  balances** — which is to say, they cannot see the two failures a
  pull-payment contract is most likely to have: a payout that does not go out,
  and an account that cannot afford to hold what it was sent.
- What it does have is one real-chain run: `fledge run smoke-subscription`
  deploys it on LocalNet, has a keeper advance its billing period, settles,
  claims and withdraws with real inner payments and real MBR. That is in
  `[lanes.local]`, so it runs when a person runs the local lane. **CI's
  LocalNet job does not run it** — that job's task list is hand-written and
  names only `build` and `smoke-keeper`.

So: copy the pattern, read the docstring, and prove your own version on a
chain. Do not treat it as code that has been shown to work where money is.

**Prior art.** Somebody built a permissionless keeper network on Algorand
before this one, on an Algorand Foundation grant, and it is dead:
[`docs/why.md`](docs/why.md) states the case for the primitive and the test that
would falsify it. [`docs/prior-art.md`](docs/prior-art.md) records what BiatecCron did, what it
did differently, and the draft ARC that was never submitted. Nothing here
claims to be first.

**All of it in one read.** [`docs/book/`](docs/book/) holds the Working Guide, a
single ordered pass through everything above: concept, console, integration,
keepers, security, economics, reference. It is compiled *from* the documents it
cites and defers to them: where the guide and a doc disagree, the doc is right,
and [`tests/test_book.py`](tests/test_book.py) pins the load-bearing figures to
the files that own them so CI notices before a reader does.

## Development

Pre-requisites: Python 3.12 or 3.13, never 3.14, [AlgoKit](https://github.com/algorandfoundation/algokit-cli),
Poetry, Docker (LocalNet only), and
[fledge](https://github.com/CorvidLabs/fledge) for the lanes and the deploy
tasks below. Installing exactly the first four leaves you unable to run any
`fledge run` or `fledge lanes` command on this page.

**LocalNet is dev mode: rounds advance only when a transaction is sent.** An
upkeep with a 10-round interval will never come due on an idle LocalNet, and a
keeper bot polling it will correctly report nothing to do. Send transactions to
move the chain (`scripts/network.py` has `wait_for_round`, which pokes it for
you). This surprises everyone once.

```bash
poetry install

fledge lanes run ci         # contracts + all three consoles: build, tests, spec check, rendered-page audit
fledge lanes run local      # ci, plus everything that needs a real chain
fledge lanes run endurance  # build, tests, spec, e2e, then a soak and a populated-registry scenario
```

`fledge lanes run local` needs LocalNet up (`algokit localnet start`) and no
secrets. LocalNet accounts come from KMD, funded by its dispenser. It adds
seven chain steps to `ci`: the keeper end-to-end, the subscription demo,
governance, multisig, a real clawback, every recorded attack, and the reference
boundary. `endurance` is not a superset of `local` — it is the short build-and-
test set plus `soak` and `scenario`.

Individual tasks (all of these are in `fledge.toml`):

```bash
poetry run python -m smart_contracts build   # Puya compile + typed clients
poetry run pytest tests/ -q                  # unit tests (algorand-python-testing)
specsync check --strict                      # spec drift check
poetry run python -m scripts.keeper_e2e --network localnet   # full e2e
```

### Looking at the live deployment

Four read-only commands, none of which signs anything:

```bash
fledge run health          # what is wrong with the registry right now
fledge run clock           # how long the deployment has been the deployment
fledge run keeper-preview  # what running a keeper here would actually earn
fledge run keeper-ui       # a local dashboard, on localhost:4300
```

`health` reports upkeeps about to starve, upkeeps that pay a keeper nothing,
and whether the keepers can still afford to run. It also simulates the overdue
ones and says *why* they are overdue, because an upkeep out of escrow and an
upkeep whose target reverts read identically otherwise and only one of them is
a funding problem. It reads one box per upkeep with no pacing, so against the
public TestNet endpoint it can be rate-limited into an HTTP 403 part way
through; re-run it, or point `ALGOD_SERVER` at a node you control.
`clock` measures the MainNet hold from the application's
creation round, and refuses to count once the local build stops matching what
is deployed, because time served by code that is about to be replaced is not
evidence about the code replacing it.

`keeper-preview` answers the question a prospective keeper actually has. It
reads what the registry paid over the last day, net of what those executions
cost to send, and divides it by the keepers already there rather than quoting
the total: an arriving keeper divides the work rather than creating it. It
simulates what is due, so an upkeep whose fee has escalated to the ceiling is
not counted as money on the table when its target reverts.

One more, which plans read-only and signs only when told to:

```bash
fledge run topup            # what it would cost to carry every upkeep 30 days
fledge run topup -- --send  # actually fund it
```

It prices escrow in **days of runway** rather than microalgo, because the two
are not the same question: 0.15 ALGO is six weeks on a daily schedule and forty
minutes on a per-minute one. An upkeep whose cadence puts 30 days out of reach
is reported rather than funded, since the answer to those is to cancel them.

### The console

The console's address is **https://corvidlabs.xyz/arcron/console/**. That is
the canonical one: Arcron's contract is permissionless, so anyone can build a
front end for it, and the only thing distinguishing ours is where it is
served from. Check a link against that address before connecting a wallet to
whatever it opens.

To run it locally instead:

```bash
cd web && bun install && bun run ng serve      # http://localhost:4200
```

A dashboard of the upkeep registry (read straight from algod, so it needs no
wallet and no indexer) plus the keeper controls: register, top up, execute a
due upkeep, cancel your own. Signing goes through
[use-wallet](https://github.com/TxnLab/use-wallet): Pera, Defly, Lute, Exodus
and Kibisis, plus KMD on LocalNet so a browser can sign with nothing
installed. Amounts read in ALGO and cadences read as time
("1,286 rounds · ~1 h"). Built on the
CorvidLabs design system, which is a private repository and vendored
here under `web/public/brand/`;
see [`web/README.md`](web/README.md).

A CSS change is not reviewed until `fledge run web-render` has run. It builds
the console, serves it, and audits the *rendered* page at four widths in both
themes: overflow, WCAG contrast on computed style in every control state, text
size, touch targets, clipping, overlap. What is knowingly unfixed is recorded
in [`web/e2e/baseline.json`](web/e2e/baseline.json) with the reason each entry
stands. Run `fledge run web-render-install` once per machine first.

### Publishing the console

The public site is a separate repository, `CorvidLabs/site`, which is an Astro
build deployed to an nginx VPS: everything under its `public/` directory is
copied verbatim into the build, and pushing `main` is what deploys. So
publishing the console means staging the bundle there and pushing it, exactly
as [`scripts/sync_site_docs.py`](scripts/sync_site_docs.py) already does for
the integrator docs.

```bash
fledge run web-build-hosted     # build with --base-href /arcron/console/
fledge run web-verify-hosted    # serve it at that subpath and load every file
fledge run site-console -- --site ../../site          # stage into the checkout
fledge run site-console -- --site ../../site --check  # report drift, write nothing
```

Neither script commits or pushes. They stage; a human in the site repository
publishes.

The base href is the whole difference between a working page and one that
404s its own JavaScript, and nothing about that is visible in a build log, so
`web-verify-hosted` is in the `ci` lane: it serves the build under
`/arcron/console/` and fetches every file in it, refusing a bundle built for
the domain root.

### End-to-end on LocalNet

The unit tests run against `algorand-python-testing` mocks, which record inner
transactions without executing them and don't enforce minimum balances.
`scripts/keeper_e2e.py` covers what only a real AVM can show, and it is the
same script that runs against TestNet with `--network testnet`. It is
twenty numbered stages, plus a 14b; the ones worth naming:

1. deploy Keeper and Pulse, register an upkeep against `Pulse.tick`
2. reject an execution before the due round
3. let a **stranger** execute it at the due round: Pulse's counter moves, the
   stranger is paid from escrow atomically, the upkeep reschedules
4. check the bot's box decoder against the chain, then let
   `scripts/keeper_bot.py --once` execute the following run
5. top up from a third party, reject a non-creator's cancel, cancel as the
   creator and get escrow + box MBR back
6. drain an upkeep and confirm it is rejected, not executed, when broke
7. prove a freshly created app holding only its 0.1 ALGO base MBR can still
   pay out its last execution (regression: `register` used to undercharge box
   MBR by 800 µALGO, which made exactly that fail)
8. register at every cadence a real user would pick (30 seconds, 5 minutes,
   1 hour, 1 day) and check the schedule, the funded-runs maths and the
   not-due rejection at each
9. leave an upkeep unattended for three whole intervals and confirm a
   `CATCH_UP` one replays a single interval per execution instead of skipping
   its history, and that a `SKIP_AHEAD` one drops the backlog and keeps its
   phase
10. measure what a *losing* keeper pays in a race, and confirm it can tell a
    lost race from a broken target
11. escalate a neglected upkeep's fee once, and confirm one escrow can never
    pay for another

Sustained operation is a separate test, because a single correct execution
says nothing about the hundredth:

```bash
poetry run python -m scripts.keeper_soak --network localnet --minutes 3
```

It executes the same upkeep over and over, asserting after every run that the
schedule advanced by exactly one interval, the escrow fell by exactly one fee,
and the app account can still pay out everything it holds. How many executions
a 2-minute run manages depends on how fast the machine drives LocalNet — 141 on
the one this was last measured on, 2026-08-31. The assertion is that none of
them drifted, not the count.

Every script picks its chain with `--network localnet|testnet` (or
`ARCRON_NETWORK`), loads the matching `.env.<network>`, and then verifies the
node's genesis id, so a stale `ALGOD_SERVER` can't quietly point a "localnet"
run at TestNet.

## Layout

```
smart_contracts/
  keeper/            # the keeper network (contract.py, deploy_config.py)
  pulse/             # the demo target: a heartbeat counter
  subscription/      # the teaching example: pull-payment recurring billing
  resource_probe/    # instrument: what an argument-free inner call may touch
  sim_probe/         # instrument: where simulate stops predicting execute
  artifacts/         # compiled TEAL, ARC-56 specs, typed clients (generated)
tests/               # unit tests (algorand-python-testing mocks + bot decoder vectors)
specs/               # spec-sync specs, one per contract, strict mode
js/                  # @corvidlabs/arcron: box decoder, ABI, transaction builders
web/                 # the console, the one page that is published
web-keeper/          # local keeper dashboard (localhost:4300, never published)
web-govern/          # local governance page (never published; can reach MainNet)
docs/
  arcron.md          # hand-off reference: API, box encoding, economics, operations
  integrating.md     # how to point Arcron at a contract you wrote
  security.md        # threat model, accepted risks, what happens if a bug is found
  design/split.md    # why rain left, and what this repository lost with it
  book/              # the Working Guide: all of docs/ in one ordered read
examples/
  register_upkeep.py # minimal: register an upkeep on the TestNet keeper app
  minimal_target.py  # the smallest contract Arcron can usefully call
  subscription.md    # the pull-payment example, walked through
  README.md          # the two integration paths (automate your app / earn fees)
scripts/
  keeper_e2e.py           # full e2e on LocalNet or TestNet: deploy, register, execute, verify
  keeper_soak.py          # sustained operation: many runs, no drift
  keeper_bot.py           # permissionless keeper bot: scans boxes, executes due upkeeps
  attacks.py              # every attack any review found, each refused by its own guard
  registry_health.py      # what is wrong with the live registry right now
  govern.py               # status / update / freeze, for the creator account
  verify_build.py         # does a deployment run this source, byte for byte
  network.py              # --network selection, genesis check, dev-mode round advance
fledge.toml          # tasks, and the ci / local / endurance lanes
.specsync/           # spec-sync config
AGENTS.md / CLAUDE.md # agent guidance (keep in sync)
```

## TestNet

The demo is end-to-end and self-funding (prints the deployer address; fund it
with ~2 TestNet ALGO from [Lora](https://lora.algokit.io/testnet/fund) or the
[bank](https://bank.testnet.algorand.network)):

```bash
cp .env.testnet.template .env.testnet   # or: algokit generate env-file -a target_network testnet
# add DEPLOYER_MNEMONIC for a TestNet account (throwaway; never reuse on mainnet)
poetry run python -m scripts.keeper_e2e --network testnet
```

### Deploying your own

Everything from a checkout to a running deployment, on any network, is in
[`docs/deploying.md`](docs/deploying.md):

```bash
algokit localnet start
fledge lanes run local          # everything, against a chain
fledge run deploy-localnet      # a keeper of your own
```

A new deployment starts **unfrozen**: its creator can still replace the
programs, and gives that up permanently with `fledge run govern -- freeze`
before anyone is asked to rely on it. `govern status` reads which state any
deployment is in, and anyone can check it.

## Running a keeper

Anyone can, and it is the most useful thing you can do here. The bot is a
plain process that watches rounds and calls `execute`, and it earns the fees
it collects. It signs as `KEEPER_MNEMONIC` if set, else `DEPLOYER_MNEMONIC`.
That is the account fees are paid to, and it pays the ~1,000 µALGO outer txn
fee per execution.

```bash
poetry run python -m scripts.keeper_bot --once   # single scan (cron-friendly)
poetry run python -m scripts.keeper_bot          # loop block-by-block
poetry run python -m scripts.keeper_bot --once --network localnet --app-id $APP
```

`--app-id` (or `KEEPER_APP_ID`) is required: there is no canonical Arcron
deployment to default to. An upkeep that fails to execute backs off exponentially,
and that state survives restarts, so a cron-driven `--once` bot does not
re-attempt a doomed upkeep on every run. Failing costs nothing (Algorand
rejects it before it reaches a block), so the schedule is gentle, capped at
about an hour, and losing a race to another keeper never backs off at all.
`--retry-now <id>` clears one upkeep's backoff once you have fixed its target.
`--check` reports registry health and exits, signing nothing and executing
nothing.

Note what a long-missed upkeep does depends on its policy. A `CATCH_UP` upkeep
schedules from the *scheduled* round, so it stays due until it has replayed one
execution per missed interval — a keeper coming back after an outage will find
a burst of them, and the escrow pays for every one. A `SKIP_AHEAD` upkeep is
due exactly once, then jumps to the next future slot. 30 of the 32 live
upkeeps are `SKIP_AHEAD`.

### Keeping it up

The bot is meant to run continuously, and there are three ways to do it:

| | For |
|---|---|
| `fledge run keeper-daemon-install` | macOS: generates and boots a launchd agent, since systemd is not an option on a Mac host |
| `deploy/keeper-bot.service` | Linux: a systemd unit |
| `deploy/Dockerfile` + `deploy/compose.yaml` | a container, anywhere |

All three read the same environment: `KEEPER_MNEMONIC`, `KEEPER_APP_ID` and an
algod endpoint. Keep the mnemonic in a `chmod 600` file the unit points at
rather than inline. A launchd plist under `LaunchAgents` is world-readable,
which is why the macOS path generates the plist rather than shipping one: it
writes no secret into it, and it refuses to install until the earnings have
somewhere to go. [`docs/hosting.md`](docs/hosting.md) compares the options with
real costs; the short version is that if you already run a server, put it
there:

```bash
./deploy/vps/package.sh
scp /tmp/arcron-keeper.tar.gz <user>@<host>:/tmp/
ssh <user>@<host> 'sudo mkdir -p /tmp/arcron-install \
    && sudo tar -xzf /tmp/arcron-keeper.tar.gz -C /tmp/arcron-install \
    && sudo bash /tmp/arcron-install/deploy/vps/install.sh'
```

`KEEPER_MAX_OUTER_FEE` raises the ceiling on the outer fee the keeper will
sign, which defaults to 10,000 microAlgos and exists to refuse a node quoting
an absurd one. A post-quantum keeper would need it if Algorand ever prices
bytes; see [`docs/hosting.md`](docs/hosting.md). Anything that is not a
positive integer falls back to the default, so neither a typo nor a zero can
switch the guard off.

A keeper is close to self-sustaining: it spends 0.003 ALGO of transaction fees
per execution and collects at least 0.004, so it needs a starting balance
rather than a budget. It refuses to start below 0.103 ALGO and warns below 0.4.

### Hard-won TestNet notes (already handled in code)

- **App account MBR**: the keeper app account escrows ALGO and holds box MBR,
  so it must be funded the base 0.1 ALGO account MBR first. `deploy_config`
  does this idempotently.
- **Suggested-params cache**: public TestNet endpoints are slow enough that
  algokit-utils' cached suggested params can expire before simulate/broadcast.
  Deploy configs disable the cache (`set_suggested_params_cache_timeout(0)`)
  and the e2e pins explicit validity rounds.

## Release stages

Arcron is at **alpha-3**: running on TestNet, and redeployable at any time for
any reason. Nothing here is a promise yet.

| Stage | What is frozen | At stake |
|---|---|---|
| **alpha** | nothing | nothing; we run every part |
| beta | the ABI surface and the `Upkeep` struct | other people's test upkeeps |
| rc | the exact bytecode intended for MainNet | our credibility |
| mainnet | everything, forever | real money |

Getting outside upkeeps registered is **alpha** work, not beta work: beta is
the freeze, so feedback that could still change the struct has to arrive before
it. The gates are in [`docs/releases.md`](docs/releases.md), and they
are deliberately specific: a struct change means a new app id whether or not
the programs can still be replaced, so a stage whose clock can be argued down
is not a gate.

## Spec-driven development

This repo is managed with [spec-sync](https://github.com/CorvidLabs/spec-sync)
(strict) and [fledge](https://github.com/CorvidLabs/fledge) lanes. Each of the
five contracts has a spec under `specs/` covering requirements, module
contract, invariants, error cases and testing. `specsync check --strict` runs
in the `ci` lane and fails if code drifts from the documented public API.

## Roadmap

- [x] Off-chain keeper bot in `scripts/keeper_bot.py` (watches rounds, executes due upkeeps)
- [x] ASA-denominated upkeep fees, a capability rather than a commitment: escrow and fees stay ALGO by default, and CORVID (mainnet ASA [`3225439167`](https://explorer.perawallet.app/asset/3225439167)) is not wired in
- [x] End-to-end verification on LocalNet (`fledge lanes run local`), which found and fixed an 800 µALGO box-MBR undercharge
- [x] Redeploy TestNet with the box-MBR fix: app [`769802474`](https://testnet.explorer.perawallet.app/application/769802474), e2e-verified on-chain
- [x] Redeploy for the 1.0 contract, now **alpha-3** on app [`769891898`](https://testnet.explorer.perawallet.app/application/769891898), all e2e stages green on-chain
- [x] Web front end: registry dashboard + keeper console in `web/`
- [x] Wallet signing (Pera, Defly, Lute, Exodus, Kibisis; KMD on LocalNet)
- [x] Multi-arg call shapes: three app args counting the selector, so up to **two** ARC-4 arguments per upkeep (`MAX_CALL_ARGS = 3`), or any arity at all if the target takes a single struct
- [x] Release stages, with the gate that ends each one ([`docs/releases.md`](docs/releases.md))
- [x] Something real built on it, in its own repository: [CorvidLabs/arcron-rain](https://github.com/CorvidLabs/arcron-rain)
- [ ] An upkeep registered by somebody who is not us, for something they actually wanted scheduled
- [ ] A keeper we can attribute to somebody else

## Contributing

The most useful contribution is not code: **run a keeper**. The network only
works because third parties execute due upkeeps, and that is deliberately open
to anyone, with no allowlist, no stake and no registration. See
[Operating a bot](docs/arcron.md#operating-a-bot).

For code, [CONTRIBUTING.md](CONTRIBUTING.md) covers what will otherwise bite
you: the Python version (never 3.14), the `fledge lanes run ci` gate, and
spec-sync strict, which fails a pull request on documentation drift a newcomer
would have no way to anticipate.

Security reports go through [SECURITY.md](SECURITY.md), privately rather than
as a public issue.

## Licence

[Apache-2.0](LICENSE) for the code, which carries an express patent grant.
That is worth having for protocol code other people are asked to build on.

**The brand assets in `web/public/brand/` are excluded**: they are CorvidLabs
trademarks, not licensed code. Fork Arcron freely, and replace those files with
your own marks if you run it as your own project. [NOTICE](NOTICE) says exactly
which files that covers and why.
