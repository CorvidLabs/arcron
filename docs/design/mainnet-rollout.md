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
   rc gate, and a calendar does not call it. A stranger registering is also
   not an automatic freeze: that would let an outsider choose the moment
   known defects become permanent, before alpha-4 has landed. The policy is
   below.
4. **Own upkeeps only, and the notifier is the control.** Not publishing the
   app id is a courtesy and not a protection: the creator is a named address
   and the id is one indexer query away from it. The protection is
   `scripts/notifier.py` running against MainNet with `--ours` set to the
   58-character `corvid.algo` address (it does not resolve NFD names),
   announcing any other creator as a durable, prioritized alert. The agreed
   answer to that announcement is an operator release-readiness decision,
   not a scripted freeze, update, or cancel. The notifier refuses to start
   on MainNet without `--ours` and a webhook for exactly this reason.

## The goal

Dates are targets. The gates are not, and each one is something a script can
check rather than something somebody remembers.

| | by | done when |
|---|---|---|
| **G1 · operations exist** | 2026-09-12 | A VPS runs the keeper and the notifier against TestNet from `main`, not from a laptop, not from a feature branch. The notifier posts to Discord and has done so for seven days. The node in front of them is our own or has a fallback, and `health` shows executions without a 403 storm behind them. |
| **G2 · the create** | 2026-09-19 | The ceremony below has been rehearsed on TestNet from a clean checkout with a fresh throwaway, including a **code-changing** `update` (not a no-op “already matches”) and `freeze` on the result, and the record is in this file. [#250](https://github.com/CorvidLabs/arcron/issues/250) F01, F02, F04, F05 and ceremony-path F14 are merged; F10/F11 evidence is recorded against that candidate. `git tag mainnet-1`. `fledge run deploy-mainnet -- --with-pulse` runs, reads back clean, and `govern status` shows spendable at or above escrow. A keeper and the notifier are running against the new id before the first upkeep is registered. Pulse `tick` is registered from `corvid.algo` at `fee_cap 0` and has executed. Soak claims at this step are bytecode equality plus install history, not app age plus the current hash (F03). |
| **G3 · the quiet month** | 2026-10-19 | `arcron-rain` has a MainNet path and its hub's `draw()` is an upkeep. At least one other CorvidLabs target is registered. Alpha-4 has landed on TestNet, soaked, and gone to MainNet by `govern update`. `fledge run health-mainnet` and `clock-mainnet` have been read weekly and say nothing surprising. The notifier has announced every execution and no stranger. |
| **G4 · the decision** | after G3 | Announce, or do not. Freeze, or do not. Both written into [`../releases.md`](../releases.md) as the rc row asks, with the notifier's record as the evidence. |

## Where confidence stands

As of 2026-09-08, after the [#250](https://github.com/CorvidLabs/arcron/issues/250)
remediation landed. Each row says what was true before it and what moves the
number next.

| question | now | what moves it |
|---|---|---|
| The contract can hold our own money on MainNet, unfrozen | high | Already the strongest thing here. Five review rounds, an audit that said yes to the contract as written, no open finding that a create makes permanent, and a remedy (`update`) for the ones that remain. |
| `fledge run deploy-mainnet` does exactly what it says | medium | Refuses a dirty or untagged tree, any creator but `corvid.algo`, a mnemonic in `.env.mainnet`, a second keeper, and a node advising a fee above 10,000 µALGO (it pays the network minimum flat whatever the node says), and, since 2026-09-08, a digest that is not what TestNet app 769891898 is running (F10, F14). Rehearsed twice on LocalNet. The TestNet rehearsal with a code-changing `update` is what turns this into done; it is still blocked on funding the throwaway. |
| The watcher sees a stranger and says so until Discord has accepted it | medium | The code half of F01, F02, F04 and F05 landed 2026-09-08 with tests against fakes. What it has never done is run against a real node for a day, which is G1, and the F05 request shape has not been answered by the live TestNet endpoint yet. |
| We can operate it quietly | low | Nothing runs anywhere but a laptop and a best-effort cron, and the notifier has never run continuously against any network; one `--once` scan on LocalNet during the 2026-09-05 rehearsal is the whole of its run history. G1 is this row. |
| We can announce it and invite escrow | not yet | Needs the escalation decision deployed, a notifier record, and the freeze decision. G4. |

## What quiet protects, and what it does not

`register` is permissionless. Anyone who learns the app id can escrow into it,
and while the deployment is unfrozen they are trusting a single key that could
replace `execute` with something that pays itself. Nothing in this plan
removes that; the plan is to make sure nobody is in that position without us
knowing on the next successful notifier scan. A snapshot watcher cannot
promise to see a register/cancel pair that both happen entirely between
scans; that coverage boundary is accepted unless we later consume
registration history.

So: the app id goes in no README, no status page, no console build, no post.
It lives in `.env.mainnet` on the machines that need it and in
`/etc/arcron/*.env` on the VPS. `corvid.algo` creating an app is visible on
any explorer regardless, which is why the notifier and not the secrecy is the
control.

If a stranger appears: a durable Discord alert, stranger-priority, and an
**operator decision**. None of automatic freeze, automatic unsoaked
alpha-4, or cancel/ignore is the safety rule. `cancel` is creator-only on
the upkeep, so we cannot remove somebody else's box. Freeze only bytecode
already accepted for permanence. Otherwise execute only an
already-approved update/verification sequence, or explicitly accept the
temporary unfrozen exposure while responding. `fledge run govern-ui` (Pera
as `corvid.algo`) remains the wallet-signing freeze surface; create and
alpha-4's `update` still sign from a shell export. That page is never
published.

**Who, and how fast.** The responsible operator is the holder of
`corvid.algo`. During the quiet month they check Discord at least once
every 24 hours. A stranger alert is decided (freeze / approved-update /
accept-unfrozen-exposure) within 24 hours of first seeing it; the
decision, the evidence, and the time of first sighting are recorded. A
missing daily notifier summary is treated as a dead watcher within those
same 24 hours — that is a human response budget, not the 30-second scan
interval. On create day the operator is at the keyboard and the budget is
minutes. The notifier's stranger text has said this since 2026-09-08: an
operator decision within 24 hours of first sighting, the three permitted
answers, and that cancel is not one of them. So has `deploy/notifier.service`
and the env example, which used to promise the opposite.

**F01 delivery.** Persist the pending stranger payload until Discord
returns 2xx, keyed by network/app/upkeep, even if the box is later
cancelled. A delivered-id set that only re-reads live boxes will drop an
alert that failed, then vanished. At-least-once; duplicates beat silence.
Landed 2026-09-08: the notifier writes each stranger to
`notifier-<network>-<app>-pending.json` (or `<state-file stem>-pending.json` under `--state-file`), beside its snapshot, before the
snapshot advances; re-posts every pending record at the start of every loop,
before the node is asked anything, so a node outage does not stall the retry,
at least five minutes apart per record; and deletes a record only after a
2xx. `post` makes up to three attempts on 429, 5xx and network errors with
bounded backoff and returns whether Discord accepted. A 2xx is Discord
accepting the message, not a person reading it; the reading is the 24-hour
budget above. The tests pin the outage, the restart, a
cancel between discovery and delivery, a crash between the 2xx and the
acknowledgement, and a flood of executions in the same scan.

**F05** is “readers request real pages and do not fail closed at the
listing cap.” It is not flood resistance and not a promise that the first
stranger box alerts before the rest of the scan finishes: today's notifier
builds a full snapshot first. Do not claim bounded alert latency from
priority-sorting a completed list. Landed 2026-09-08: every page, the first
included, is `GET /v2/applications/{id}/boxes?limit=1000[&next=…]`, which
algod's spec defines as pagination mode (sorted names, a `round` on the
response, a `next-token` exactly when more remain; in algod since 4.7.0).
A node that ignores `limit` answers without a `round` and is refused rather
than read on. The pinned SDK's `application_boxes(limit=)` sends the legacy
`max`, which is why the old continuation loop was reachable only from a
fake. The live TestNet endpoint was not probed from the machine that wrote
this; the first `fledge run health` after merge is that probe.

Policy recorded 2026-09-05 by Leif, taking the recommendation in
[#250](https://github.com/CorvidLabs/arcron/issues/250) (Astra / Kyntrin
comment 5553940330, corrections 5554020272). Quiet create waits on F01,
F02 (this policy plus matching notifier text), F04, F05 as scoped above,
ceremony-path F14, and F10/F11 evidence, including a real code-changing
TestNet update. F06, F07, F08 at `fee_cap 0`, F09 public copy, F12 and
F13 summaries are not G2 blockers. F13 numbers are estimates unless they
come from actual execution payments; they are not gate evidence. If even
temporary outsider escrow were unacceptable, the create would wait until
freeze-ready; that stricter path is not this experiment.

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
from it, and for `clock-mainnet`, which reads the create and every `update`
from it and reports the hold as unknown without it.

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
ARCRON_ALLOW_MAINNET=1 fledge run clock-mainnet    # days since the programs were installed; unknown if the indexer cannot say
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
app; the decisions being rehearsed are the same. The LocalNet `govern update`
was a no-op (“already match”); [#250](https://github.com/CorvidLabs/arcron/issues/250)
F10 requires the TestNet rehearsal to also send a **code-changing** update
before freeze, so the path that will carry alpha-4 has been exercised on a
public node, not only refused as identical.

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

And on 2026-09-08, taking the [#250](https://github.com/CorvidLabs/arcron/issues/250)
G2 bar finding by finding:

- **F01.** A stranger alert is a pending record on disk until Discord returns
  a 2xx, re-posted first on every scan, and it carries its own payload so a
  box cancelled before delivery is still announced. `post` retries and reports
  whether it succeeded instead of swallowing 429 and 5xx.
- **F02.** The stranger text, the systemd unit and the env example ask for the
  operator decision above, within 24 hours, instead of a freeze. `--ours`
  refuses anything that is not a 58-character address, so `corvid.algo`
  typed literally is a startup error rather than every creator becoming a
  stranger.
- **F03.** `fledge run clock` dates the hold from the round the installed
  programs were installed, found in indexer history (the create and every
  `update`, paged to exhaustion), reports app age separately, and refuses
  to count when history is unavailable or incomplete. A no-op update resets
  it, because the indexer records the transaction and not the bytes.
- **F04.** A box cancelled between the listing and the read is skipped; the
  scan, and so the keeper, the notifier and `health`, carries on. A 403, a
  5xx or a box that does not decode still fails it.
- **F05.** Every page is requested as a page; see above.
- **F10.** `deploy-mainnet` refuses a digest that differs from what the
  TestNet keeper runs, or that it could not read. The TestNet id is the
  constant `SOAKED_APP_ID` (769891898), changed only by commit; there is
  deliberately no flag to point the check at another app, because any TestNet
  app, including one created minutes ago from the same tree, would pass it.
- **F13.** The notifier's summary counts every run a burst made and labels
  its payment total an estimate; an execution nobody could be attributed to
  says so.
- **F14.** Every in-process shell signer that can reach MainNet pays a flat
  fee at the network minimum and refuses, before anything is signed, a node
  whose advice is above the ceiling the multisig path already used: `deploy`
  (the create and the floor payment), `govern update`, `govern freeze`, the
  unsigned `govern create`, `seed_registry --commit` (the ceremony's third
  step, three transactions per seed, which a second review round found still
  unbounded), `keeper_topup --send`, and the unattended `keeper_sweep`;
  `reclaim`'s cancel carries `max_fee` above its inner budget instead, because
  it pays for an inner payment. No override on any of them. The e2e and demo
  scripts are not bounded and are kept off MainNet by `ARCRON_ALLOW_MAINNET`
  and the mnemonic rule rather than by a fee bound; `bounded_params`'
  docstring is the inventory.
- **F07, F09.** The console reports solvency as unknown while any box is
  unreadable, bounds its per-box reads, and pages its listing; the README's
  settlement guarantee, the example target's authorization comment, the
  integrator guide's profitability sentence and `why.md`'s break-even
  arithmetic say what is true. Neither was a G2 blocker.

Still open for G2 after that: the TestNet rehearsal with a code-changing
`update` (F10), G1 itself, and the chain-facing half of F11, which no machine
without Docker can run. What was run on 2026-09-08 is under
[F11 evidence](#f11-evidence).

And after three independent reviews of the 2026-09-05 script, the same day:

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

## F11 evidence

Run on 2026-09-08 against the tree these changes were made in, Python
3.13.12 in the Poetry venv, algokit 2.10.2, Bun 1.3.11, Node 24.15.0 (the
Angular CLI refuses 22.22.2 by one patch version). No chain was reachable.

| check | result |
|---|---|
| `poetry run python -m smart_contracts build` | exit 0, zero artifact drift under `smart_contracts/artifacts` |
| `poetry run pytest tests/ -q` | green before and after every commit: 646 passed on `main`, 753 at the head of the first review round. The number will move again; the run that gates the merge is CI's. |
| `tests/test_verify_release.py` | 6 passed after `git fetch --unshallow` and tags; a shallow clone fails it for want of history, which CI avoids with `fetch-depth: 0` |
| `specsync check --strict` | **not run.** The SpecSync binary is a private release the machine could not fetch. `tests/test_specs_match_contracts.py` covers the half of it that compares the specs to the contracts; the well-formedness half is CI's. |
| `cd js && bun test` | 137 passed |
| `cd web && bun test` | 227 passed (199 before F07's 28) |
| `web-keeper`, `web-govern` `bun test` and `ng build` | 13 and 15 passed; both build |
| `web-build`, `web-build-hosted`, `web-verify-hosted` | all exit 0; the hosted bundle's 404.html is byte for byte its index.html |
| `web-render` | 40 passed, against Chromium build 1194 aliased into the revision Playwright 1.62 asks for (1234), because the download is refused from here. No CSS changed, so this is a regression check on layout and contrast rather than a review of a change. |
| LocalNet lane (`smoke-keeper`, `smoke-govern`, `smoke-multisig`, `smoke-clawback`, `attacks`, `hostile-target`, `smoke-reference-boundary`) | **not run**, no Docker. Owed by whoever runs G1, as the review said. |
| the live TestNet node's answer to a paged box listing | **not run.** The egress policy here refuses `testnet-api.algonode.cloud` and `testnet-idx.algonode.cloud` outright (HTTP 403 on CONNECT, from `curl` and from the fetch tool alike), so the F05 request shape and the F03 indexer query have only ever met fakes and the spec. |

The two probes that close the last row take a minute from any machine that
can reach TestNet, and belong in this table with their output before G2:

```sh
curl -s https://testnet-api.algonode.cloud/versions | jq .build          # major 4, minor >= 7, or 5
curl -s 'https://testnet-api.algonode.cloud/v2/applications/769891898/boxes?limit=2' | jq 'keys'   # ["boxes","next-token","round"]
fledge run health                                                          # the readers, against the real node
fledge run clock                                                           # the indexer walk: expect the alpha-3 update round, not the create
```

A `/versions` below 4.7 or a listing without `round` means every reader on
this branch refuses that node, loudly, which is the intended behaviour and
also a reason not to merge until the node in front of G1 is newer.
