# Arcron keeper dashboard

A local page for somebody **running** a keeper. Not the console.

```bash
fledge run keeper-ui      # http://localhost:4300
```

## What it is for

The console at `corvidlabs.xyz/arcron/console/` answers "should I register an
upkeep". This answers a different question: **is my keeper working, and is it
worth running.**

It shows the registry as a keeper sees it, which is what
`@corvidlabs/arcron/board` already models: what is due, what each upkeep pays
*net of the roughly 3,000 microAlgos an execution costs*, which fees have
escalated because nobody serviced them, and how many runs each has left.

## What it deliberately does not do

**It never sees a key.** There is no wallet, and the package has no wallet
dependency so one cannot be added by accident. The bot already holds the key;
a dashboard that could hold one too would be a second place to lose it.

**It does not talk to the bot.** It would be easy to have the keeper process
serve its own status, and the answers would be better: real backoff state, why
a call was skipped, whether the process is actually alive. That means opening a
listening socket on the one component in this system that holds a hot key and
runs unattended, which is a poor trade for a nicer dashboard. So this reads a
public node and infers what it can.

The honest consequence is in `keeper-view.ts`: this can only report what a
keeper **did**, never whether it is up. A bot running fine on an idle registry
produces exactly the same silence as a bot that was killed, so silence with
nothing due is reported as `quiet` and explicitly *not* as a fault. Only
silence while work is genuinely due becomes `stopped`.

**It is never published.** `tests/test_keeper_ui_stays_local.py` enforces that,
because "we did not mean to publish it" is not a control. The console's address
is a security property: the contract is permissionless, so that address is the
only thing separating our front end from a look-alike. A second Arcron-branded
page whose purpose is to be pointed at arbitrary app ids would undo it.

## Block time

It measures block time from the chain rather than assuming a constant. The
repository carries three different numbers for this (2.8 in the scripts, 2.66
in `docs/why.md`, about 2.70 when actually measured), and a dashboard that
picked one would quietly disagree with whichever page you read last.
