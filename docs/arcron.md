# Arcron keeper network technical reference

Hand-off document for Arcron, the permissionless keeper network. For the
quick overview see `../README.md`; for runnable flows see `../examples/`.

## TestNet deployment

| Item | Value |
|------|-------|
| Keeper app | [`769891898`](https://testnet.explorer.perawallet.app/application/769891898) |
| Pulse demo target | [`769891902`](https://testnet.explorer.perawallet.app/application/769891902) |
| Reference bot | `scripts/keeper_bot.py` |
| Proof | All 20 stages of `scripts/keeper_e2e.py` pass against it on-chain, including the box-MBR regression, the losing-keeper measurement, and the escalation-lockout and patient-keeper regressions. |
| Stage | **alpha-3**, see [release stages](releases.md) |
| Superseded | [`769823086`](https://testnet.explorer.perawallet.app/application/769823086) (alpha-1, immutable, pre-governance), [`769802474`](https://testnet.explorer.perawallet.app/application/769802474) (predates the 1.0 struct) and [`769772891`](https://testnet.explorer.perawallet.app/application/769772891); see [migration](#migrating-off-the-deprecated-app) |

### Migrating off the deprecated app

App `769772891` was the first TestNet deployment. It predates the box-MBR fix
of 2026-08-24 and carries both defects that fix addressed:

1. `register` undercharged box MBR by 800 µALGO, so an app account with no
   surplus could fail to pay out an upkeep's last execution.
2. `cancel` refunded only the escrow, leaving the box MBR stranded in the app
   account with no method able to sweep it.

**If you registered an upkeep against it**, call `cancel` and you will get the
remaining escrow back. You will *not* get the box MBR back. The old contract
has no path to return it, and it predates `update`, so its programs cannot be
replaced either. Note the old ABI is
`cancel(uint64)void`, a different selector from the current
`cancel(uint64)uint64`, so a client generated from this repo cannot call it;
build the call from the old signature directly.

Its registry is already empty: the upkeeps registered during development were
cancelled on 2026-08-24 and 40,000 µALGO of escrow reclaimed. What remains is
243,000 µALGO of stranded box MBR, permanently. That number is the clearest
argument for why the fix mattered. On the current app, registering and
cancelling is balance-neutral, and stage 11 of the e2e asserts the app account
returns to exactly its base MBR.

## Architecture

```
creator                keeper app (769891898)               target app
   | register + escrow ALGO  |                                    |
   |------------------------>|  box "u"+id: Upkeep struct         |
   |                         |                                    |
 keeper bot                  |                                    |
   | execute(upkeep_id) ---->|  inner app call ------------------>|
   |                         |  inner payment (fee) --> keeper    |
```

- One box per upkeep, name `b"u" + itob(upkeep_id)` (9 bytes).
- The registry is fully on-chain and readable with free algod box queries. No
  indexer is required (the bot only uses algod).
- `execute` is atomic: the target call and the keeper payment are inner
  transactions of the same call, so a fee is only ever paid alongside a real
  execution.

## Public API

All methods are ARC-4 ABI methods on the keeper app
(`smart_contracts/keeper/contract.py`).

**The exact signatures**, because a selector is `sha512_256(signature)[:4]` and
a table of parameter *names* is not enough to compute one. Getting this wrong
does not produce a helpful error: the contract's method router falls through to
`err`, and you see `logic eval error: err opcode executed` with no mention of
the method or the selector.

```
register(pay,pay,uint64,byte[][],uint64,uint64,uint64,uint64,uint64,uint64)uint64
execute(uint64)uint64
top_up(uint64,pay)uint64
cancel(uint64)uint64
opt_in_asset(pay,uint64,uint64)uint64
top_up_asset(uint64,axfer)uint64
freeze()void
update()void
```

Note `opt_in_asset` takes the asset as a plain `uint64`, not the ARC-4
`asset` reference type. The natural guess is wrong.

The machine-readable version is the ARC-56 spec at
`smart_contracts/artifacts/keeper/Keeper.arc56.json`, produced by
`poetry run python -m smart_contracts build`. Prefer it over this table; the
table is for reading and the JSON is for calling.

What is not in any signature, and that you cannot call `register` without.
Every one of these fails as a bare `assert` that names nothing, so getting one
wrong costs an hour of staring at a program counter:

- **Both payments go to the keeper application's own account**, the address
  derived from its app id, not to the creator and not to a keeper.
- **Both payments must be *sent by* the same account that sends the app call.**
  A third party cannot fund somebody else's registration. This surprises people,
  because `top_up` is the opposite: funding an upkeep that already exists is
  permissionless and is a gift, and funding one into existence is not.
- **The group order is `[mbr_payment, funding_payment, app call]`.**
- **The MBR payment is a minimum, not an exact amount.** Overpaying is accepted
  and is not refunded, so pay the formula.
- **The funding payment must cover at least one execution** at the price this
  upkeep can be charged, which is `fee_cap` when a ceiling is set and
  `fee_per_execution` otherwise. Registering with a token escrow to try things
  out is refused.
- **The call must carry a box reference for `b"u" + itob(n)`**, where `n` is
  the app's global `next_upkeep_id`. `register` assigns the id, so you have to
  predict it before you send: read that global key first. (A typed
  algokit-utils client does this for you. Building the group from raw algosdk,
  you have to. The alternative is a simulate with unnamed resources.)

And the one for `execute`, which was documented for `register` and not here:

- **Your `execute` transaction must itself carry the box reference for
  `b"u" + itob(upkeep_id)` and a foreign-app reference to the target.** Without
  the box you get `invalid Box`; without the app, `unavailable`. Arcron spends
  those two of the eight reference slots on your behalf in its own accounting,
  but it does not attach them to your transaction for you.

| Method | Callers | Purpose |
|--------|---------|---------|
| `register(mbr_payment, funding_payment, target_app, call_args, interval_rounds, fee_per_execution, policy, fee_cap, fee_asset, asset_fee) → uint64` | anyone | Create an upkeep; returns its id. Two payment args fund the box MBR and the escrow. `call_args` is every app arg of the call, in order. `policy` is `CATCH_UP` (0) or `SKIP_AHEAD` (1); `fee_cap` is the most one run may ever pay in ALGO, or 0 for no escalation. `fee_asset`/`asset_fee` add an ASA bonus, or 0 for ALGO only. |
| `execute(upkeep_id) → uint64` | anyone (permissionless) | Fire a due, funded upkeep; pays the caller the effective fee and records the round it ran in. Returns the next due round. |
| `top_up(upkeep_id, funding_payment) → uint64` | anyone | Add escrow; returns new balance. |
| `cancel(upkeep_id) → uint64` | creator only | Delete the upkeep; refunds remaining escrow **plus the box MBR** the deletion releases, and any unspent ASA bonus. Returns the refunded ALGO. |
| `opt_in_asset(mbr_payment, upkeep_id, asset) → uint64` | anyone | Let the app account hold an upkeep's bonus asset. 0.1 ALGO, permanent, not refundable. |
| `top_up_asset(upkeep_id, asset_funding) → uint64` | anyone | Add to an upkeep's ASA bonus escrow; returns the new asset balance. |
| `freeze() → void` | creator only | Give up the update path permanently. Sets the global `frozen` to 1, after which nobody can replace the programs. See the warning in `README.md`: until this is called, the creator can reach every escrow in the app. |
| `update() → void` | creator only | Replace the programs. Refused once `frozen` is 1. This is the power `freeze` gives up. |

Constraints (asserted on-chain):

- `interval_rounds ≥ 10`, `4_000 ≤ fee_per_execution ≤ 1_000_000_000` µALGO,
  call data `0 < len ≤ 1_024` bytes.
- `policy` is `CATCH_UP` or `SKIP_AHEAD`; `fee_cap` is either 0 or between
  `fee_per_execution` and 1,000,000,000 µALGO.
- Executions are NoOp inner app calls carrying every stored app arg, up to
  three counting the selector. That is enough for an ARC-4 method of arity two,
  and for any arity at all if the target declares its arguments as one struct.
  Foreign arrays are not stored: a keeper supplies resource references on its
  own transaction, and they reach the target (measured in #24).
- An upkeep may carry an ASA bonus paid **on top of** the ALGO fee, never
  instead of it, so no keeper needs to hold or value an asset to be paid. A
  keeper that is not opted in takes the ALGO fee and forfeits the bonus.
- Scheduling is interval-based from the *scheduled* round. Under `CATCH_UP`,
  `next_due += interval` on each execution, so an upkeep missed for many
  intervals stays due until it has caught up. Under `SKIP_AHEAD` one execution
  advances to the first slot strictly in the future that is still a whole
  number of intervals from the original schedule, so the backlog is dropped
  and the schedule keeps its phase.
- The fee paid is the **effective fee**: `fee_per_execution` when no ceiling
  is set, otherwise rising linearly to `fee_cap` across one missed interval
  and then holding. Lateness is measured from `last_serviced_round`, not from
  the schedule, so the first execution of a catch-up burst can be escalated
  and every replay behind it pays base. `balance ≥ effective fee` is what
  makes an upkeep executable, and a ceiling does **not** raise that threshold: when the escalated fee is more than the escrow holds, the fee falls back to the base and the upkeep still runs.

## Box encoding (Upkeep struct)

ARC-4 head/tail tuple encoding of:

```
creator: Address | target_app: uint64 | call_args: DynamicArray[DynamicBytes]
interval_rounds: uint64 | next_execution_round: uint64
fee_per_execution: uint64 | balance: uint64 | times_executed: uint64
policy: uint64 | fee_cap: uint64 | last_serviced_round: uint64
fee_asset: uint64 | asset_fee: uint64 | asset_balance: uint64
```

| Bytes | Field |
|-------|-------|
| `[0:32]` | creator address |
| `[32:40]` | target app id |
| `[40:42]` | offset to the call_args tail (currently 130) |
| `[42:50]` | interval_rounds |
| `[50:58]` | next_execution_round |
| `[58:66]` | fee_per_execution |
| `[66:74]` | balance |
| `[74:82]` | times_executed |
| `[82:90]` | policy |
| `[90:98]` | fee_cap |
| `[98:106]` | last_serviced_round |
| `[106:114]` | fee_asset |
| `[114:122]` | asset_fee |
| `[122:130]` | asset_balance |
| `[130:]` | tail: ARC-4 `byte[][]`, see below |

The tail is a uint16 count, then one uint16 offset per argument, then each
argument as a uint16 length followed by its bytes. **Every offset is measured
from just after the count, so add 2 to it before indexing into the tail.**

That sentence is the whole reason this section exists. Omitting it does not
raise: it yields a plausible wrong value. Decoding one real box without the +2
returns `["0004"]` where the argument is actually `["40d7be68"]`, and a keeper
built that way would mis-read every upkeep in the registry and never find out.

The head is always 130 bytes and the contract always writes 130 as the tail
offset at `[40:42]`. Both decoders reject anything else rather than reading on,
because a box from an older deployment is shorter and reading past its end
silently yields zeros.

### Global state

| Key | Meaning |
|-----|---------|
| `next_upkeep_id` | The id `register` will assign next. Read this to predict the box name your `register` group must reference. |
| `frozen` | 0 while the creator can still replace the programs, 1 once `freeze` has been called. An app deployed before governance carries no `frozen` key at all, and a missing flag reads as frozen rather than unknown, because such an app has no update path. |

Reference decoder: `scripts/keeper_bot.py::_decode_upkeep`; its TypeScript twin
is `js/src/upkeep.ts`. Both are pinned to the *same* recorded box, in
`tests/test_keeper_bot.py` and `js/test/upkeep.test.ts`, so they
cannot drift apart.

## Economics

- Creator costs, per upkeep: box MBR `2_500 + 400 × (139 + len(encoded call_args))`
  µALGO (62,100 for a bare 4-byte selector) + escrowed `funding`. Both come back
  on `cancel`, so registering an upkeep costs only transaction fees in the
  end.
- **Post-quantum accounts work, with one thing to watch.** Algorand 5 derives a
  Falcon-1024 account's address as a 32-byte hash chosen not to be an ed25519
  curve point, so it is an ordinary address, and the contract, which only ever
  compares addresses and pays `Txn.sender`, cannot tell the difference. A
  creator or a keeper may be post-quantum today (`scripts/spike_quantum.py`
  confirms algod runs a real Falcon verification). But a Falcon-signed
  `execute` is **4,384 bytes against ed25519's 340, or 12.9×**, and Algorand
  charges `max(min_fee, size × fee_per_byte)`. That per-byte rate is zero
  today, which is the only reason `MIN_UPKEEP_FEE` still covers a
  post-quantum keeper. The floor is permanent and cannot be raised, so a chain
  that ever prices bytes would leave post-quantum keepers under-paid.
- Keeper costs, per execution: 1,000 µALGO outer fee + 2,000 µALGO
  `extra_fee` covering the two inner transactions (fee pooling). Paid fee is
  the effective fee (≥ 4,000), so net ≥ 1,000 µALGO per execution, and more
  when the upkeep is late and its creator set a ceiling.
- An upkeep is executable while `balance ≥ fee_per_execution`; it goes dormant
  when underfunded and resumes after a `top_up`. A ceiling does not raise that
  threshold: when the escalated fee is more than the escrow holds, the fee
  falls back to the base, so the upkeep stays executable by anyone rather than
  stranding a ceiling's worth of escrow nobody can spend. Budget runway
  against `fee_cap` anyway, since a late run can consume that much.

## Liveness

Arcron does not execute itself. There is no on-chain timer on Algorand, so
every execution is a transaction some account sent and paid for. `execute` is
an external entry point that anyone may call once an upkeep is due.
`README.md` covers why that is the design; this is what it means to operate.

### Current coverage

| | |
|---|---|
| Keeper app | `769891898` (TestNet, alpha-3) |
| Upkeeps registered | 11 as of round 66707000 on 2026-08-26, including one registered from the console by a wallet |
| Always-on keeper | **running**: `.github/workflows/keeper-bot.yml` every thirty minutes, plus a container on a VPS and a second cron on the same barrier. The workflow was manual-dispatch-only until its `KEEPER_MNEMONIC` secret was set on 2026-08-26. |
| Executions | 70 as of round 66707000 on 2026-08-26, the most recent paid to a keeper for real |

This table said "none" and "none running" for a day after both stopped being
true, which is worth more than the correction. Two independent bugs kept it
accurate by accident: the cron keeper was green roughly forty-eight times a day
while skipping for a missing secret, and the local keeper was servicing a
superseded app. Nothing was watching either. `scripts/verify_release.py` now
runs daily, and a `snapshot` task exists for exactly this table.

### Funding depth is not liveness

Escrow is rarely what stops an upkeep. At the 4,000 µALGO minimum fee:

| Escrow | Executions | Hourly cadence | Daily cadence |
|--------|-----------|----------------|---------------|
| 0.1 ALGO | 25 | ~1 day | ~25 days |
| 1 ALGO | 250 | ~10 days | ~8 months |
| 100 ALGO | 25,000 | ~2.8 years | ~68 years |

A well-funded upkeep with no keeper watching does not run, and no amount of
additional escrow changes that. Conversely a keeper cannot execute an upkeep
whose escrow has fallen below one fee. `--check` calls that *starved* and
does not blame keepers for it.

### What an outage looks like afterwards

Scheduling advances from the *scheduled* round, not the round execution
happened, so nothing is skipped: an upkeep unattended for N intervals is still
due N times and catches up **one interval per call**.

Operationally that means a keeper restarted after a day-long outage will fire
a backlog: a 100-round upkeep down for a day is ~300 executions owed, and the
bot will work through them as fast as it can scan, paying ~3,000 µALGO of fees
each and collecting the fee each time. The creator pays for executions that
happened late rather than at the intended moments. Whether that is right
depends on the upkeep. A missed distribution should probably catch up; a
missed prize draw probably should not. That is the argument in
[issue #7](https://github.com/CorvidLabs/arcron/issues/7).

### Rounds are not a clock

A cadence is a round count, and rounds are not seconds. TestNet measured
**2.66 s/round** over a 45-second sample; the nominal figure is 2.8. The gap
compounds:

| Cadence | Rounds | At 2.8 s | At the measured 2.66 s | Drift per cycle |
|---------|--------|----------|------------------------|-----------------|
| hourly | 1,286 | 1.0 h | 1.0 h | ~3 min |
| daily | 30,857 | 24.0 h | 22.8 h | ~1.2 h |
| weekly | 216,000 | 168.0 h | 159.7 h | ~8.3 h |

A "daily" upkeep therefore slides about **36 hours** (a day and a half)
against the calendar over thirty cycles, and which way it slides depends on
how busy the network is.

Arcron promises "not before this round". It does not promise "at 00:00 UTC",
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
  account, and it pays the outer fees, so keep it funded.
- **How many keepers this needs: two or three, not a crowd.** One keeper is a
  loop over boxes, so it does not shard: ten thousand upkeeps on a one hour
  cadence average 7.8 due per round, which is one machine's work. Keeper count
  is a liveness question, not a throughput one, and the escalating fee exists to
  recruit the second keeper when the first stops rather than to run an auction.
  The arithmetic and the evidence are in [prior art](prior-art.md).
- Multiple competing bots are safe: the contract re-checks due-ness
  atomically, so exactly one keeper is paid per due round. **The loser pays
  nothing.** Algorand rejects a failing transaction at validation rather than
  committing it, so it never enters a block and no fee is charged, unlike an
  EVM revert, which still burns gas.

  Measured, not inferred: `scripts/keeper_e2e.py` stage 14 broadcasts a losing
  `execute` straight to algod, bypassing the simulate the typed client would
  otherwise do, and asserts the loser's balance is unchanged. algod answers
  `TransactionPool.Remember: … logic eval error` and discards it. The same
  holds for an upkeep whose target rejects the inner call: no fee, no state
  change, no escrow spent.

  Measured between two keepers that genuinely collided, not only by
  construction: `scripts/keeper_race.py` starts two real bots against a shared
  wall-clock barrier so both reach for the same due upkeep in the same round,
  and then checks the claim from chain data: the winner named out of the
  block it landed in, the loser's transaction absent from any indexer, the
  loser's balance moved by exactly zero. Stage 14b of the e2e pins the same
  thing in the ordinary shape of a race, where the loser is not refused before
  it broadcasts but by the pool after it does.

  So the barrier to running a keeper is lower than it looks: a bot that loses
  every race it enters is out nothing but local compute and a round-trip.
- **Two keepers only compete if they run at the same time.** An offset
  schedule is a queue: the earlier keeper takes every due upkeep and the later
  one finds nothing. `--align SECONDS` holds the first scan until the next
  whole multiple of SECONDS in UTC, so keepers that have never met scan the
  same round. Both scheduled workflows use it; see `docs/hosting.md`.
- A failing upkeep (e.g. a target that rejects the call) **backs off
  exponentially**, and that state survives restarts, so a `--once` cron
  invocation does not re-attempt a doomed upkeep on every run, which the old
  skip-for-the-rest-of-this-run behaviour did.

  The schedule is deliberately gentle, because failing costs nothing: the wait
  doubles in the upkeep's own intervals up to 8×, but is capped at ~1,286
  rounds (about an hour) in absolute terms. Without that cap a daily upkeep
  would go unretried for over a week, and the only thing that buys is a slow
  recovery once someone fixes the target. A success resets it to zero.

  **Losing a race never backs off.** Another keeper getting there first is the
  common case in a healthy network, it is free, and a keeper that stopped
  trying everything it lost a race for would service less and less of the
  registry.

  Two signals separate a lost race from a broken target, and they are not
  equally trustworthy. The error text is what arrives first, and a target has
  some say in it: on-chain failures carry no assert strings, but algod
  disassembles the failing program into the message, so a target can get
  chosen words in front of a keeper. What it cannot do is fail without the
  node saying the failure was in an inner transaction, because `execute`
  checks the schedule before it calls anything. The second signal is the
  registry itself: if the upkeep's box moved on between the scan that picked
  it and the call that failed, somebody executed it, and nothing a target
  writes can fake that. The box is only ever evidence *for* a race, because a
  winner still sitting in the pool has not moved it yet, so a keeper reads both.

  Once you have fixed a target: `--retry-now <id>` clears one upkeep's
  backoff, `--clear-backoff` clears them all. State lives under
  `XDG_STATE_HOME` per network and app, or wherever `--state-file` says;
  `--no-state` keeps it in memory only.

### Running one continuously

The contract is passive, so "Arcron is running" means *somebody's bot is
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
exits, so a redeploy never abandons a half-signed execution. A second signal
exits immediately.

**systemd**, for a host that already runs Python: `deploy/keeper-bot.service`,
with the mnemonic in `/etc/arcron/keeper.env` (chmod 640, owned root:keeper).

**GitHub Actions** (`.github/workflows/keeper-bot.yml`) runs `--once` on a
schedule. It was deliberately manual-dispatch-only until somebody set the
`KEEPER_MNEMONIC` secret, and it is a stopgap rather than the end state:
cron granularity is ~5 minutes and best-effort, so short-interval upkeeps are
serviced late, every run is a fresh process that re-attempts failing upkeeps,
and the mnemonic lives in repository secrets.

### Reading the logs

`--log-format json` (the container default) emits one object per line:

```json
{"event": "started", "keeper": "E5M2…FJZQ3E", "app_id": 769891898, "network": "testnet"}
{"event": "scan", "round": 66629378, "upkeeps": 3, "due": 1, "skipped": 0}
{"event": "executed", "round": 66629379, "upkeep_id": 9, "target_app": 1043,
 "fee_collected": 4000, "escrow_remaining": 8000, "next_due_round": 66629389,
 "tx_id": "F724IJ7A…UC6A"}
{"event": "execute_failed", "round": 66629380, "upkeep_id": 4, "reason": "…"}
{"event": "shutdown_requested", "signal": 15}
{"event": "stopped"}
```

`executed` is the line that answers "did upkeep N fire, and when?" months
later. It carries the round, the fee collected, what escrow was left and the
transaction id, so any claim can be checked against the chain. Use
`--log-format text` for a human at a terminal.

### Announcing what happened

A network whose work is invisible looks dead even when it is running fine.
`scripts/notifier.py` watches the registry and says what changed, either to a
Discord channel or to the terminal when no webhook is set:

```bash
poetry run python -m scripts.notifier --network testnet          # prints here
DISCORD_WEBHOOK_URL=https://… poetry run python -m scripts.notifier --network testnet
docker compose -f deploy/compose.yaml up -d notifier              # alongside the keeper
```

```
**Upkeep 9 executed**, 0.004 ALGO paid, next due at round 2976
↳ keeper `FIYLSRRX…XO4LGA`
⚠️ **Upkeep 4 has run dry**: escrow 0.001 ALGO is below its 0.004 ALGO fee,
   so no keeper can run it. Anyone can top it up.
⚠️ **Upkeep 7 is going unserviced**. Funded and due, but 812 rounds late.
```

Three properties worth knowing:

- **It holds no keys and cannot sign.** A notifier that could sign would be a
  liability with no upside, so that is enforced by a test rather than promised
  in a comment: `tests/test_notifier.py` fails if anything key-shaped appears
  in the module.
- **It needs no indexer, even for "which keeper".** Box state records that an
  upkeep ran, never who ran it, but the notifier knows the rounds between its
  last scan and this one, so it reads those few blocks directly. Attribution
  is derived from the *execution* round rather than the upkeep's scheduled
  round, which differ whenever an upkeep is catching up after an outage.
- **Restarting is quiet.** The last announced state is persisted, so a restart
  replays nothing. Starting fresh against a busy app announces what is
  currently *broken* and stays silent about what is merely healthy.

It surfaces failures deliberately. An upkeep out of funds, or funded and due
with nobody servicing it, is the network not working, and saying so builds
more trust than a feed of good news.

### Knowing it is still alive

A keeper fails silently in two ways, and both take the network down with it.

**It dies.** Nothing on-chain says so; upkeeps just quietly accumulate as due.
Every twenty scans (and on every `--once` run) the bot emits a heartbeat:

```json
{"event": "heartbeat", "round": 66629378, "upkeeps": 3, "due": 1,
 "executed_session": 12, "skipped": 0, "balance": 4192000}
```

Alert on its absence, not its content. A heartbeat that stops is the signal.

**It runs out of ALGO.** This one is nastier, because a keeper earns fees into
the same account it spends from: it is self-sustaining while the registry is
busy, and stuck the moment it is empty, with no way to earn its way back out.
So the balance is checked before the first scan and at every heartbeat:

- Below `100,000 + 3,000` µALGO (its account minimum plus one execution) the
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
as an external probe. You do not need the keeper's account, or its
cooperation:

```bash
poetry run python -m scripts.keeper_bot --check --network testnet
```

```
Round 3303: 3 upkeeps on app 1180, 1 stalled, 1 starved
  upkeep 35: escrow 0 µALGO is below its 4000 µALGO fee. It needs a top-up, not a keeper
  upkeep 9: overdue by 1609 rounds (15.0 intervals); nobody is servicing it
```

Exit `1` if any upkeep is stalled, `0` otherwise, so it drops straight into
cron or a monitoring check. The distinction matters: **starved** is the
creator's problem (escrow below one fee, no keeper can execute it) and
**stalled** is a keeper problem (funded, due, and nobody came). Blaming
keepers for starved upkeeps would make the signal useless.

### Giving it something to do

A bot with an empty registry is correct and useless. Register an upkeep
against the app it services (`examples/register_upkeep.py` is a working
starting point) and watch the `scan` line's `due` count move.

## Testing and CI

```bash
fledge lanes run ci       # build → unit tests → spec-sync strict check
fledge lanes run local    # ci + LocalNet e2e smoke
```

- Unit tests (`tests/`) use `algorand-python-testing` mocks. Note the mocks
  record but do not *execute* inner app calls; the Pulse counter increment
  was proven with the TestNet e2e (`scripts/keeper_testnet_demo.py`).
- Specs (`specs/keeper/`, `specs/pulse/`) are enforced by
  `specsync check --strict`; update them with any contract surface change.

## Capability boundary

Arcron is the clock, not the eyes. It schedules on-chain calls; it cannot
observe the world.

- No off-chain access. Contracts have no network access, and `execute` fires an
  inner app call to another app on the same chain.
- No keeper discretion. `call_args` is fixed at registration, so a keeper
  replays exactly what the creator specified. This is what makes keepers
  trustless, and it is also why no keeper can inject fresh data.

**Oracle pairing** is the supported answer for data-driven automation: a
reporter pushes values into an oracle contract, an Arcron upkeep triggers
`settle()` on a cadence, and settlement reads the stored value. Arcron supplies
the timing guarantee: settlement cannot be stalled, delayed or selectively
timed by an interested party. It does not supply the data.

One case needs no oracle trust at all: a **staleness check** that compares the
feed's last-updated round against the current round and flags the feed if it
has gone quiet. Comparing timestamps cannot be lied to. A previous revision of
this page had a worked example of this pattern (`smart_contracts/watchdog/`);
it was cut from the repository on 2026-08-26 as one of four example contracts
whose review findings outweighed their purpose as illustrations, so this page
now states the pattern rather than pointing at a shipped instance of it.

The shape stays the same whichever contract implements it: the value comes
from the reporter, and Arcron does not reduce the trust that requires; that a
value arrived at all is the chain's own record; and that nobody has noticed a
silence is what Arcron adds, because it only compares round numbers and
cannot be fed a wrong price. Two things are worth keeping if you build one:
let the flag clear on the next update rather than needing an authority to
clear it, and keep the threshold in rounds and no tighter than Arcron's own
10-round cadence minimum, since rounds are not wall-clock time (see
[Liveness](#liveness)) and a tight threshold flags ordinary keeper lateness as
a provider outage.

### What an Arcron-triggered call can reach

`execute` submits its inner app call with no foreign arrays, which was recorded
as a limitation without anyone establishing what it forbids. Measured on
LocalNet (algod 5.0.0 stable, `dockernet-v1`) with a probe app that reaches for
an account, an asset and a third app that no argument names, using
`smart_contracts/resource_probe/` and `scripts/spike_resources.py` so the
answer stays reproducible:

| Resource pattern | Bare `execute` | Keeper supplies references |
|------------------|----------------|----------------------------|
| Inner payment to an unreferenced account | fails: `unavailable Account …` | **works** |
| Inner asset transfer to an unreferenced account | fails: `unavailable Account …` | **works** |
| Read an unreferenced account's ALGO balance | fails: `unavailable Account …` | **works** |
| Read an unreferenced account's asset holding | fails: `unavailable Account …` | **works** |
| Inner call to an unreferenced app | fails: `unavailable App …` | **works** |

**Availability flows down from the keeper's transaction.** Resource references
attached to the keeper's own `execute` call reach Arcron's inner call *and* the
target's own inner transactions, two levels down. So a keeper can supply
*availability* without supplying *data*, and the trust model does not move,
because `call_args` is still fixed at registration and the keeper still cannot
change what is called.

That makes far more buildable today than "no foreign arrays" suggests: a target
can pay an arbitrary address, move an ASA, read a balance or call another app,
provided some keeper attaches the reference.

**The budget is 8 references per transaction, and the reference keeper
services all six a target can be given.** Arcron spends two of them (the
upkeep's box and the target app), leaving **six** for the keeper to fill with
accounts, assets or apps in any mix. Six accounts were accepted at this
protocol version, so the old four-account cap no longer binds at the AVM, and
`scripts/keeper_bot.py` no longer stops short of it either.

That second half used to be false, and the failure mode is worth knowing even
though it is fixed here, because a keeper built by copying algokit-utils'
default pattern will still hit it. `send.execute` used to leave every
reference to algokit-utils' typed client, whose default resource populator
caps at four direct account references per transaction and refuses a fifth
with "No more transactions below reference limit", a client-side ceiling that
predates the AVM allowing six, not a protocol one. `scripts/keeper_bot.py` now
simulates the call itself first
(`scripts/keeper_bot.py::_resolve_execute_references`), names every account,
app, asset and box the simulation reports directly on the transaction, and
tells the populator not to run at all, the same recipe
`scripts/spike_simulate_test_button.py` (section 5b) proved by hand. Copy that
pattern rather than a bare `send.execute` if you are building your own keeper
and want it as capable as the protocol.

`scripts/reference_boundary.py` pins the result on LocalNet through the real
bot: a `needs_six()` target is serviced, and a `needs_seven()` one is refused
with `tx references exceed MaxAppTotalTxnReferences = 8`, a genuine AVM
ceiling rather than a client one. Both are asserted every run of the `local`
lane (`smoke-reference-boundary` in `fledge.toml`), so a future change to
algokit-utils' populator cap regresses this loudly instead of quietly.

**What is still missing is discovery, not capability.** Nothing on-chain tells
a keeper which resources an upkeep needs; the reference list is not part of the
`Upkeep` struct, so a keeper would have to know out of band. Any design that
wants keeper-supplied resources needs somewhere for the creator to declare
them, which is a smaller and different problem than changing the call shape.
See [issue #8](https://github.com/CorvidLabs/arcron/issues/8).

**Pull the resource as well as the payment.** The pull-payment pattern above
extends past money: any step needing a resource the keeper cannot supply
belongs in a transaction the interested party sends for themselves. The
scheduled call does accounting only, and someone with skin in the game
provides the references.

`smart_contracts/rain/` is the worked example. A scheduled `draw()` locks a
prize and fixes a future randomness-beacon round, with no inner calls and
nothing unreachable. A participant then calls `resolve()`, attaching the beacon
reference a keeper could not, and the winner calls `claim()`. The beacon is
never in the path of a scheduled execution, so a beacon outage cannot stall
the schedule for everyone.

**Randomness beacon app ids**, verified by searching the deployed approval
programs for the ARC-21 selector rather than trusting documentation:

| Network | App | Notes |
|---------|-----|-------|
| MainNet | `1615566206` | implements `must_get(uint64,byte[])byte[]` |
| TestNet | `600011887` | same program size; the current beacon |
| MainNet | `947957720` | an older, smaller program; also `must_get` |
| TestNet | `110096026` | older |
| LocalNet | n/a | **no beacon exists**; `smart_contracts/beacon_stub/` stands in |

None of them implement `get(uint64,byte[])(bool,byte[])`, so there is no
non-throwing variant to fall back on.

**When references are not enough** (more than six resources, or a set that is
not knowable at registration), use the pull pattern instead: have the upkeep
record what is owed in the target's own state, and let each counterparty claim
it in a transaction they send themselves, supplying their own resources. That
also sidesteps the failure mode where one unreachable account breaks the whole
execution for everybody.

## Building on Arcron

The v1 hook shape is a NoOp ABI method taking no arguments of its own, called
with just its selector. Two properties matter when writing one:

- **It is called on every cadence**, whether or not there is work to do. The
  no-op path must be cheap.
- **A hook that fails trips keeper backoff** and stops being serviced. Fail
  soft: record the condition in state rather than throwing.

**The pull-payment pattern.** Scheduled calls should do accounting only and let
counterparties claim in transactions they send themselves:

```
scheduled_call()  # zero-arg, Arcron calls this. Records allocations, moves nothing.
claim()           # the recipient calls this and pulls their funds.
```

This sidesteps resource availability (`Txn.sender` is always available to a
contract, whereas an arbitrary payout address may not be) and it isolates
failure: a payout to a closed or hostile account fails that claim alone instead
of failing the whole execution and disrupting the schedule. Most applications
that look like they need multi-arg calls do not, once payouts are pull-based.

## What 1.0 will be

An update replaces code, not the shape of boxes that already exist, so a
struct change means a new app id however a deployment is governed. Struct
changes are therefore batched into one last release and the surface is then
frozen: per-upkeep catch-up policy, fee
escalation, resource declaration, and ASA-denominated fees as a capability
(ALGO remains the default; no token is required). Scope, what is deliberately
out, the dogfood plan and the mainnet gate are in
[docs/design/1.0.md](design/1.0.md).

## Known limitations (v1)

- ASA fees are a **capability, not a commitment**: escrow and fees are ALGO by
  default and no token is required. An upkeep may add a bonus in any asset;
  CORVID (mainnet asset
  [`3225439167`](https://explorer.perawallet.app/asset/3225439167), 6 decimals)
  is a candidate, wired in nowhere.
- Three app args per execution, counting the selector. Foreign arrays are
  supplied by the keeper rather than stored, and there is no on-chain way for
  an upkeep to declare which resources it needs. It does not need one: a
  keeper that simulates the call first has algod report what it touched, and
  attaches those references. That is a property of the keeper, not of the
  network: the Python bot does it itself, because algokit-utils' default populator caps at four references, and
  `js/src/keeper-txns.ts` (which the console also imports) does the same
  thing itself, against raw algosdk, so an upkeep reaching an account, asset
  or app beyond the target itself is servable from either. A `resources()`
  declaration convention was proposed and then withdrawn, because simulation
  already answers the same question for every target, including ones written
  before the convention existed. See [Reaching resources your hook cannot name](integrating.md#reaching-resources-your-hook-cannot-name)
  and [docs/design/call-shapes.md](design/call-shapes.md).
- The console shows ASA bonuses in base units, not the asset's decimals.
- Catch-up is now a choice, not a limitation: a creator picks `CATCH_UP`
  (replay every missed interval, the default) or `SKIP_AHEAD` (run once and
  land on the next slot that is still ahead) at registration. Designed in
  [docs/design/scheduling-and-fees.md](design/scheduling-and-fees.md), which
  also explains why the two features had to be designed together.
- A creator may also set a fee ceiling, and a late upkeep's fee climbs towards
  it. That raises what a late run can consume, so budget runway against the
  ceiling, but it does not raise the balance at which an upkeep goes dormant:
  when the escalated fee is more than the escrow holds, the fee falls back to
  the base and the upkeep stays executable.
- Unaudited. TestNet throwaway deployer; redeploy fresh for mainnet.


### Continuous integration

`.github/workflows/ci.yml` runs on GitHub's hosted runners, for this
repository's branches and for any fork's pull request alike, with no secrets and
no second job that could fall behind the first.

There is deliberately **no self-hosted runner**. A self-hosted runner executes
whatever a workflow says on hardware somebody owns, so on a public repository
"open a pull request" starts to mean "run this on someone's Mac". Guarding that
with a condition works until the condition is edited; not having the runner
cannot be edited wrong.

The workflow reads the `ci` lane's step list out of `fledge.toml` rather than
repeating it, so CI and `fledge lanes run ci` cannot disagree about what CI
means. They did once: the repeated copy had lost `js-install` and `js-test`, so
123 tests ran nowhere, and nobody noticed because the copy still looked
plausible.

Nothing has to be registered or installed for CI to work. The hosted runner
installs `poetry`, `bun` and `algokit` itself, pinned in the workflow so a new
major of any of them does not arrive unannounced. Docker for the end-to-end job
is already on GitHub's ubuntu image.

The Python version is pinned to 3.13, because coincurve has no wheels for 3.14
and the failure it produces otherwise is deeply unhelpful.
