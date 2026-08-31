# Where to run a keeper

A keeper is a plain process that watches rounds and calls `execute` on due
upkeeps. It holds a hot key, it needs to be up, and it earns fees. Nothing
about it is special to us. The network is permissionless, so these are the
options for anybody, not for our deployment alone.

The requirement is more forgiving than it first looks. Upkeeps here run on
cadences of hours, and a neglected upkeep's fee *escalates* toward its cap. A
keeper that checks every fifteen minutes services a six-hour upkeep perfectly
well. Latency only starts to matter when keepers are competing for the same
upkeep, which is not yet true.

## The options

| | Cost | Uptime | Key lives | Effort |
|---|---|---|---|---|
| **A server you already run** | nothing extra | continuous | on your box | one script |
| GitHub Actions cron | free to ~$115/mo, by cadence | ~7% of schedule, measured | repo secrets | uncomment a line |
| A small always-on host | ~$2 to $5/mo | continuous | on that host | container |
| A laptop | nothing | poor | on your laptop | one plist |

### A. A server you already run (recommended)

If you have a VPS doing anything else, put the keeper on it. It is a small
Python process; it will not notice.

```bash
./deploy/vps/package.sh                                  # builds a 392 KB tarball
scp /tmp/arcron-keeper.tar.gz <user>@<host>:/tmp/
ssh <user>@<host> 'sudo mkdir -p /tmp/arcron-install \
    && sudo tar -xzf /tmp/arcron-keeper.tar.gz -C /tmp/arcron-install \
    && sudo bash /tmp/arcron-install/deploy/vps/install.sh'
```

Then add the mnemonic and start it:

```bash
sudo -e /etc/arcron/keeper.env     # KEEPER_MNEMONIC=
sudo systemctl start keeper-bot
sudo journalctl -u keeper-bot -f
```

The archive carries no secrets, so the mnemonic never rides over the wire in a
file: it is typed on the host, into a file the installer creates `640
root:keeper`. Re-running the installer upgrades in place and leaves that file
alone. The contracts are not compiled on the far end, because
`smart_contracts/artifacts/` is committed and the bot only reads boxes and
calls a generated client.

Nothing in the tarball is specific to our deployment. `KEEPER_APP_ID` names
the app to service.

### B. GitHub Actions on a schedule

`.github/workflows/keeper-bot.yml` does this, and the schedule is live at
half-hourly. It skips itself cleanly when `KEEPER_MNEMONIC` is not set, so the
workflow being on before the secret exists produces a green run with a notice
rather than a failure every half hour that everyone learns to ignore.

**It does not run half-hourly.** That is not a caveat about load, it is the
normal behaviour, and it was measured on 2026-08-29 across two independent
workflows in this repository, both scheduled `*/30`. Six more days have not
improved it: scheduled runs delivered per day since then were 4, 16, 3, 2, 6
and 5, against 48 asked for. Option D turns the same bot into a launchd agent
that watches every block, and is the answer if this is the only thing keeping
your upkeeps overdue.

| Workflow | Runs | Window | Expected | Delivered | Mean gap |
|---|---|---|---|---|---|
| `keeper-bot` | 6 | 41.2 h | 82 | **7%** | 8.2 h |
| `keeper-bot-2` | 5 | 25.0 h | 50 | **10%** | 6.3 h |

The gaps on the first were 8.4, 12.9, 7.4, 7.0 and 5.5 hours. There is no
half-hour interval anywhere in the history. The mean gap is **sixteen times the
schedule**, and the second workflow fails the same way on its own, which is
what rules out anything particular to the job: it is the platform.

Two upkeeps went 3,524 and 2,104 rounds unserviced during one of those gaps and
climbed to their fee ceiling before a keeper took them. Nothing was lost, and
the escalating fee did exactly what it exists for, but the schedule you write
in the cron expression is not the schedule you get.

To start keeping, add `KEEPER_MNEMONIC` as a repository secret. Generate the
account yourself and give it only what it needs: it refuses to start below
103,000 microAlgos, and a couple of ALGO of float is plenty while fees top it
up.

Half-hourly was chosen against the upkeeps that existed when it was written,
and those have changed. That justification said the shortest live cadence was
about six hours, so a half-hourly check was late by at most eight percent of
one interval. On 2026-08-27 the shortest executable cadence was **ten minutes**,
which makes a half-hourly keeper **three intervals late**, not a fraction of
one. The claim was overstated by a factor of about thirty-six.

Take the cadence you actually register seriously, then. A half-hourly keeper
suits work measured in hours. Below that it will service your upkeep eventually
and not on time, and with `CATCH_UP` it will replay every interval it missed at
one fee each, which is how upkeep 18 burned its entire escrow and advanced 41
rounds against a 23,478 round backlog. If you need minutes, run something that
polls in minutes: `deploy/compose.yaml` is that, and it is the reason the cron
is described here as a stopgap.

**The cost is the thing to check first**, because it is billed per minute on a
private repository and a keeper runs constantly. A run is roughly two minutes,
mostly dependency installation:

| Cadence | Runs/month | Minutes | Cost beyond a 3,000-minute allowance |
|---|---|---|---|
| every 5 min | 8,640 | ~17,300 | **~$115/month** |
| every 15 min | 2,880 | ~5,800 | ~$22/month |
| every 30 min | 1,440 | ~2,900 | free, but it consumes the whole allowance |

Caching the dependency install roughly halves those. A 30-minute cadence is
genuinely fine for six-hour upkeeps.

The things that are not about money:

- **Scheduled workflows are best-effort.** GitHub delays them under load and
  may drop them. Fine for servicing upkeeps that escalate anyway; poor as
  *evidence* of continuous uptime, which is what the beta gate asks for.
- **In a public repository, scheduled workflows are disabled after 60 days
  without repository activity.** A quiet month turns the keeper off silently.
  A private repository is not auto-disabled, so this applies to Arcron now and
  did not while the repository was private. GitHub states the rule with that
  qualifier and this page did not, which mattered in the direction that lets
  somebody plan around a limit they do not have.
- **The mnemonic sits in repository secrets**, readable by any workflow in the
  repository. On TestNet, with a throwaway account holding a little ALGO, that
  is a small exposure. On MainNet it is not one to accept casually.
- **Each run is a fresh process**, so `scripts/keeper_backoff.py` has no memory
  between runs and a persistently failing upkeep is retried every time.

Good as a backstop next to a real keeper.

### B2. There was a second keeper. It never ran.

There was a second keeper workflow beside `keeper-bot.yml`, on the *same* cron as
the first, signing from `KEEPER_2_MNEMONIC`, and the reasoning for it was
sound: Arcron's economic argument is that competition between keepers holds
the fee below the ceiling, and that competing is safe because losing a race
costs nothing. Neither had ever happened on a real chain. One keeper serviced
TestNet, won everything by default, and a keeper that never loses a race is no
evidence at all about what losing costs.

**The secret was never set, so the job skipped itself every single time.** It
was added on 2026-08-27 and removed on 2026-08-31, and in that window it ran
on schedule dozens of times, took about twenty seconds, printed a notice, and
exited green. Every run is in the Actions history and not one of them signed
anything. The workflow was written to fail politely when unconfigured, which
it did, forever, while the docs described a race that was not occurring.

It is deleted rather than left waiting for a credential. A workflow whose
whole behaviour is "exit green without doing the thing" is worse than absent:
it makes the Actions history look like two keepers are running.

The design note is worth keeping, because whatever races next has to get it
right. **An offset schedule does not race.** Two keepers thirty minutes apart
never contend for anything: the first takes every due upkeep and the second
arrives to an empty registry. That looks like redundancy and is a queue. Even
the same cron is not enough, because two runners finish installing
dependencies tens of seconds apart and the slower one finds the work done.
Both workflows therefore passed `--align 120`, holding the first scan until
the next whole two-minute mark in UTC. Runner clocks are NTP-synced, so an
absolute instant is the one thing two machines that have never met can agree
on. `--align` is still in `scripts/keeper_bot.py` and still the right tool;
`scripts/keeper_race.py` still proves the collision on demand.

### C. A small always-on host

`deploy/Dockerfile` and `deploy/compose.yaml` are ready. Fly.io, Railway,
Render or a $4 Hetzner box all work. Worth it only if you would rather not put
this on a server that does something else.

### D. A laptop, under launchd

```bash
fledge run keeper-daemon-install -- --sweep-to <your wallet> \
    --sweep-above 2000000 --sweep-every 86400
fledge run keeper-daemon-status
tail -f ~/Library/Logs/arcron/keeper-testnet.log
```

Both triggers, deliberately: the threshold moves a large balance out of a hot
key promptly, and the period catches slow accumulation. Either alone is valid.

`scripts/keeper_daemon.py` generates the plist, writes it to
`~/Library/LaunchAgents`, and boots it. There is no template to hand-edit:
paths that were `CHANGEME` in a checked-in plist are the reason a keeper
starts once, fails on the third path, and is discovered a week later.

It will not install until you have either named a wallet for the earnings or
passed `--no-sweep`. The bot signs from the account it earns into, and this is
the first thing in the repository meant to run unattended for weeks; leaving
the surplus in a hot key should be a decision, not a default. Everything else
about the sweep -- a bad address, the keeper's own address, a destination with
no trigger -- is checked by calling `keeper_bot._validate_sweep`, the same
function the bot runs before its first scan, so the agent cannot be installed
in a state the bot refuses to start in. That matters more here than in a
terminal: under launchd a refusal is a job restarting once a minute in a log
nobody is tailing.

**No mnemonic is written to the plist.** `~/Library/LaunchAgents` is
world-readable. The job runs with the repository as its working directory and
the bot loads `.env.<network>` itself, so keep that file at `chmod 600` and
the key never leaves it.

Three choices in the generated plist are deliberate and were wrong in the
hand-written one it replaces:

| | |
|---|---|
| `ProcessType: Standard` | Not `Background`. App Nap throttles Background jobs, and a throttled keeper loses races it would otherwise win. |
| `KeepAlive: {SuccessfulExit: false}` | The bot returns zero only when signalled, so a clean exit is a deliberate stop and stays stopped. A crash comes back after `ThrottleInterval`, which is 60s so a permanent misconfiguration fails slowly enough to read. |
| `ExitTimeOut: 30` | launchd SIGTERMs and waits, so the scan in flight finishes instead of dying between signing a group and submitting it. |

Installing also boots out `com.corvidlabs.arcron-keeper`, the label the
hand-written plist used, if it is still loaded. Two agents signing from one
key race each other and both pay group fees, and nothing else would say so.

After bootstrapping, the installer waits a few seconds and asks launchd
whether the job is actually running. `bootstrap` returning zero only means the
plist was accepted; it says nothing about whether the bot survived argument
parsing.

**It still sleeps and it still travels.** A laptop is the cheapest way to stop
depending on GitHub's scheduler and it is not an uptime record; option A or C
is. What it is good for is exactly what the registry needs today, which is a
keeper that is present most of the time rather than five times a day.

The log is JSON, one object per line, and the bot emits a `scan` line every
round -- **about 3 MB a day**. Nothing rotates it. Either point a shipper at
it or truncate it periodically:

```bash
: > ~/Library/Logs/arcron/keeper-testnet.log
```

`fledge run keeper-daemon` runs the same loop in the foreground, for a
terminal you are watching. `fledge run keeper-daemon-uninstall` stops the
agent and removes the plist, leaving the log.

## What the account needs

A keeper pays 3,000 microAlgos per execution and collects the upkeep's fee, so
it is profitable as long as fees exceed costs. It refuses to start when it
cannot afford one execution.

What it can afford is not what it holds. Every Algorand account has a minimum
balance it cannot spend, and that floor is not a constant: it rises by 100,000
microAlgos for every asset the account is opted in to, and again for every app
and every asset it has created. A keeper opted in to eleven bonus assets was
measured at a floor of 5,439,000 microAlgos. The bot reads the floor from the
node rather than assuming it, and `--min-balance` is measured in spendable
microAlgos, so an account can hold five ALGO and still be told, correctly, that
it is nearly empty.

Use an account that holds no more than it needs. It is a hot key on a machine
that is running unattended, and its whole job is to spend small amounts
constantly.

### If the keeper is a post-quantum account

It works, and it costs the same today. `docs/arcron.md` has the measurements:
a Falcon-signed `execute` is **4,384 bytes against ed25519's 340**, and
Algorand charges `max(min_fee, size x fee_per_byte)`. The per-byte rate is
zero, so both pay the flat 1,000 microAlgo minimum and a post-quantum keeper
spends the same 3,000 on an execution as any other.

That is a property of current network conditions, not of the design, and
`arcron.md` is explicit about what follows: a chain that ever prices bytes
would leave post-quantum keepers underpaid at the floor.

**Two things follow for you as an operator.**

The contract needs no change for it. An upkeep nobody can profitably serve
goes unserviced, its fee climbs toward the creator's ceiling, and either a
cheaper keeper takes it or the creator raises the price. The contract never
learns what kind of account is signing, which is the right place for that
knowledge to be absent.

**The bot would stop before it overpaid**, and the message would not explain
why. The keeper refuses to sign an outer fee above 10,000 microAlgos, because
verifying a genesis id proves which network a node speaks for and not that it
is honest. That guard cannot tell a lying node from a legitimately large
transaction. Under per-byte pricing a Falcon keeper would hit the ceiling and
refuse to broadcast rather than pay.

Failing that way round is correct. Raise the ceiling with
**`KEEPER_MAX_OUTER_FEE`**, in microAlgos, rather than removing the guard:

| `KEEPER_MAX_OUTER_FEE` | ceiling used |
|---|---|
| unset | 10,000 |
| `60000` | 60,000 |
| `nonsense` | 10,000 |
| `0` or negative | 10,000 |

A value that is not a positive integer falls back to the default rather than
being honoured. Neither a typo nor a zero should be a way to switch a guard
off: zero would make every fee look too high and stop the keeper dead, which
is a confusing way to express "no ceiling" and is not what anyone means by
it.

### Forwarding what it earns

A keeper earns into the same account it spends from. That is what makes it
self-sustaining, and it is also why the balance on a profitable registry
climbs indefinitely in a hot key on an unattended machine. `--sweep-to`
forwards the surplus somewhere you actually control.

```bash
poetry run python -m scripts.keeper_bot --network testnet \
    --sweep-to <your address> --sweep-above 5000000 --sweep-every 86400
```

Either trigger fires on its own: `--sweep-above` when the surplus reaches an
amount, `--sweep-every` when a period has elapsed and there is anything worth
sending. Naming a destination with neither is refused, because it would mean
a sweep that never happens.

**The period is wall time and it survives a restart.** That is worth stating
because it was twice not true. It first measured from the last sweep and
passed "unmeasurable" until one had happened, so `--sweep-every` alone could
never fire at all. The fix for that measured from process start using
`time.monotonic`, which is the wrong clock on exactly the machine option D
recommends: launchd restarts the keeper on every crash and every login, and
monotonic time does not advance while a Mac sleeps, so a daily period needed
a full day of awake, uninterrupted uptime. It now comes off the same state
file the bot keeps its backoff in, so sleeping and restarting cost nothing.
With `--no-state` there is no file and the period restarts with the process.

**The reserve is the part worth understanding.** `--sweep-reserve` is what
stays behind, and it is floored at `--min-balance` whatever you ask for. A
keeper swept below the point where it can pay for executions stops earning,
and the fee income that would have refilled it stops at the same moment, so
the account cannot recover on its own. The default reserve keeps the account
minimum plus about a hundred executions.

Sweeping to the keeper's own address is refused. It looks like it works,
because the transaction succeeds and the balance barely moves, and it burns a
transaction fee every period for as long as it runs.

`--sweep-dry-run` logs what would be sent and sends nothing, which is the
right way to see what a configuration does before trusting it with a key.

A sweep failure never stops execution. Trading the thing that earns for the
thing that tidies up would be the wrong way round, so a failed sweep is
logged and retried on the next heartbeat.

## Watching it

`scripts/notifier.py` posts to a Discord webhook when the registry changes or
a keeper falls behind. It holds no account and cannot sign, so it is safe to
run anywhere. `deploy/notifier.env.example` is its configuration.

```bash
poetry run python -m scripts.keeper_bot --check --network testnet --app-id <id>
```

exits non-zero if the registry has due upkeeps nobody is servicing, which is
the one-line health check to hang a monitor on.

## ASA bonuses, and whether to take them

An upkeep can offer a bonus in an Algorand Standard Asset on top of its ALGO
fee. The contract pays that bonus only to a keeper already opted in to the
asset. A keeper that is not opted in still executes the upkeep and still
collects the full ALGO fee; the bonus stays in escrow. Nothing fails, so
nothing is logged, and the only symptom of leaving bonuses behind is earnings
quietly lower than the board says they should be.

So the opt-in is a decision, and it is worth being exact about what it costs.
It is not the deposit. Opting in locks 100,000 microAlgos of minimum balance,
but a close-out releases it again, so the only part genuinely spent is about
2,000 microAlgos of transaction fees for the opt-in and the close-out.

What it costs is a flow. The bonus is a third inner transaction, so a keeper
that can receive one has to fund it, which is 1,000 microAlgos more per
execution. It cannot decline: Algorand pools fees and does not refund the
unused part, so skipping the surcharge and then receiving a bonus would mean
an underfunded group and a failed execution. There is no per-execution
opt-out, only opting out of the asset.

| | keeper pays | keeper receives | net ALGO |
|---|---|---|---|
| not opted in | 3,000 | the fee | fee less 3,000 |
| opted in | 4,000 | the fee and a bonus | fee less 4,000, plus the bonus |

The difference an opt-in makes is therefore **the bonus, less 1,000 microAlgos,
per execution**, and that holds whatever the fee is, however late the upkeep,
and wherever its escalation cap sits. An asset worth less than that is not a
missed opportunity if you decline it. It is a permanent tax on every upkeep
naming it, for as long as the opt-in stands.

```bash
poetry run python -m scripts.keeper_assets --network testnet --app-id <id> \
    --keeper-address <your keeper>
```

reports, for every fee asset in the registry, how many upkeeps name it, how
much it is accruing per day, what the surcharge on that costs, how many
bonuses are left in escrow, and the **break-even unit price**: what one unit of
the asset has to be worth for the opt-in to pay for itself. Compare that with a
price and the decision is made. `scripts/keeper_assets.py` deliberately does
not source a price itself, because a price feed is a backend, and a keeper does
not need one for anything else.

`--check` reports the same thing in one line per asset when given
`--keeper-address`, so a monitor already running the health check gets the
warning without a second command:

```bash
poetry run python -m scripts.keeper_bot --check --network testnet --app-id <id> \
    --keeper-address <your keeper>
```

Neither command signs anything, and neither opts in to anything. Opting in is
an operator decision, made once, with a plain `AssetTransfer` of zero units to
yourself. It is not something a bot should do at three in the morning on the
strength of an accrual figure.

Note the app account's opt-in is a different thing with different rules. That
one is permanent: `opt_in_asset` on the contract takes a deposit and there is
no way to release it, by design. Only the keeper's own opt-in can be closed out.

## Seeing a race afterwards

A race leaves almost no trace, and that is the point of it. Algorand rejects a
failing transaction at validation, so the losing keeper's execution never
reaches a block: there is nothing to query later, no receipt, no fee, no entry
in any explorer. Only the winner's transaction exists.

So the losing keeper's own log is the record, and it is written to carry
everything an outsider would need to check it. This one is real, from the
first race that ever happened on TestNet, at round 66703234:

```json
{"event": "race_lost", "round": 66703234, "upkeep_id": 75,
 "target_app": 769891902,
 "winner": "NUGVPQGZCURNU4CBHQ2IMXCY4UO2VI3VYCBWKCATL4OAKBJAT4MUTQMBVU",
 "won_at_round": 66703238, "fee_forgone": 4000, "spent": 0,
 "registry_advanced": true,
 "tx_id": "KXTAGVSRJAYXTUGRGA5VY73SLRRH2YGUKIB7YIFOEUBWM4P7XDXQ"}
```

Every field there can be checked against the chain by somebody who does not
trust the keeper that wrote it:

- `won_at_round` and `winner` come from the upkeep's own box and from the
  block that upkeep was serviced in. Read the block; the winner's execution is
  in it.
- `spent` is a balance read either side of the rejected call. It is `0`, and
  that number is the whole argument for running a keeper: **losing costs
  nothing**, which is not true on chains where a revert still burns gas.
- `tx_id` is the transaction that was thrown away. Look it up in an indexer
  and it is not there, because it never was. That absence is the claim.
- `registry_advanced` says whether the box had moved when the loser looked. It
  is often `false`, because the winner's transaction can still be in the pool
  a moment after it has already beaten you, and then `winner` and
  `won_at_round` are `null` rather than a guess. A keeper that reported the
  account which serviced the upkeep an hour earlier would be worse than one
  that said nothing.

To produce one on purpose rather than waiting for the schedule:

```bash
poetry run python -m scripts.keeper_race --network localnet
poetry run python -m scripts.keeper_race --network testnet \
    --app-id 769891898 --target-app 769891902
```

It registers a fast upkeep, starts two real keeper bots against the same
barrier, and then makes the checks above itself. It exits non-zero when the
two keepers did not actually collide, because a run in which they politely
took turns proves nothing and should not read as a pass.
