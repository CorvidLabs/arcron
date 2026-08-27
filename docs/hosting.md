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
| GitHub Actions cron | free to ~$115/mo, by cadence | best-effort | repo secrets | uncomment a line |
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
- **Scheduled workflows are disabled after 60 days without repository
  activity.** A quiet month turns the keeper off silently.
- **The mnemonic sits in repository secrets**, readable by any workflow in the
  repository. On TestNet, with a throwaway account holding a little ALGO, that
  is a small exposure. On MainNet it is not one to accept casually.
- **Each run is a fresh process**, so `scripts/keeper_backoff.py` has no memory
  between runs and a persistently failing upkeep is retried every time.

Good as a backstop next to a real keeper.

### B2. The second keeper, and why it is not just a backup

`.github/workflows/keeper-bot-2.yml` is a second keeper on the *same* cron as
the first, signing from `KEEPER_2_MNEMONIC`. It exists to make the thing this
project asserts actually happen.

Arcron's economic argument is that competition between keepers holds the fee
below the ceiling, and that competing is safe because losing a race costs
nothing. Neither had ever occurred on a real chain. One keeper serviced
TestNet, it won everything by default, and a keeper that never loses a race is
no evidence at all about what losing costs.

**An offset schedule does not race.** This is the part that is easy to get
wrong. Two keepers thirty minutes apart never contend for anything: the first
takes every due upkeep, the second arrives to an empty registry. That looks
like redundancy and is a queue. Even the same cron is not enough on its own,
because two runners finish installing dependencies tens of seconds apart, and
the slower one finds the work already done.

So both workflows pass `--align 120`, which holds the first scan until the
next whole two-minute mark in UTC. Runner clocks are NTP-synced, so an
absolute instant is the one thing two machines that have never met can agree
on. Both then scan in the same round window and reach for the same upkeep,
which is what a race is. Nothing about the barrier is specific to GitHub or to
this repository: a keeper on a VPS can join it with the same flag.

The two workflows keep **separate concurrency groups**. Sharing one would
queue the second behind the first, which is exactly the arrangement being
avoided.

#### What the owner runs

The secret is a credential, so it is the owner's to create. Three commands,
none of which write the mnemonic anywhere:

```bash
# 1. A new account. Nothing is saved; copy the mnemonic straight into step 3.
poetry run python - <<'PY'
from algosdk import account, mnemonic
private_key, address = account.generate_account()
print(f"address:  {address}")
print(f"mnemonic: {mnemonic.from_private_key(private_key)}")
PY

# 2. Fund it on TestNet. A couple of ALGO is plenty; fees top it up.
#    Or paste the address into https://bank.testnet.algorand.network/
algokit dispenser fund --receiver <address> --amount 2000000

# 3. Hand the mnemonic to the workflow, and to nothing else.
gh secret set KEEPER_2_MNEMONIC --repo CorvidLabs/nest
```

It must be a **different** account from `KEEPER_MNEMONIC`. One account cannot
race itself, and two jobs signing as the same address would collide on the
transaction id rather than on the upkeep. Until the secret exists the second
workflow skips itself with a notice, the same way the first one does, so
turning the schedule on early produces a green run and an explanation rather
than a failure every half hour that everyone learns to ignore.

#### What it costs

Two keepers is two runs, and `--align 120` adds about a minute of waiting to
each. Against the table above:

| | Runs/month | Minutes | Cost beyond the 3,000-minute allowance |
|---|---|---|---|
| one keeper, no barrier | 1,440 | ~2,900 | free |
| two keepers, `--align 120` | 2,880 | ~8,600 | ~$45/month |

That is the price of the demonstration, and it is worth checking against what
the demonstration is for. Once a race has been observed and recorded, the
second keeper can drop to a slower cron, or move to a host that is already
running: the barrier works between a workflow and a VPS just as well.

### C. A small always-on host

`deploy/Dockerfile` and `deploy/compose.yaml` are ready. Fly.io, Railway,
Render or a $4 Hetzner box all work. Worth it only if you would rather not put
this on a server that does something else.

### D. A laptop

`deploy/com.corvidlabs.arcron-keeper.plist` for launchd. Free, and fine for
development. It sleeps, it travels, and a 30-day uptime record will notice.

## What the account needs

A keeper pays 3,000 microAlgos per execution and collects the upkeep's fee, so
it is profitable as long as fees exceed costs. It refuses to start below
103,000 microAlgos: 100,000 to keep the account, plus one execution.

Use an account that holds no more than it needs. It is a hot key on a machine
that is running unattended, and its whole job is to spend small amounts
constantly.

## Watching it

`scripts/notifier.py` posts to a Discord webhook when the registry changes or
a keeper falls behind. It holds no account and cannot sign, so it is safe to
run anywhere. `deploy/notifier.env.example` is its configuration.

```bash
poetry run python -m scripts.keeper_bot --check --network testnet --app-id <id>
```

exits non-zero if the registry has due upkeeps nobody is servicing, which is
the one-line health check to hang a monitor on.

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
