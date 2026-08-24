# Archon — keeper network technical reference

Hand-off document for Archon, the permissionless keeper network. For the
quick overview see `../README.md`; for runnable flows see `../examples/`.

## TestNet deployment

| Item | Value |
|------|-------|
| Keeper app | [`769802474`](https://testnet.explorer.perawallet.app/application/769802474) |
| Pulse demo target | [`769772906`](https://testnet.explorer.perawallet.app/application/769772906) |
| Reference bot | `scripts/keeper_bot.py` |
| Proof | All 14 stages of `scripts/keeper_e2e.py` pass against it on-chain — first permissionless execution at round 66629036, catch-up stage through 66629138 |
| Deprecated | [`769772891`](https://testnet.explorer.perawallet.app/application/769772891) — see [migration](#migrating-off-the-deprecated-app) |

### Migrating off the deprecated app

App `769772891` was the first TestNet deployment. It predates the box-MBR fix
of 2026-08-24 and carries both defects that fix addressed:

1. `register` undercharged box MBR by 800 µALGO, so an app account with no
   surplus could fail to pay out an upkeep's last execution.
2. `cancel` refunded only the escrow, leaving the box MBR stranded in the app
   account with no method able to sweep it.

**If you registered an upkeep against it**, call `cancel` and you will get the
remaining escrow back. You will *not* get the box MBR back — the old contract
has no path to return it, and it cannot be upgraded. Note the old ABI is
`cancel(uint64)void`, a different selector from the current
`cancel(uint64)uint64`, so a client generated from this repo cannot call it;
build the call from the old signature directly.

Its registry is already empty: the upkeeps registered during development were
cancelled on 2026-08-24 and 40,000 µALGO of escrow reclaimed. What remains is
243,000 µALGO of stranded box MBR, permanently. That number is the clearest
argument for why the fix mattered — on the current app, registering and
cancelling is balance-neutral, and stage 11 of the e2e asserts the app account
returns to exactly its base MBR.

## Architecture

```
creator                keeper app (769802474)               target app
   | register + escrow ALGO  |                                    |
   |------------------------>|  box "u"+id: Upkeep struct         |
   |                         |                                    |
 keeper bot                  |                                    |
   | execute(upkeep_id) ---->|  inner app call ------------------>|
   |                         |  inner payment (fee) --> keeper    |
```

- One box per upkeep, name `b"u" + itob(upkeep_id)` (9 bytes).
- The registry is fully on-chain and readable with free algod box queries —
  no indexer required (the bot only uses algod).
- `execute` is atomic: the target call and the keeper payment are inner
  transactions of the same call, so a fee is only ever paid alongside a real
  execution.

## Public API

All methods are ARC-4 ABI methods on the keeper app
(`smart_contracts/keeper/contract.py`).

| Method | Callers | Purpose |
|--------|---------|---------|
| `register(mbr_payment, funding_payment, target_app, call_data, interval_rounds, fee_per_execution) → uint64` | anyone | Create an upkeep; returns its id. Two payment args fund the box MBR and the escrow. |
| `execute(upkeep_id) → uint64` | anyone (permissionless) | Fire a due, funded upkeep; pays the caller. Returns the next due round. |
| `top_up(upkeep_id, funding_payment) → uint64` | anyone | Add escrow; returns new balance. |
| `cancel(upkeep_id) → uint64` | creator only | Delete the upkeep; refunds remaining escrow **plus the box MBR** the deletion releases. Returns the refunded amount. |

Constraints (asserted on-chain):

- `interval_rounds ≥ 10`, `fee_per_execution ≥ 4_000` µALGO, call data
  `0 < len ≤ 1_024` bytes.
- Executions are NoOp inner app calls with exactly one app arg (the stored
  call data — typically the target method's 4-byte selector) and no foreign
  arrays.
- Scheduling is interval-based from the *scheduled* round:
  `next_due += interval` on each execution. An upkeep missed for many
  intervals stays due until it has caught up — there is no wall-clock clamp.

## Box encoding (Upkeep struct)

ARC-4 head/tail tuple encoding of:

```
creator: Address | target_app: uint64 | call_data: DynamicBytes
interval_rounds: uint64 | next_execution_round: uint64
fee_per_execution: uint64 | balance: uint64 | times_executed: uint64
```

| Bytes | Field |
|-------|-------|
| `[0:32]` | creator address |
| `[32:40]` | target app id |
| `[40:42]` | offset to the call_data tail (currently 82) |
| `[42:50]` | interval_rounds |
| `[50:58]` | next_execution_round |
| `[58:66]` | fee_per_execution |
| `[66:74]` | balance |
| `[74:82]` | times_executed |
| `[82:]` | tail: `uint16 length` + call data |

Reference decoder: `scripts/keeper_bot.py::_decode_upkeep`; regression vector:
`tests/test_keeper_bot.py`.

## Economics

- Creator costs, per upkeep: box MBR `2_500 + 400 × (93 + len(call_data))`
  µALGO (41,300 for a 4-byte selector) + escrowed `funding`. Both come back
  on `cancel`, so registering an upkeep costs only transaction fees in the
  end.
- Keeper costs, per execution: 1,000 µALGO outer fee + 2,000 µALGO
  `extra_fee` covering the two inner transactions (fee pooling). Paid fee is
  `fee_per_execution` (≥ 4,000), so net ≥ 1,000 µALGO per execution.
- An upkeep is executable while `balance ≥ fee_per_execution`; it goes
  dormant when underfunded and resumes after a `top_up`.

## Liveness

Archon does not execute itself. There is no on-chain timer on Algorand, so
every execution is a transaction some account sent and paid for — `execute` is
an external entry point that anyone may call once an upkeep is due.
`README.md` covers why that is the design; this is what it means to operate.

### Current coverage

| | |
|---|---|
| Keeper app | `769802474` (TestNet) |
| Upkeeps registered | none — the e2e cancels everything it creates |
| Always-on keeper | **none running** |
| Last executions | rounds 66629036–66629138, from the deployment verification |

Stated plainly because it would otherwise be inferred wrongly: an upkeep
registered against this app today would sit due until somebody started a
keeper. `deploy/` makes that a `docker compose up -d`, but nobody has.

### Funding depth is not liveness

Escrow is rarely what stops an upkeep. At the 4,000 µALGO minimum fee:

| Escrow | Executions | Hourly cadence | Daily cadence |
|--------|-----------|----------------|---------------|
| 0.1 ALGO | 25 | ~1 day | ~25 days |
| 1 ALGO | 250 | ~10 days | ~8 months |
| 100 ALGO | 25,000 | ~2.8 years | ~68 years |

A well-funded upkeep with no keeper watching does not run, and no amount of
additional escrow changes that. Conversely a keeper cannot execute an upkeep
whose escrow has fallen below one fee — `--check` calls that *starved* and
does not blame keepers for it.

### What an outage looks like afterwards

Scheduling advances from the *scheduled* round, not the round execution
happened, so nothing is skipped: an upkeep unattended for N intervals is still
due N times and catches up **one interval per call**.

Operationally that means a keeper restarted after a day-long outage will fire
a backlog — a 100-round upkeep down for a day is ~300 executions owed, and the
bot will work through them as fast as it can scan, paying ~3,000 µALGO of fees
each and collecting the fee each time. The creator pays for executions that
happened late rather than at the intended moments. Whether that is right
depends on the upkeep — a missed distribution should probably catch up, a
missed prize draw probably should not — which is the argument in
[issue #7](https://github.com/CorvidLabs/archon/issues/7).

### Rounds are not a clock

A cadence is a round count, and rounds are not seconds. TestNet measured
**2.66 s/round** over a 45-second sample; the nominal figure is 2.8. The gap
compounds:

| Cadence | Rounds | At 2.8 s | At the measured 2.66 s | Drift per cycle |
|---------|--------|----------|------------------------|-----------------|
| hourly | 1,286 | 1.0 h | 1.0 h | ~2 min |
| daily | 30,857 | 24.0 h | 22.8 h | ~1.2 h |
| weekly | 216,000 | 168.0 h | 159.7 h | ~8.3 h |

A "daily" upkeep therefore slides about **35 hours** — a day and a half —
against the calendar over thirty cycles, and which way it slides depends on
how busy the network is.

Archon promises "not before this round". It does not promise "at 00:00 UTC",
and nothing built on it should assume otherwise. Anything that must happen at
a wall-clock time needs the *target* to check the time and no-op if it is
early, with the upkeep firing often enough to catch the window.

## Operating a bot

```bash
poetry run python -m scripts.keeper_bot            # loop, block-by-block
poetry run python -m scripts.keeper_bot --once     # single scan (cron)
poetry run python -m scripts.keeper_bot --app-id N # other keeper instance
```

- Signs as `KEEPER_MNEMONIC`, else `DEPLOYER_MNEMONIC`; fees are paid to that
  account, and it pays the outer fees — keep it funded.
- Multiple competing bots are safe: the contract re-checks due-ness
  atomically, so exactly one keeper is paid per due round. **The loser pays
  nothing.** Algorand rejects a failing transaction at validation rather than
  committing it, so it never enters a block and no fee is charged — unlike an
  EVM revert, which still burns gas.

  Measured, not inferred: `scripts/keeper_e2e.py` stage 14 broadcasts a losing
  `execute` straight to algod, bypassing the simulate the typed client would
  otherwise do, and asserts the loser's balance is unchanged. algod answers
  `TransactionPool.Remember: … logic eval error` and discards it. The same
  holds for an upkeep whose target rejects the inner call: no fee, no state
  change, no escrow spent.

  So the barrier to running a keeper is lower than it looks: a bot that loses
  every race it enters is out nothing but local compute and a round-trip.
- A failing upkeep (e.g. a target that rejects the call) is still skipped for
  the rest of the bot run — for latency and transaction-pool hygiene now,
  not to avoid burning fees, since there are none to burn.

### Running one continuously

The contract is passive, so "Archon is running" means *somebody's bot is
running*. Two supported shapes, both in `deploy/`:

**Container (recommended).**

```bash
cp deploy/keeper.env.example deploy/keeper.env    # add KEEPER_MNEMONIC
docker compose -f deploy/compose.yaml up -d
docker compose -f deploy/compose.yaml logs -f
```

`restart: unless-stopped` covers reboots and crashes; the bot backs off
internally (5s doubling to 60s) so a node outage does not become a hot loop.
`docker compose down` sends SIGTERM, which finishes the scan in flight and
exits — a redeploy never abandons a half-signed execution. A second signal
exits immediately.

**systemd**, for a host that already runs Python: `deploy/keeper-bot.service`,
with the mnemonic in `/etc/archon/keeper.env` (chmod 600).

**GitHub Actions** (`.github/workflows/keeper-bot.yml`) runs `--once` on a
schedule. It is deliberately manual-dispatch-only until someone sets the
`KEEPER_MNEMONIC` secret, and it is a stopgap rather than the end state:
cron granularity is ~5 minutes and best-effort, so short-interval upkeeps are
serviced late, every run is a fresh process that re-attempts failing upkeeps,
and the mnemonic lives in repository secrets.

### Reading the logs

`--log-format json` (the container default) emits one object per line:

```json
{"event": "started", "keeper": "E5M2…FJZQ3E", "app_id": 769802474, "network": "testnet"}
{"event": "scan", "round": 66629378, "upkeeps": 3, "due": 1, "skipped": 0}
{"event": "executed", "round": 66629379, "upkeep_id": 9, "target_app": 1043,
 "fee_collected": 4000, "escrow_remaining": 8000, "next_due_round": 66629389,
 "tx_id": "F724IJ7A…UC6A"}
{"event": "execute_failed", "round": 66629380, "upkeep_id": 4, "reason": "…"}
{"event": "shutdown_requested", "signal": 15}
{"event": "stopped"}
```

`executed` is the line that answers "did upkeep N fire, and when?" months
later — it carries the round, the fee collected, what escrow was left and the
transaction id, so any claim can be checked against the chain. Use
`--log-format text` for a human at a terminal.

### Knowing it is still alive

A keeper fails silently in two ways, and both take the network down with it.

**It dies.** Nothing on-chain says so; upkeeps just quietly accumulate as due.
Every twenty scans (and on every `--once` run) the bot emits a heartbeat:

```json
{"event": "heartbeat", "round": 66629378, "upkeeps": 3, "due": 1,
 "executed_session": 12, "skipped": 0, "balance": 4192000}
```

Alert on its absence, not its content — a heartbeat that stops is the signal.

**It runs out of ALGO.** This one is nastier, because a keeper earns fees into
the same account it spends from: it is self-sustaining while the registry is
busy, and stuck the moment it is empty, with no way to earn its way back out.
So the balance is checked before the first scan and at every heartbeat:

- Below `100,000 + 3,000` µALGO — its account minimum plus one execution — the
  bot **refuses to start**, says why, and exits `2` so a supervisor notices:

  ```
  Keeper AOOZ…MZ5FOU holds 100000 µALGO, below the 103000 µALGO needed to keep
  its account and pay for one execution (3000 µALGO). Fund it before starting.
  ```

- Below `--min-balance` (default ~100 executions of headroom, or
  `KEEPER_MIN_BALANCE`) it warns each heartbeat with how many executions are
  left, and keeps working.

### Checking a keeper you do not run

`--check` reads the registry and exits without signing anything, so it works
as an external probe — you do not need the keeper's account, or its
cooperation:

```bash
poetry run python -m scripts.keeper_bot --check --network testnet
```

```
Round 3303: 3 upkeeps on app 1180, 1 stalled, 1 starved
  upkeep 35: escrow 0 µALGO is below its 4000 µALGO fee — needs a top-up, not a keeper
  upkeep 9: overdue by 1609 rounds (15.0 intervals) — nobody is servicing it
```

Exit `1` if any upkeep is stalled, `0` otherwise, so it drops straight into
cron or a monitoring check. The distinction matters: **starved** is the
creator's problem (escrow below one fee, no keeper can execute it) and
**stalled** is a keeper problem (funded, due, and nobody came). Blaming
keepers for starved upkeeps would make the signal useless.

### Giving it something to do

A bot with an empty registry is correct and useless. Register an upkeep
against the app it services — `examples/register_upkeep.py` is a working
starting point — and watch the `scan` line's `due` count move.

## Testing and CI

```bash
fledge lanes run ci       # build → unit tests → spec-sync strict check
fledge lanes run local    # ci + LocalNet e2e smoke
```

- Unit tests (`tests/`) use `algorand-python-testing` mocks. Note the mocks
  record but do not *execute* inner app calls — the Pulse counter increment
  was proven with the TestNet e2e (`scripts/keeper_testnet_demo.py`).
- Specs (`specs/keeper/`, `specs/pulse/`) are enforced by
  `specsync check --strict`; update them with any contract surface change.

## Capability boundary

Archon is the clock, not the eyes. It schedules on-chain calls; it cannot
observe the world.

- No off-chain access. Contracts have no network access, and `execute` fires an
  inner app call to another app on the same chain.
- No keeper discretion. `call_data` is fixed at registration, so a keeper
  replays exactly what the creator specified. This is what makes keepers
  trustless, and it is also why no keeper can inject fresh data.

**Oracle pairing** is the supported answer for data-driven automation: a
reporter pushes values into an oracle contract, an Archon upkeep triggers
`settle()` on a cadence, and settlement reads the stored value. Archon supplies
the timing guarantee — that settlement cannot be stalled, delayed or
selectively timed by an interested party — not the data.

One case needs no oracle trust at all: a **staleness check** that compares the
feed's last-updated round against the current round and flags the feed if it
has gone quiet. Comparing timestamps cannot be lied to.

### What an Archon-triggered call can reach

`execute` submits its inner app call with no foreign arrays, which was recorded
as a limitation without anyone establishing what it forbids. Measured on
LocalNet (algod 5.0.0 stable, `dockernet-v1`) with a probe app that reaches for
an account, an asset and a third app that no argument names —
`smart_contracts/resource_probe/` and `scripts/spike_resources.py`, so the
answer stays reproducible:

| Resource pattern | Bare `execute` | Keeper supplies references |
|------------------|----------------|----------------------------|
| Inner payment to an unreferenced account | fails — `unavailable Account …` | **works** |
| Inner asset transfer to an unreferenced account | fails — `unavailable Account …` | **works** |
| Read an unreferenced account's ALGO balance | fails — `unavailable Account …` | **works** |
| Read an unreferenced account's asset holding | fails — `unavailable Account …` | **works** |
| Inner call to an unreferenced app | fails — `unavailable App …` | **works** |

**Availability flows down from the keeper's transaction.** Resource references
attached to the keeper's own `execute` call reach Archon's inner call *and* the
target's own inner transactions, two levels down. So a keeper can supply
*availability* without supplying *data* — and the trust model does not move,
because `call_data` is still fixed at registration and the keeper still cannot
change what is called.

That makes far more buildable today than "no foreign arrays" suggests: a target
can pay an arbitrary address, move an ASA, read a balance or call another app,
provided some keeper attaches the reference.

**The budget is 8 references per transaction.** Archon spends two of them — the
upkeep's box and the target app — leaving **six** for the keeper to fill with
accounts, assets or apps in any mix. (Six accounts were accepted at this
protocol version, so the old four-account cap no longer binds separately.)

**What is still missing is discovery, not capability.** Nothing on-chain tells
a keeper which resources an upkeep needs; the reference list is not part of the
`Upkeep` struct, so a keeper would have to know out of band. Any design that
wants keeper-supplied resources needs somewhere for the creator to declare
them — which is a smaller and different problem than changing the call shape.
See [issue #8](https://github.com/CorvidLabs/archon/issues/8).

**When references are not enough** — more than six resources, or a set that is
not knowable at registration — use the pull pattern instead: have the upkeep
record what is owed in the target's own state, and let each counterparty claim
it in a transaction they send themselves, supplying their own resources. That
also sidesteps the failure mode where one unreachable account breaks the whole
execution for everybody.

## Building on Archon

The v1 hook shape is a NoOp ABI method taking no arguments of its own, called
with just its selector. Two properties matter when writing one:

- **It is called on every cadence**, whether or not there is work to do. The
  no-op path must be cheap.
- **A hook that fails trips keeper backoff** and stops being serviced. Fail
  soft — record the condition in state rather than throwing.

**The pull-payment pattern.** Scheduled calls should do accounting only and let
counterparties claim in transactions they send themselves:

```
scheduled_call()  # zero-arg, Archon calls this. Records allocations, moves nothing.
claim()           # the recipient calls this and pulls their funds.
```

This sidesteps resource availability — `Txn.sender` is always available to a
contract, whereas an arbitrary payout address may not be — and it isolates
failure: a payout to a closed or hostile account fails that claim alone instead
of failing the whole execution and disrupting the schedule. Most applications
that look like they need multi-arg calls do not, once payouts are pull-based.

## Known limitations (v1)

- ALGO escrow only (no ASA-denominated fees yet). First candidate: CORVID,
  CorvidLabs' ASA — mainnet asset
  [`3225439167`](https://explorer.perawallet.app/asset/3225439167) (6 decimals).
- Single-arg NoOp call shape; no multi-arg or foreign-array calls.
- No catch-up clamp: long-missed upkeeps fire once per round until caught up.
- Unaudited. TestNet throwaway deployer — redeploy fresh for mainnet.

## CI

`.github/workflows/ci.yml` runs on a CorvidLabs **self-hosted macOS runner**.
Every step shells out to a task in `fledge.toml` rather than restating the
command, so CI and `fledge lanes run ci` cannot drift apart.

| Job | When | What |
|-----|------|------|
| Contracts and console | every push to `main`, same-repo PRs, manual | build, unit tests, spec drift, artifacts-are-current, console tests and build |
| LocalNet end-to-end | pushes to `main`, manual | starts LocalNet, runs the keeper e2e and the timed-release demo |

The end-to-end job is kept off pull requests so the fast checks stay fast; it
needs Docker and takes minutes rather than seconds.

**Fork pull requests do not run.** A self-hosted runner executes whatever the
workflow says on hardware we own, so `build-and-test` is guarded with
`github.event.pull_request.head.repo.full_name == github.repository`. Once the
repository is public this matters a great deal: without that guard, opening a
pull request would be remote code execution on the runner. Revisit it as part
of the open-source readiness work rather than leaving it implicit.

### Registering the runner

Repository **Settings → Actions → Runners → New self-hosted runner**, then give
it the labels `self-hosted` and `macOS` (the defaults on a macOS runner). It
needs `poetry`, `bun`, `fledge` and — for the end-to-end job — `algokit` and a
running Docker on its `PATH`. The workflow's first step checks for them and
fails with a readable message rather than a mysterious "command not found".

The Python version is guarded too: 3.12 or 3.13 only, because coincurve has no
wheels for 3.14 and the failure it produces otherwise is deeply unhelpful.
