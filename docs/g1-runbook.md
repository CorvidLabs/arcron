# G1: the keeper and the notifier on a server

G1 is the first gate of the MainNet rollout ([`design/mainnet-rollout.md`](design/mainnet-rollout.md),
"The goal"): a server runs the keeper and the notifier against TestNet **from
`main`**, not from a laptop and not from a feature branch; the notifier posts
to Discord and has done so for **seven days**; the node in front of them is our
own or has a fallback; and `health` shows executions without a 403 storm behind
them.

This page is the step-by-step for whoever does the server work. Every command
is copy-paste except the placeholders in angle brackets. Secrets appear here as
variable names only: they are typed on the server, into files the installer
creates, and never go in this repository, a pull request, a chat or a ticket.
The general reference is [`hosting.md`](hosting.md), section A.

## 0. Before you start

These are inputs and decisions, not server steps. Each has an owner.

| What | Owner | Where it ends up |
|---|---|---|
| A Discord webhook for a channel somebody reads every day | Leif | `DISCORD_WEBHOOK_URL` in `/etc/arcron/notifier.env` |
| The list of creator addresses that count as ours on TestNet (step 4) | Leif | `ARCRON_OURS` in `/etc/arcron/notifier.env` |
| Which node sits in front of the services. **Decided 2026-09-29 by Leif: the public endpoint plus a fallback** (see "The node" below) | Leif | `ALGOD_SERVER`, `ALGOD_SERVER_FALLBACK` in both env files (step 4), and the notifier's poll (step 4b) |
| Whether the laptop keeper and the GitHub cron keep running during the seven days. **Decided 2026-09-30 by Leif: both stop** | Leif | stopped at the go/no-go check (5b), restarted after step 8 (see "Other keepers" below) |
| Whether the starved TestNet upkeeps are cancelled or topped up first | Leif | see "The first scan" below |
| A new keeper account, made on the server (step 3) and funded with 2 TestNet ALGO | the operator | `KEEPER_MNEMONIC` in `/etc/arcron/keeper.env` |

The keeper account is a **hot key made for this server**. Never the creator of
any keeper app, never an account used anywhere else, never reused on MainNet.
The bot refuses to start as the creator of an unfrozen app, or as `corvid.algo`,
whatever variable holds the key (`scripts/keeper_bot.py`, `refuse_rewriting_signer`).

### The node

G1 needs "our own node or a fallback". **Decided 2026-09-29 by Leif: the public
endpoint plus a fallback.** Step 4 sets both, and step 4b slows the notifier
down, both as part of G1 rather than as options.

What was checked on 2026-09-29: besides the default
`https://testnet-api.algonode.cloud`, the host
`https://testnet-api.4160.nodely.dev` answers as `testnet-v1.0` on algod 5.0.2
and serves a real paged box listing (a `round` and a `next-token` on the
response), which every reader here requires; so does
`https://testnet-api.algonode.network`. They are run by the same operator as
the default, so whether they share its request quota is **unknown**.
`scripts/node_retry.py` alternates to the fallback when the primary refuses a
request; if both shed load together, the 403 count in step 8 will show it,
and a TestNet node of our own is the next step then. That would be a change
to `deploy/vps/algod.compose.yaml`, which runs a MainNet node and is not
written for TestNet.

What the two services ask of a node, counted the way `scripts/node_retry.py`
counts the keeper: the keeper about 4,800 requests a day, measured. The
notifier reads the box listing and then every box on each scan, about 38
requests for today's 36 upkeeps, every 30 seconds by default: about 110,000 a
day, before the block reads it makes to name a keeper. The public endpoint
refused the old keeper 4,949 times in one log at about 211,000 a day, and its
real quota is unmeasured. So step 4b sets the notifier's poll to two minutes,
about 27,000 a day. TestNet does not need a stranger seen within 30 seconds;
the unit's own 30-second default is left alone for MainNet, where it does.

### Other keepers

Today the TestNet registry is also serviced by a keeper on Leif's laptop (a
launchd agent, `xyz.corvidlabs.arcron.keeper.testnet`) and by the GitHub cron
in `.github/workflows/keeper-bot.yml`. **Decided 2026-09-30 by Leif: both stop
for the seven days**, so this server's keeper alone produces the evidence.
Both are stopped at the very end of the go/no-go check (5b), right before
step 6, so the registry is never left without a keeper while the server is
still being set up; and both come back after step 8. While they are stopped,
nothing else services the registry: if the server's keeper stops, upkeeps go
unserviced and the notifier says so, which is exactly what G1 is meant to
show.

Leif runs these, from his arcron checkout on the laptop.

Stop the laptop keeper. `keeper-daemon-uninstall` removes the agent's plist as
well as stopping it; stopping it with `launchctl bootout` alone would leave the
plist in `~/Library/LaunchAgents`, and launchd would start it again at the next
login, in the middle of the seven days. The copy is a fallback for the
restart; the plist carries no secret.

```bash
cp ~/Library/LaunchAgents/xyz.corvidlabs.arcron.keeper.testnet.plist ~/xyz.corvidlabs.arcron.keeper.testnet.plist.before-g1 \
  && test -s ~/xyz.corvidlabs.arcron.keeper.testnet.plist.before-g1 \
  && fledge run keeper-daemon-uninstall
fledge run keeper-daemon-status
```

The `&&` chain means the uninstall only runs once the backup exists, because
the uninstall deletes the plist. The status must report the plist as `absent`
and launchd as `not loaded`. launchd can take up to 30 seconds to let the job
go (the plist gives it that long to finish a scan), so if the status still
shows it loaded, run `fledge run keeper-daemon-status` again until it says
`not loaded`.

Stop the GitHub cron. Let a run already in progress finish first (it takes a
couple of minutes); disabling does not cancel it.

```bash
gh run list -R CorvidLabs/arcron --workflow keeper-bot.yml -L 1 --json status --jq '.[0].status'
gh workflow disable keeper-bot.yml -R CorvidLabs/arcron
gh workflow list --all -R CorvidLabs/arcron --json name,state --jq '.[] | select(.name=="Keeper bot") | .state'
```

The last line must print `disabled_manually`. Disabling stops new runs, not
one already going, so run the first command again: it must print `completed`
(not `in_progress` or `queued`) before step 6. The `--json` forms print one
plain word; the ordinary table shows status symbols in a terminal.

Restarting both, after the seven days, is in "After the seven days" below.

### The first scan

The notifier announces conditions when they begin. On its first scan every
upkeep that is already out of funds, under a week of runway, or funded but
overdue by more than three of its intervals is announced once. On 2026-09-25 that was 16 upkeeps out of funds and several more running
low, so expect a burst of messages on the first scan and quiet after it.
Cancelling or topping up those upkeeps first (`scripts/reclaim.py`,
`scripts/keeper_topup.py`, run by whoever holds each creator key) avoids the
burst; it is not required for G1.

## 1. Build the package, from `main`

On a workstation with this repository and Poetry installed:

```bash
git fetch origin
git switch --detach origin/main
git status --porcelain                  # must print nothing
./deploy/vps/package.sh                 # writes /tmp/arcron-keeper.tar.gz
tar -xzOf /tmp/arcron-keeper.tar.gz BUILD
```

`BUILD` must show `dirty=0` and `on_origin_main=yes`, and its `commit` is the
one to record. The script refuses a tree with uncommitted changes.

## 2. Install

The server needs Python 3.12 or 3.13 (`python3 --version`; 3.14 is refused
because a dependency has no wheels for it) and `apt-get`. Then:

```bash
scp /tmp/arcron-keeper.tar.gz <user>@<host>:/tmp/
ssh <user>@<host>
sudo mkdir -p /tmp/arcron-install
sudo tar -xzf /tmp/arcron-keeper.tar.gz -C /tmp/arcron-install
sudo bash /tmp/arcron-install/deploy/vps/install.sh
```

It creates a `keeper` system user, installs the code to `/opt/arcron` with its
own virtualenv, writes `/etc/arcron/keeper.env` and `/etc/arcron/notifier.env`
from the examples (mode `640`, owner `root:keeper`), installs the `keeper-bot`
and `arcron-notifier` units, and prints the build it installed. It neither
starts nor enables either unit yet, because the key is empty, so a reboot
before step 6 starts nothing.

## 3. Make the keeper's key, on the server

This makes a new account, writes its mnemonic straight into
`/etc/arcron/keeper.env`, and prints only the address. The mnemonic is never
shown, and it refuses to replace a key that is already there.

```bash
sudo sh -c 'cd /opt/arcron && .venv/bin/python - <<"PY"
import pathlib, re
from algosdk import account, mnemonic
path = pathlib.Path("/etc/arcron/keeper.env")
text = path.read_text()
if re.search(r"^KEEPER_MNEMONIC=\S", text, re.M):
    raise SystemExit("KEEPER_MNEMONIC is already set; not replacing it")
key, address = account.generate_account()
line = "KEEPER_MNEMONIC=" + mnemonic.from_private_key(key)
path.write_text(re.sub(r"^KEEPER_MNEMONIC=.*$", lambda _: line, text, count=1, flags=re.M))
print(address)
PY'
```

Fund the printed address with 2 TestNet ALGO from
<https://bank.testnet.algorand.network/>. An execution costs the keeper about
3,000 µALGO in fees and pays it back with the upkeep's fee, so 2 ALGO lasts a
long time; the bot logs its balance when it starts.

## 4. Fill in the env files

Edit with `sudo -e /etc/arcron/keeper.env` and `sudo -e /etc/arcron/notifier.env`.
One value per line and **no comment on the same line as a value**: systemd keeps
a trailing `# ...` as part of the value.

Both files set these values, each on its own line, alongside everything else
already in them (`keeper.env` keeps the `KEEPER_MNEMONIC` step 3 wrote). In the examples the
two TestNet fallback lines are commented out and empty; replace them with the
values below rather than uncommenting them, because an empty
`ALGOD_SERVER_FALLBACK` means no fallback at all. The fallback in the MainNet
block further down is a MainNet host and stays commented.

```ini
ARCRON_NETWORK=testnet
KEEPER_APP_ID=769891898
ALGOD_SERVER=https://testnet-api.algonode.cloud
ALGOD_PORT=
ALGOD_TOKEN=
ALGOD_SERVER_FALLBACK=https://testnet-api.4160.nodely.dev
ALGOD_TOKEN_FALLBACK=
```

Leave the MainNet blocks commented.

`notifier.env` also takes `DISCORD_WEBHOOK_URL` and `ARCRON_OURS`: every
creator address that counts as ours, comma separated, 58-character addresses
only (a name like `corvid.algo` is refused). To see who has upkeeps on the
registry, run this read-only line from a workstation checkout (the
`ALGOD_SERVER` on it is what `scripts/network.py` needs when the checkout has
no `.env.testnet`):

```bash
ALGOD_SERVER=https://testnet-api.algonode.cloud poetry run python -c "from scripts import network as net; from scripts.keeper_bot import scan_upkeeps; from collections import Counter; a = net.connect('testnet').client.algod; print(*(f'{n:3} upkeep(s)  {c}' for c, n in Counter(u.creator for u in scan_upkeeps(a, 769891898)).most_common()), sep='\n')"
```

Leif confirms which of those are ours. On 2026-09-29 the registry had seven
creators; the last full attribution, on 2026-09-01, found all seven were ours
([`status.md`](status.md)). Check again rather than trusting that.

### 4b. Slow the notifier to a two-minute poll

Part of G1 as decided (see "The node"): about 27,000 requests a day instead of
about 110,000. This adds a systemd drop-in beside the unit step 2 installed;
the installer replaces only the unit file itself, so the drop-in survives
upgrades.

```bash
sudo mkdir -p /etc/systemd/system/arcron-notifier.service.d
sudo tee /etc/systemd/system/arcron-notifier.service.d/poll.conf >/dev/null <<'EOF'
[Service]
ExecStart=
ExecStart=/opt/arcron/.venv/bin/python -m scripts.notifier --poll-seconds 120 --summary-every 60
EOF
sudo systemctl daemon-reload
systemctl cat arcron-notifier | grep -- '--poll-seconds 120'
```

The last line must print the new `ExecStart`. The empty `ExecStart=` line is
required: it clears the unit's own before the drop-in sets a new one.
`--summary-every 60` keeps the summary two-hourly at that poll. If G1 moves to
a node of our own, `sudo rm /etc/systemd/system/arcron-notifier.service.d/poll.conf`
and a `daemon-reload` put the unit's 30-second default back.

## 5. Preflight from the server, before starting anything

Read-only, and the one check an install cannot make: whether the node the
units will use serves a paged box listing at all. Run it as `keeper`, with the
units' own virtualenv:

```bash
sudo -u keeper sh -c 'cd /opt/arcron \
  && set -a && . /etc/arcron/notifier.env \
  && INDEXER_SERVER="https://${ARCRON_NETWORK}-idx.algonode.cloud" \
  && set +a \
  && .venv/bin/python -m scripts.preflight \
       --network "$ARCRON_NETWORK" --app-id "$KEEPER_APP_ID" --ours "$ARCRON_OURS"'
```

`node`, `app`, `boxes`, `build` and `solvency` must pass. `strangers` must say
`0 would be announced`; if it names upkeeps, `ARCRON_OURS` is short. The
`rehearsal` row is about a different job (see the end of this page) and does
not block G1. Save the output: it is part of the G1 record.

## 5b. Go / no-go

Every line must be a yes before step 6. The last two are the only ones that
change anything outside this server, which is why they come last.

| Check | How |
|---|---|
| The package is from `main` | `cat /opt/arcron/BUILD` shows `dirty=0` and `on_origin_main=yes` |
| The keeper account is funded | the address from step 3 holds at least 1 TestNet ALGO (any TestNet explorer; the bot also logs its balance when it starts) |
| Both env files are filled in | step 4, including the fallback lines; `notifier.env` has `DISCORD_WEBHOOK_URL` set and the `ARCRON_OURS` list Leif confirmed |
| The notifier's poll is two minutes | `systemctl cat arcron-notifier \| grep -- '--poll-seconds 120'` prints a line (step 4b) |
| The preflight passed | step 5: `node`, `app`, `boxes`, `build`, `solvency` pass and `strangers` says `0 would be announced` |
| Leif has answered the rest of step 0 | the starved-upkeeps decision is made (cleaned up first, or the first-scan burst accepted) |
| The laptop keeper is stopped | Leif runs the first block in "Other keepers"; `fledge run keeper-daemon-status` reports the plist `absent` and launchd `not loaded` |
| The GitHub cron is stopped | Leif runs the second block in "Other keepers"; `gh workflow list --all -R CorvidLabs/arcron --json name,state --jq '.[] \| select(.name=="Keeper bot") \| .state'` prints `disabled_manually`, and `gh run list -R CorvidLabs/arcron --workflow keeper-bot.yml -L 1 --json status --jq '.[0].status'` prints `completed` |

Then start the server's services straight away (step 6), so the registry is
without a keeper for minutes, not hours.

## 6. Start

```bash
sudo systemctl enable --now keeper-bot
sudo systemctl enable --now arcron-notifier
sudo systemctl status keeper-bot arcron-notifier --no-pager
sudo journalctl -u keeper-bot -f          # Ctrl-C to stop following
sudo journalctl -u arcron-notifier -f
```

The bot logs its keeper address, its balance and each scan. The notifier's
first line must say `posting to Discord`. If it says `printing here`,
`DISCORD_WEBHOOK_URL` is empty: on TestNet the notifier starts anyway and
announces into its own log, so it looks healthy while the seven days never
begin. The seven days start at the first summary in the channel. The notifier
posts the first-scan announcements to Discord, then one message per execution, and a
`📊 **Registry**:` summary every 60 scans, two-hourly at the two-minute poll
from step 4b. Its first line also says `every 120s`; if it says `every 30s`,
the drop-in is not in place. That summary is the liveness signal: a day
without one means the watcher is down.

## 7. Check it is doing the work

From a workstation checkout, after the first due upkeep has come round:

```bash
fledge run health
```

The keeper list at the bottom should include this server's keeper address with
executions against it. `fledge run keeper-preview` shows the same split with
what each keeper earned. On the server itself, where there is no `fledge`,
the same report is:

```bash
sudo -u keeper sh -c 'cd /opt/arcron \
  && set -a && . /etc/arcron/notifier.env \
  && INDEXER_SERVER="https://${ARCRON_NETWORK}-idx.algonode.cloud" \
  && set +a \
  && .venv/bin/python -m scripts.registry_health --network "$ARCRON_NETWORK" --app-id "$KEEPER_APP_ID"'
```

A node that refuses a request partway through stops that report with an
error; run it again, and count it in step 8.

## 8. The seven days

Every day, somebody looks at the Discord channel: summaries arriving, no
stranger alert, executions announced. The G1 record at the end of the week is:

| Evidence | How |
|---|---|
| The commit the services ran | `cat /opt/arcron/BUILD` |
| Preflight from the server | the output saved in step 5, and one more run at the end |
| Seven days of Discord posts | the channel history, first and last summary |
| Executions by this server's keeper | `fledge run health` and `fledge run keeper-preview` from a workstation |
| No 403 storm | `sudo journalctl -u keeper-bot --since "7 days ago" \| grep -c "The node refused"` and the same for `arcron-notifier` |
| No crash loop | `systemctl show keeper-bot arcron-notifier -p NRestarts` |
| The notifier ran at the decided poll | `systemctl cat arcron-notifier \| grep -- '--poll-seconds 120'` |
| Only this server's keeper executed | `fledge run health` once a day, keeping its keeper lines: each run lists the keepers of about the last day (32,000 rounds) from one indexer page of up to 1,000 transactions, so seven daily readings cover the week where one reading at the end would not. From the second day on it should list one keeper, this server's; the laptop keeper drops out about a day after 5b |

Those numbers go into the F11 evidence section of
[`design/mainnet-rollout.md`](design/mainnet-rollout.md) with the dates.

## After the seven days

Once the G1 record is written into
[`design/mainnet-rollout.md`](design/mainnet-rollout.md), Leif restarts the
two keepers stopped in 5b, from his arcron checkout on the laptop. The server's
keeper and notifier keep running; G2 needs them.

The laptop keeper first. Put the plist saved in 5b back and load it: that is
the same job as before, with its sweep settings, which live only in that
plist.

```bash
cp ~/xyz.corvidlabs.arcron.keeper.testnet.plist.before-g1 ~/Library/LaunchAgents/xyz.corvidlabs.arcron.keeper.testnet.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/xyz.corvidlabs.arcron.keeper.testnet.plist
fledge run keeper-daemon-status
```

The status must say `state = running`. Only then the GitHub cron:

```bash
gh workflow enable keeper-bot.yml -R CorvidLabs/arcron
gh workflow list --all -R CorvidLabs/arcron --json name,state --jq '.[] | select(.name=="Keeper bot") | .state'
```

which must say `active`. If the saved plist is lost, reinstall the agent with
the sweep flags the laptop section of [`hosting.md`](hosting.md) uses; a bare
`fledge run keeper-daemon-install` refuses, because the bot will not take a
sweep destination without a trigger:

```bash
fledge run keeper-daemon-install -- --sweep-to <your wallet> --sweep-above 2000000 --sweep-every 86400
```

If G1 is abandoned before the seven days are up, the same commands restart
them.

## Upgrading

Build a new package from the new `main` (step 1), copy it over and run the
installer again (step 2). It stops the keeper and the notifier before replacing
the code, starts the keeper again, restarts the notifier if it was running,
leaves both env files and the step 4b drop-in alone, and prints the new build.
Check `BUILD` afterwards.

## Stopping

```bash
sudo systemctl stop keeper-bot arcron-notifier
sudo systemctl disable keeper-bot arcron-notifier
```

The keeper's key stays in `/etc/arcron/keeper.env`. To retire the server,
forward what the keeper earned (`scripts/keeper_sweep.py`, or `KEEPER_SWEEP_TO`
in the env file) before deleting that file.

## What not to do

- Do not put the creator key, or any key that has created a keeper app, in
  either env file.
- Do not uncomment the MainNet blocks, set `ARCRON_ALLOW_MAINNET`, or start
  `algod.compose.yaml` as part of G1. MainNet is G2 and has its own ceremony.
- Do not paste the webhook, the mnemonic or the env files into a chat, a
  ticket, a pull request or this repository.

## What this does and does not depend on

G1 does not depend on the TestNet ceremony rehearsal (`rehearse.sh`, #250 F10),
which Leif runs separately with the rehearsal throwaway's key. The rehearsal
creates its own throwaway keeper app; the services here watch and service
`769891898` only, so they do not see it. G2 needs both G1 and the rehearsal.
The `rehearsal` row in step 5 reports whether that throwaway still holds
enough ALGO for the rehearsal, which is why it does not block G1. The
rehearsal's key never goes on this server, and the rehearsal's scratch branch
never goes in a package: step 1 packages `main`. Until a fallback or a node of
our own is set, the rehearsal and these services use the same public TestNet
endpoint, so run the rehearsal from a workstation rather than at a moment the
server is already being refused.
