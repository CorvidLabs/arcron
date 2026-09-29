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
| Which node sits in front of the services: the public endpoint plus a fallback, or a TestNet node of our own (see "The node" below) | Leif | `ALGOD_SERVER`, `ALGOD_SERVER_FALLBACK` in both env files |
| Whether the laptop keeper and the GitHub cron keep running during the seven days | Leif | see "Other keepers" below |
| Whether the starved TestNet upkeeps are cancelled or topped up first | Leif | see "The first scan" below |
| A new keeper account, made on the server (step 3) and funded with 2 TestNet ALGO | the operator | `KEEPER_MNEMONIC` in `/etc/arcron/keeper.env` |

The keeper account is a **hot key made for this server**. Never the creator of
any keeper app, never an account used anywhere else, never reused on MainNet.
The bot refuses to start as the creator of an unfrozen app, or as `corvid.algo`,
whatever variable holds the key (`scripts/keeper_bot.py`, `refuse_rewriting_signer`).

### The node

G1 needs "our own node or a fallback". What was checked on 2026-09-29: besides
the default `https://testnet-api.algonode.cloud`, the hosts
`https://testnet-api.4160.nodely.dev` and `https://testnet-api.algonode.network`
answer as `testnet-v1.0` on algod 5.0.2, and the first serves a real paged box
listing (a `round` and a `next-token` on the response), which every reader
here requires. They are run by the same operator as the default, so whether
they share its request quota is **unknown**. Two options:

- **Public plus fallback** (no extra software): set
  `ALGOD_SERVER_FALLBACK=https://testnet-api.4160.nodely.dev` and leave
  `ALGOD_TOKEN_FALLBACK` empty in both env files. `scripts/node_retry.py`
  alternates to it when the primary refuses a request. If both shed load
  together, the 403 count in step 8 will show it.
- **A TestNet node of our own.** `deploy/vps/algod.compose.yaml` runs a MainNet
  node and is not written for TestNet; running one for G1 is a change to that
  file, and a decision, not part of these steps.

What the two services ask of a node, counted the way `scripts/node_retry.py`
counts the keeper: the keeper about 4,800 requests a day, measured. The
notifier reads the box listing and then every box on each scan, about 38
requests for today's 36 upkeeps, every 30 seconds by default: about 110,000 a
day, before the block reads it makes to name a keeper. The public endpoint
refused the old keeper 4,949 times in one log at about 211,000 a day, and its
real quota is unmeasured. So on the public endpoint the notifier is the part
most likely to draw a 403 storm. If Leif chooses public plus fallback, step 4b slows the notifier down to keep
it well under that.

### Other keepers

Today the TestNet registry is also serviced by a keeper on a laptop and by the
GitHub cron in `.github/workflows/keeper-bot.yml`. Keepers racing is harmless
(a losing transaction is rejected and costs nothing), but the seven days are
meant to show this server doing the work. If the others keep running, the
evidence in step 8 has to be read per keeper, which `health` already does.

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

Both files: `ARCRON_NETWORK=testnet`, `KEEPER_APP_ID=769891898`, and the node
settings chosen in step 0 (`ALGOD_SERVER`, and `ALGOD_SERVER_FALLBACK` with
`ALGOD_TOKEN_FALLBACK` empty for a public fallback). Leave the MainNet blocks
commented.

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

### 4b. Only for public plus fallback: slow the notifier

Skip this with a node of our own. With the public endpoint, a two-minute
poll is about 27,000 requests a day, and TestNet does not need a stranger
seen within 30 seconds. The unit is installed by now (step 2), so:

```bash
sudo systemctl edit arcron-notifier
```

and in the editor that opens, between the comment lines it shows:

```ini
[Service]
ExecStart=
ExecStart=/opt/arcron/.venv/bin/python -m scripts.notifier --poll-seconds 120 --summary-every 60
```

`--summary-every 60` keeps the summary two-hourly at that poll. The empty
`ExecStart=` line is required: it clears the unit's own before the override
sets a new one.

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
`📊 **Registry**:` summary every 240 scans (about two hours at the default
30-second poll). That summary is the liveness signal: a day without one means
the watcher is down.

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

Those numbers go into the F11 evidence section of
[`design/mainnet-rollout.md`](design/mainnet-rollout.md) with the dates.

## Upgrading

Build a new package from the new `main` (step 1), copy it over and run the
installer again (step 2). It stops the keeper and the notifier before replacing
the code, starts the keeper again, restarts the notifier if it was running,
leaves both env files alone, and prints the new build. Check `BUILD` afterwards.

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
