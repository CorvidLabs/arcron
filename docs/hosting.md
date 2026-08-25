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

Half-hourly is chosen against the upkeeps that exist rather than in the
abstract. The shortest live cadence is about six hours, so a check every
thirty minutes is late by at most eight percent of one interval.

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

Good as a backstop next to a real keeper. Two keepers running from
different places is also a more honest demonstration of the network's premise
than one.

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
