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
The job matters; whoever runs it does not, and nobody owns it.*

**Why this exists:** every serious chain should have a way to say *"call this
later"* without requiring a server, and Algorand does not have one. There is no
ARC for scheduled execution and never has been. [`docs/why.md`](docs/why.md)
makes the case at about a seventh the cost of the cheapest paid host, says
where it stops being true (above ~10 upkeeps, run your own bot), and states
plainly what would prove it wrong. [`docs/testnet.md`](docs/testnet.md)
lists every contract and upkeep actually running.

| Contract | What it is | Status |
|----------|-----------|--------|
| [`smart_contracts/keeper`](smart_contracts/keeper/contract.py) | The Arcron network: upkeep scheduling with ALGO escrow and keeper rewards | **Live on TestNet**, app [`769891898`](https://testnet.explorer.perawallet.app/application/769891898) |
| [`smart_contracts/pulse`](smart_contracts/pulse/contract.py) | Demo upkeep target: a heartbeat counter, with and without arguments | Live on TestNet, app [`769891902`](https://testnet.explorer.perawallet.app/application/769891902) |
| [`web`](web/) | The console: registry dashboard + keeper controls | Live at [`corvidlabs.xyz/arcron/console/`](https://corvidlabs.xyz/arcron/console/) |

> [!WARNING]
> **Unaudited, and TestNet only.** No third party has reviewed this contract.
> A deployment also starts **upgradeable**: until its creator calls `freeze`,
> they can replace the programs. That cuts both ways. A bug can be fixed in
> place, and the rules can be changed after you have escrowed funds. Calling
> `freeze` gives up both, permanently, and `frozen` is global state so anyone
> can check which of the two a deployment is:
> ```
> poetry run python -m scripts.govern status --network testnet --app-id 769891898
> ```
> Read [`docs/security.md`](docs/security.md) before escrowing anything: the
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

Ownerless, no protocol rake, no token required. Escrow is plain ALGO, so any
group can use it. Upkeep records are `arc4.Struct`s in boxes, so reading the
registry is a free algod query.

**Constraints (v1):** registered calls are NoOp app calls carrying up to three
app args, counting the selector, which is enough for an ARC-4 method of arity
two. The zero-argument "tick/settle/harvest" hook is still the common shape.
An upkeep declares no foreign arrays, and does not need to: a keeper that
simulates before executing discovers what the inner call touches and attaches
the references. The Python bot does this by simulating the call and naming the references itself, because algokit-utils' default populator caps at four, and
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

Missed executions are not lost. Scheduling advances from the *scheduled* round,
so an upkeep left unattended stays due and catches up one interval per
execution rather than skipping its history.

Cadences are counted in rounds, not wall-clock time (~2.8 s per round, and it
drifts), so "daily" means "every ~30,857 rounds" and slides slowly against the
calendar.

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

**Proven end-to-end on TestNet**: upkeeps registered against `Pulse.tick`
(both by the e2e script and the `examples/` flow) have been executed by
permissionless callers at their due rounds, with `Pulse.beats` incremented by
every execution (rounds 66610411, 66611741, 66625540+, all verifiable on the
explorer). Full reference: [`docs/arcron.md`](docs/arcron.md).

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

fledge lanes run ci         # contracts + console: build, tests, spec check
fledge lanes run local      # ci + the LocalNet end-to-end test
fledge lanes run endurance  # local + a soak: many consecutive executions, no drift
```

`fledge lanes run local` needs LocalNet up (`algokit localnet start`) and no
secrets. LocalNet accounts come from KMD, funded by its dispenser.

Individual tasks (also in `fledge.toml`):

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
a funding problem. `clock` measures the MainNet hold from the application's
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
("every 1,286 rounds · ~1 h"). Built on the
the CorvidLabs design system, which is a private repository and vendored
here under `web/public/brand/`;
see [`web/README.md`](web/README.md).

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
same script that runs against TestNet with `--network testnet`:

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
9. leave an upkeep unattended for three whole intervals and confirm it catches
   up one interval per execution instead of skipping its history

Sustained operation is a separate test, because a single correct execution
says nothing about the hundredth:

```bash
poetry run python -m scripts.keeper_soak --network localnet --minutes 3
```

It executes the same upkeep over and over, asserting after every run that the
schedule advanced by exactly one interval, the escrow fell by exactly one fee,
and the app account can still pay out everything it holds. A 2-minute run
does ~170 consecutive executions.

Every script picks its chain with `--network localnet|testnet` (or
`ARCRON_NETWORK`), loads the matching `.env.<network>`, and then verifies the
node's genesis id, so a stale `ALGOD_SERVER` can't quietly point a "localnet"
run at TestNet.

## Layout

```
smart_contracts/
  keeper/            # the keeper network (contract.py, deploy_config.py)
  pulse/             # demo target
  artifacts/         # compiled TEAL, ARC-56 specs, typed clients (generated)
tests/               # unit tests (algorand-python-testing mocks + bot decoder vectors)
specs/               # spec-sync specs (keeper, pulse), strict mode
web/                 # the console (Angular + Bun + algosdk, CorvidLabs design system)
docs/
  arcron.md          # hand-off reference: API, box encoding, economics, operations
examples/
  register_upkeep.py # minimal: register an upkeep on the TestNet keeper app
  README.md          # the two integration paths (automate your app / earn fees)
scripts/
  keeper_e2e.py           # full e2e on LocalNet or TestNet: deploy, register, execute, verify
  keeper_soak.py          # sustained operation: many runs, no drift
  keeper_bot.py           # permissionless keeper bot: scans boxes, executes due upkeeps
  network.py              # --network selection, genesis check, dev-mode round advance
  keeper_testnet_demo.py  # alias for `keeper_e2e --network testnet`
fledge.toml          # fledge lanes (ci, local)
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

Anyone can. It is a plain process that watches rounds and calls `execute`,
and it earns the fees it collects. [`docs/hosting.md`](docs/hosting.md)
compares the options with real costs; the short version is that if you
already run a server, put it there:

```bash
./deploy/vps/package.sh
scp /tmp/arcron-keeper.tar.gz <user>@<host>:/tmp/
ssh <user>@<host> 'sudo mkdir -p /tmp/arcron-install \
    && sudo tar -xzf /tmp/arcron-keeper.tar.gz -C /tmp/arcron-install \
    && sudo bash /tmp/arcron-install/deploy/vps/install.sh'
```

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

## Running a keeper bot

The bot services the live keeper app: it scans the upkeep boxes every round
and calls `execute` on anything due and funded, collecting the fees. It signs
as `KEEPER_MNEMONIC` if set, else `DEPLOYER_MNEMONIC`. That is the account
fees are paid to, and it pays the ~1,000 µALGO outer txn fee per execution.

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

Note the contract schedules from the *scheduled* round, so an upkeep that was
missed for many intervals stays due until it has caught up one execution per
interval.

### Hard-won TestNet notes (already handled in code)

- **App account MBR**: the keeper app account escrows ALGO and holds box MBR,
  so it must be funded the base 0.1 ALGO account MBR first. `deploy_config`
  does this idempotently.
- **Suggested-params cache**: public TestNet endpoints are slow enough that
  algokit-utils' cached suggested params can expire before simulate/broadcast.
  Deploy configs disable the cache (`set_suggested_params_cache_timeout(0)`)
  and the e2e pins explicit validity rounds.

## Running a keeper

The bot is meant to run continuously, and `deploy/` has three ways to do it:

| | For |
|---|---|
| `com.corvidlabs.arcron-keeper.plist` | macOS: a launchd agent, since systemd is not an option on a Mac host |
| `keeper-bot.service` | Linux: a systemd unit |
| `Dockerfile` + `compose.yaml` | a container, anywhere |

All three read the same environment: `KEEPER_MNEMONIC`, `KEEPER_APP_ID` and an
algod endpoint. Keep the mnemonic in a `chmod 600` file the unit points at
rather than inline. A launchd plist under `LaunchAgents` is world-readable.

`KEEPER_MAX_OUTER_FEE` raises the ceiling on the outer fee the keeper will
sign, which defaults to 10,000 microAlgos and exists to refuse a node quoting
an absurd one. A post-quantum keeper would need it if Algorand ever prices
bytes; see [`docs/hosting.md`](docs/hosting.md). Anything that is not a
positive integer falls back to the default, so neither a typo nor a zero can
switch the guard off.

A keeper is close to self-sustaining: it spends 0.003 ALGO of transaction fees
per execution and collects at least 0.004, so it needs a starting balance
rather than a budget. It refuses to start below 0.103 ALGO and warns below 0.4.

## Spec-driven development

This repo is managed with [spec-sync](https://github.com/CorvidLabs/spec-sync)
(strict) and [fledge](https://github.com/CorvidLabs/fledge) lanes. Every
contract has a spec under `specs/` covering requirements, module contract,
invariants, error cases and testing. `specsync check --strict` runs in the
`ci` lane and fails if code drifts from the documented public API.

## Roadmap

- [x] Off-chain keeper bot in `scripts/keeper_bot.py` (watches rounds, executes due upkeeps)
- [x] ASA-denominated upkeep fees, a capability rather than a commitment: escrow and fees stay ALGO by default, and CORVID (mainnet ASA [`3225439167`](https://explorer.perawallet.app/asset/3225439167)) is not wired in
- [x] End-to-end verification on LocalNet (`fledge lanes run local`), which found and fixed an 800 µALGO box-MBR undercharge
- [x] Redeploy TestNet with the box-MBR fix: app [`769802474`](https://testnet.explorer.perawallet.app/application/769802474), e2e-verified on-chain
- [x] Redeploy for the 1.0 contract, now **alpha-3** on app [`769891898`](https://testnet.explorer.perawallet.app/application/769891898), all 20 e2e stages green on-chain
- [x] Web front end: registry dashboard + keeper console in `web/`
- [x] Wallet signing (Pera, Defly, Lute, Exodus, Kibisis; KMD on LocalNet)
- [x] Multi-arg call shapes, up to three ARC-4 arguments per upkeep
- [x] Release stages, with the gate that ends each one ([`docs/releases.md`](docs/releases.md))

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
