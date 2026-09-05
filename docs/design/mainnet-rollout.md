# The quiet MainNet rollout

Decided 2026-09-05. This is the plan for putting the keeper on MainNet without
announcing it, what has to be true before the create, what the month after it
is for, and what decides whether it is ever announced. It is the operating
half of [`1.0.md`](1.0.md): that page says what the contract is, this one says
how it reaches real money.

## The decision

**Create the keeper on MainNet from the bytecode that has been soaking on
TestNet, leave it unfrozen, publish nothing, and use it only for our own
upkeeps for at least thirty days.** Then decide, on the evidence, whether to
announce it and whether to freeze it.

Four things follow from that, and each was chosen against an alternative:

1. **The soaked bytecode, not new bytecode.** `smart_contracts/keeper/contract.py`
   has not changed since `alpha-3`; HEAD compiles to the exact `c94c6e0c…`
   that app 769891898 has run for ten days and 1,400 executions. Every open
   review finding against the contract is program-only (three small asserts
   at `opt_in_asset` and `top_up_asset`, and the escalation ramp), so none of
   them is something a create makes permanent, and all of them are reachable
   by `govern update`. Cutting new bytes for them now would mean deploying
   something with zero days behind it to fix things that cannot hurt us.
2. **Escalation is decided in alpha-4, not before the create.**
   [`escalation.md`](escalation.md) frames three options and asks two
   questions that a registry with no outside participants cannot answer, so
   waiting a month buys no information. What the month does buy is a first
   `govern update` on MainNet with only our own escrow at stake, which is a
   path that has to be exercised before a freeze anyway. Until then every
   upkeep we register has `fee_cap = 0`, which
   [`../security.md`](../security.md) says makes it immune to the whole
   finding. Alpha-4 bundles that decision with the three asserts, lands on
   TestNet first, soaks, then goes to MainNet by update.
3. **Unfrozen, deliberately, and for the whole quiet period.** Every reviewer
   who scored both scored the frozen deployment lower, for the same reason:
   this repository has produced about one new true finding per review round,
   and freeze turns the next one from a patch into a migration. Freeze is the
   rc gate. It is triggered by "somebody who is not us is about to escrow", or
   by the notifier saying somebody already has, and not by a calendar.
4. **Own upkeeps only, and the notifier is the control.** Not publishing the
   app id is a courtesy and not a protection: the creator is a named address
   and the id is one indexer query away from it. The protection is
   `scripts/notifier.py` running against MainNet with `--ours` set, announcing
   any other creator, and the agreed answer to that announcement, which is to
   freeze. The notifier refuses to start on MainNet without `--ours` and a
   webhook for exactly this reason.

## The goal

Dates are targets. The gates are not, and each one is something a script can
check rather than something somebody remembers.

| | by | done when |
|---|---|---|
| **G1 · operations exist** | 2026-09-12 | A VPS runs the keeper and the notifier against TestNet from `main`, not from a laptop, not from a feature branch. The notifier posts to Discord and has done so for seven days. The node in front of them is our own or has a fallback, and `health` shows executions without a 403 storm behind them. |
| **G2 · the create** | 2026-09-19 | The ceremony below has been rehearsed on TestNet from a clean checkout with a fresh throwaway, including `update` and `freeze` on the result, and the record is in this file. `git tag mainnet-1`. `fledge run deploy-mainnet -- --with-pulse` runs, reads back clean, and `govern status` shows spendable at or above escrow. A keeper and the notifier are running against the new id before the first upkeep is registered. Pulse `tick` is registered from `corvid.algo` at `fee_cap 0` and has executed. |
| **G3 · the quiet month** | 2026-10-19 | `arcron-rain` has a MainNet path and its hub's `draw()` is an upkeep. At least one other CorvidLabs target is registered. Alpha-4 has landed on TestNet, soaked, and gone to MainNet by `govern update`. `fledge run health-mainnet` and `clock-mainnet` have been read weekly and say nothing surprising. The notifier has announced every execution and no stranger. |
| **G4 · the decision** | after G3 | Announce, or do not. Freeze, or do not. Both written into [`../releases.md`](../releases.md) as the rc row asks, with the notifier's record as the evidence. |

## Where confidence stands

As of 2026-09-05, after this plan's pull request. Each row says what was true
before it and what moves the number next.

| question | now | what moves it |
|---|---|---|
| The contract can hold our own money on MainNet, unfrozen | high | Already the strongest thing here. Five review rounds, an audit that said yes to the contract as written, no open finding that a create makes permanent, and a remedy (`update`) for the ones that remain. |
| `fledge run deploy-mainnet` does exactly what it says | low, rising | The MainNet branch of `scripts/deploy.py` was a day old and had never run. It now refuses a dirty or untagged tree, any creator but `corvid.algo`, a mnemonic in `.env.mainnet`, and a second keeper; prints every permanent field; simulates; reads everything back. The rehearsal in G2 is what turns this from tested to done. |
| We can operate it quietly | low | Nothing runs anywhere but a laptop and a best-effort cron, and the notifier has never run on any network. G1 is this row. |
| We can announce it and invite escrow | not yet | Needs the escalation decision deployed, a notifier record, and the freeze decision. G4. |

## What quiet protects, and what it does not

`register` is permissionless. Anyone who learns the app id can escrow into it,
and while the deployment is unfrozen they are trusting a single key that could
replace `execute` with something that pays itself. Nothing in this plan
removes that; the plan is to make sure nobody is in that position without us
knowing within a scan.

So: the app id goes in no README, no status page, no console build, no post.
It lives in `.env.mainnet` on the machines that need it and in
`/etc/arcron/*.env` on the VPS. `corvid.algo` creating an app is visible on
any explorer regardless, which is why the notifier and not the secrecy is the
control.

If a stranger appears: `fledge run govern-ui`, connect Pera as `corvid.algo`,
freeze. That page is the one wallet-signing surface that reaches MainNet (the
create and alpha-4's `update` sign from a shell export instead), it is never
published, and freezing is the answer even if the plan said another month.

## The ceremony

Every step is a command, and every command that can refuse does.

**Before, once.** The rehearsal on TestNet, recorded below. A VPS keeper and
notifier already running (G1). `corvid.algo` holding about one ALGO more than
it needs: the create costs roughly 0.46 ALGO of permanent minimum balance
across the keeper's two pages, its two globals and Pulse, plus 0.1 ALGO sent to
the app account and fees.

**The machine.** A clean checkout at the tag, on a machine that will not keep
the key, with the Python environment installed: the rebuild shells out to
`algokit generate client`, which needs `algokitgen-py` from the virtualenv, so
run it through `fledge run` (or `poetry run`), which puts that on `PATH`.
`.env.mainnet` copied from the template, carrying the node and an empty
`KEEPER_APP_ID` and nothing else.

```sh
git checkout mainnet-1 && git status --porcelain   # prints nothing
poetry install
read -rs DEPLOYER_MNEMONIC; export DEPLOYER_MNEMONIC
ARCRON_ALLOW_MAINNET=1 fledge run deploy-mainnet -- --with-pulse
```

The script rebuilds, connects, checks the genesis id is `mainnet-v1.0`, and
prints the network, creator, program sizes, combined sha256, commit, tag, extra
pages and both schemas. It refuses, all reasons at once, on a dirty tree, an
untagged commit, a creator that is not `corvid.algo`, a `DEPLOYER_MNEMONIC`
line in `.env.mainnet`, or an existing keeper created by this account. Then it
asks for the creator address to be typed back, simulates the create, sends the
same signed bytes, funds the 0.1 ALGO floor, and reads creator, pages, schema,
programs and `frozen` back from the chain. A mismatch there is shouted, because
the app exists.

**After, in this order, in the same shell.** The key stays exported until
the first upkeep is registered, because that registration signs as the
creator too, and the alternative is writing the key into a file that every
MainNet script now refuses to load.

```sh
# put the printed id in .env.mainnet as KEEPER_APP_ID, and in the VPS env files
ARCRON_ALLOW_MAINNET=1 fledge run govern -- status --network mainnet --app-id <id>
ARCRON_ALLOW_MAINNET=1 poetry run python -m scripts.verify_build --network mainnet --app-id <id>
```

`status` must show `frozen 0` and spendable at or above escrow. Straight
after the create that reads `0.000 ALGO owed, 0.000 ALGO spendable`: the
0.1 ALGO floor is exactly the account minimum, so nothing is spendable until
the first registration brings its own box minimum balance and escrow with it.
The LocalNet rehearsal below printed exactly that, and an earlier draft of
this sentence said 0.1. `verify_build` must say byte for byte. Record both
outputs, the id and the sha256, privately.

Both apps are created directly, from their own specs, with the same checks
and the same read-back; no indexer is consulted for either. `INDEXER_SERVER`
in `.env.mainnet` is for `health` and `keeper-preview`, which read executions
from it.

Then the keeper and the notifier on the VPS, pointed at the new id, with a
separate hot key holding one or two ALGO, before anything is registered. An
empty registry with no watcher is worse than no deployment. Both refuse an id
that does not exist or is not a keeper, so a typo in either env file stops the
unit rather than watching an empty box list.

Then the first upkeep: Pulse `tick` every twelve hours, `SKIP_AHEAD`, at the
4,000 µALGO floor with `fee_cap 0`, funded for thirty runs. That is the
`skip-ahead` seed in `scripts/seed_registry.py`, the same shape as TestNet
upkeeps 20 to 22, and `--only` selects it alone. The script's other seeds
include a 25-round burn-in and one with a fee ceiling, neither of which
belongs on MainNet, and an earlier draft of this page ran the script without
`--only` and would have registered all six.

```sh
ARCRON_ALLOW_MAINNET=1 poetry run python -m scripts.seed_registry --network mainnet --app-id <id> --target <pulse id> --only skip-ahead
ARCRON_ALLOW_MAINNET=1 poetry run python -m scripts.seed_registry --network mainnet --app-id <id> --target <pulse id> --only skip-ahead --commit
unset DEPLOYER_MNEMONIC                                    # and close the shell
```

The first line prices it and signs nothing; the second registers. Watch it
execute. Watch the notifier say so, and name the keeper.

## Reading it during the month

```sh
ARCRON_ALLOW_MAINNET=1 fledge run health-mainnet   # what is wrong, and who is executing
ARCRON_ALLOW_MAINNET=1 fledge run clock-mainnet    # days since the create; stops counting if the source moves
```

Both read `KEEPER_APP_ID` from `.env.mainnet`. Neither signs. Both refuse to
guess an id.

## What it costs

MainNet at the measured 2.752 seconds a round.

| | µALGO | |
|---|---|---|
| creator minimum balance, keeper (app, one extra page, two globals) | ~257,000 | permanent |
| creator minimum balance, Pulse | ~207,000 | permanent |
| app account floor | 100,000 | sent once, stays |
| box minimum balance per upkeep, bare selector | 62,100 | refunded on cancel |
| hourly upkeep for 30 days at 10,000 per run | 7,200,000 | escrow, spent as it runs |
| daily upkeep for 30 days at 10,000 per run | 300,000 | escrow |
| keeper account | 1,000,000 to 2,000,000 | a hot key; it earns 7,000 net per hourly run |

A quiet month with Pulse twice daily, rain hourly and two daily targets is
about eight ALGO of escrow, half an ALGO of permanent creator minimum balance,
and a quarter of an ALGO of box minimum balance that comes back on cancel. It
is not a budget question.

## Rehearsal record

A plan that has not been rehearsed is a hope. Two rehearsals are planned, on
real chains, from a clean detached worktree at the commit under review, with
no `.env.*` file present and the tooling's virtualenv on `PATH`. One has run.

**LocalNet, 2026-09-05, commit `1ea6045`.** Creator is the KMD dispenser
account, which had made three keepers before, so the first run was the
refusal it should be:

```
Refusing to create:
  - 6AG5ECWI… has already created keeper app(s) 1002, 1065, 1499 on localnet.
    Pass --another to create a second one on purpose, which a rehearsal may well want.
```

The second run, `--with-pulse --another`, with the creator address typed
back: the checklist printed `2219 + 4 bytes`, combined
`c94c6e0cc561c028eeb3ccdd8c462c509ee106a28ba2e1d61469adbb62ffe124`, extra
pages 1, global 2 uints / 0 byte slices, local 0 / 0; simulate passed; the
create landed as app `205070`; the floor was funded with 100,000 µALGO; the
read-back reported `Verified: creator, pages, schema and programs read back as
described`. Then, in order:

| step | result |
|---|---|
| `govern status` | `frozen 0`, `0.000 ALGO owed, 0.000 ALGO spendable` |
| `verify_build --app-id 205070` | `The deployed app is this source, byte for byte.` |
| a create with `yes` typed instead of the address | `Not created.`, nothing sent |
| `govern update` | `Deployed programs already match this tree. Nothing to do.` |
| `govern freeze`, app id typed back | `Frozen. App 205070 is now permanently c94c6e0c…` |
| `govern status` | `frozen 1: the programs can never be replaced` |
| `govern update` after freeze | `Refusing: this app is frozen, or has no freeze flag at all.` |

The worktree was clean before and after both rebuilds, so the committed
artifacts are what the compiler produces. One thing the rehearsal found and
this page did not know: without the virtualenv on `PATH` the rebuild fails at
`algokit generate client` (`Command not found: algokitgen-py`), which is why
the ceremony above says `poetry install` and `fledge run`.

**LocalNet again, 2026-09-05, commit `4078b17`**, after three independent
reviews changed the script. `--with-pulse` without `--another` refused both
apps in one list (`keeper app(s) 1002, 1065, 1499, 205070` and
`pulse app(s) 1004`). With `--another` and the creator typed back: keeper
`205073` and Pulse `205075`, both created directly, both read back
(`Verified: creator, pages, schema and programs read back as described`).
Pulse on chain: 0 extra pages, global 2 uints / 1 byte slice, local 0 / 0.
The startup check accepted `205073`, refused `205075` (`not a keeper: its
global state has no next_upkeep_id`) and refused `999999999` (`does not
exist`). `notifier --once` against `205073` ran clean. Worktree clean.

**TestNet.** Pending: the throwaway creator
`CVM4NOTWQYDRAUVF3EYHLZJXWERUI33GLFCNAV4MR4YVNOT6Z3XJMDGKNE` is generated and
the script is staged; it needs about two TestNet ALGO, and the TestNet deployer
has half of one spendable. What TestNet adds over LocalNet is the public node
in the loop (retries, the 403 shedding) and a creator that has never made an
app; the decisions being rehearsed are the same.

## What this plan changed in the repository

- `scripts/deploy.py` is the ceremony described above, with `tests/test_deploy.py`
  pinning every refusal and the read-back against a chain that lies.
- `.env.mainnet.template` carries no secret and says why; `deploy-mainnet`
  refuses if a mnemonic is written into the real file.
- `scripts/notifier.py` credits an execution only to a call carrying the
  `execute` selector, caps Discord's `Retry-After`, polls every 30 seconds
  instead of 5, reads `ARCRON_OURS` from the environment, and refuses to
  start on MainNet without `--ours` and a webhook.
- `scripts/node_retry.py` alternates to `ALGOD_SERVER_FALLBACK` on a refusal.
- `fledge run health-mainnet`, `clock-mainnet` and `keeper-preview-mainnet`
  read the app id from `.env.mainnet` rather than from the tree.
- `deploy/vps/package.sh` ships the files `install.sh` installs; it did not,
  and a packaged install died on the notifier unit. `install.sh` writes the
  keeper env from `deploy/keeper.env.example` instead of a second copy, and
  `deploy/vps/algod.compose.yaml` runs a MainNet node of our own.
- `scripts/keeper_daemon.py` refuses `--network mainnet`: MainNet is a VPS.

And after three independent reviews of that, the same day:

- `--with-pulse` creates Pulse directly too, and `smart_contracts/*/deploy_config.py`
  (algokit's deploy, reachable from `algokit project deploy` and the soak)
  refuses MainNet by genesis id whoever the deployer is.
- The mnemonic-on-disk rule moved into `scripts/network.py`, so `govern`,
  `seed_registry`, `health` and everything else that reaches MainNet refuses
  a `.env.mainnet` carrying the key, not only the create.
- A create that is sent and does not confirm is reported with its txid and
  "it may well have landed", instead of a traceback that implies it did not.
- The bot and the notifier refuse an app id that does not exist or is not a
  keeper at startup; a wrong id used to scan an empty box list and exit clean.
- The endpoint rotation swaps the token with the address and returns to the
  primary after a success, so our node's token never reaches the public edge
  and one refusal during a restart does not park a keeper there.
- `deploy/keeper.env.example` carries no comment on a value line: systemd's
  `EnvironmentFile` keeps a trailing `# ...` as part of the value, and an
  `ALGOD_SERVER` with one glued on answered every request with 405.
- `install.sh` installs `python3-pip` on a stock image and copies the algod
  compose file to `/etc/arcron/`; both units restart on failure with a limit
  rather than flapping forever.
