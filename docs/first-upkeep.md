# Registering your first upkeep from the console

A walkthrough for doing, by hand and with a real wallet, the one thing nobody
has ever done: writing to the Arcron contract from the console.

Everything below reads or writes TestNet only. The worst outcome is losing a
fraction of a TestNet ALGO, and even that is refundable: cancelling an upkeep
returns its remaining escrow **and** its box minimum balance in full.

## Why this is worth your time

Every read path in the console has been driven against live TestNet. **No write
path has ever been exercised by a wallet.** `register`, `execute`, `cancel` and
`top_up` all need a signature, and no automated test can produce one.

Three of the five journeys in [`journeys.md`](journeys.md) rest entirely on
this, so until somebody does it, J2, J3 and J4 are built and unverified.

It is also the cheapest bug-finding available. The disabled-button contrast bug,
where every Register and Execute button in the console rendered at a 1.02:1
contrast ratio and was literally invisible, survived four independent agent
reviews, an axe-core pass reporting zero violations, and 91 unit tests. Someone
opening the page found it in about ninety seconds, because none of those checks
look at rendered pixels.

## Before you start

**Switch Pera to TestNet.** Settings, then Developer Settings, then Node
Settings, then TestNet. Skip this and Pera hands the console a MainNet address,
which has no TestNet balance, and the Register button stays disabled with no
visible explanation.

**Have about 0.2 TestNet ALGO.** The breakdown is below and the bulk of it comes
back when you cancel.

**Start the console:**

```bash
cd web && bun run ng serve
```

Then open <http://localhost:4200/register?network=testnet&app=769891898>.

If the page loads a title and nothing else, the dev server is serving the
workspace TypeScript untranspiled. That was fixed by `prebundle.exclude` on the
dev-server target in `web/angular.json`; if it comes back, that is where to
look.

## The numbers you will need

| | |
|---|---|
| Keeper app | `769891898` (alpha-3) |
| App account | `M4YFP33L5VIFRF53X53WUMQWBOWSLYQNBSSAJV2SORGF43L36XBY7OREUA` |
| Target app (pulse) | `769891902` |
| Method signature | `tick()uint64` |
| Selector it produces | `0x4d4d5f0b` |
| Box MBR | 0.0621 ALGO, **refunded in full on cancel** |
| Minimum fee per run | 0.004 ALGO |

`pulse` is a heartbeat counter that exists to be called. It has no state worth
protecting and cannot fail, which is what makes it the right first target.

## The steps

### 1. Test the call before connecting anything

Fill in **TARGET APP ID** `769891902` and **METHOD SIGNATURE** `tick()uint64`.
The selector `0x4d4d5f0b` should appear beneath the signature field as you type.

Press **Test the call**.

This needs no wallet and costs nothing. It simulates the inner call Arcron will
make, with the sender set to the keeper application's own account, which has no
private key for anyone to hold. Checking the thing before exposing a wallet to
the page is the right order, and the button is built so you can.

Expect a graded result, never a flat pass. For `pulse.tick()` it should read
`RESOURCES: NONE` and say the call reached for nothing a keeper would have to
name. It will also tell you what it *cannot* know, which is whether a keeper
will turn up and whether the call's needs will grow later.

**If it grades anything other than `NONE` or `servable`, stop and say so.** The
grades exist because a naive version of this button would return a flat pass on
a target needing more than six references, which is permanently unexecutable
once you have escrowed. That was measured, not assumed:
`scripts/spike_simulate_test_button.py`.

### 2. Fill in the rest

| Field | Value | Why |
|---|---|---|
| INTERVAL (ROUNDS) | `215` | About 10 minutes. Or press the `every 5 min` quick-cadence button. |
| FEE PER EXECUTION | `0.004` | The minimum. Keepers spend about 0.003 in group fees, so this leaves them 0.001. |
| FEE CEILING | `0` | Off. Only raise it if an upkeep is actually going unserviced. |
| FUNDING | `0.02` | Five runs. |
| IF A RUN IS MISSED | **Skip ahead** | See below. |

**Leave it on Skip ahead.** Catch up replays every missed interval at one fee
each, and the number of fees is bounded by how long it went unkept rather than
by the escrow. Upkeep 18 on this same deployment is the live demonstration: it
burned its entire escrow on 17 catch-up runs and advanced 41 rounds against a
23,478 round backlog, then starved. On a short cadence, catch-up after any real
outage cannot catch up. It is still the right choice for work where every period
genuinely counts; it is the wrong choice for a first upkeep.

### 3. Read the cost before you sign

Check the **UP-FRONT COST** tile. With the funding above it should read
**0.0851 ALGO**, itemised:

| | | |
|---|---|---|
| Box deposit | 0.0621 | returned in full when you cancel |
| Escrow | 0.0200 | spent one execution at a time, remainder returns on cancel |
| Network fees | 0.0030 | three transactions, gone either way, including if the group fails |

An earlier draft of this page said 0.0771, which is the total for 0.012 of
funding rather than the 0.02 its own table asks for. The console was right and
this document was wrong, which is worth recording: this section exists to catch
the console lying about cost, and the first thing it caught was itself.

The console's figure was also genuinely wrong until 2026-08-26, reading 0.0741
against a real 0.0771 debit. **Compare the tile against what Pera actually asks
you to approve.** If they disagree, that is a bug and worth more than the
upkeep.

### 4. Tick the attestation, connect, and register

Tick **I have tested this call against my own app and accept the risk.** It
records human judgement, and it is deliberately not satisfied by the Test button
having passed. Arcron cannot know whether calling this method on a schedule is
what you want.

Click **Pera** in the CONNECT row at the top and scan the QR with your phone.

The console should read your balance. Watch for this: an unread balance and a
zero balance are different states and the console is built to distinguish them.
If it says you have nothing when you have 14 ALGO, that is a bug in
`payer.service.ts`, which is new and has never seen a real wallet.

Then press **Register upkeep** and approve in Pera.

### 5. What should happen

You should land on `/u/<id>`, the upkeep's own page, not on a confirmation
panel. Registering ending somewhere you can link to is the point of that route.

That page should show what it calls, its cadence, its next run, its escrow, its
runway, and a plain sentence about what happens when the escrow runs out.

Within about ten minutes the half-hourly cron keeper should execute it and
`RUNS` should become 1.

## What to tell me afterwards

Whether it worked matters less than these:

1. **Did the cost tile match what Pera charged you**, to the microalgo.
2. **Did the console read your balance**, and did it distinguish unread from
   zero.
3. **Where did you land after signing**, and did the Activity log say anything
   useful.
4. **Anything that looked broken, ugly, small, or confusing.** The UI has known
   problems: text is small, the layout does not use the full screen, and it is
   not mobile responsive. A Playwright suite is being built to measure those,
   but it will only ever find what somebody thought to assert.
5. **Anything you had to guess.**

## Cleaning up

```
Cancel
```

on the upkeep's page. It refunds the remaining escrow plus the full 0.0621 box
MBR. Cancelling is creator-only, and the refund goes to the account that
registered.

Leaving it running is also fine and mildly useful: it is one more upkeep on the
uptime clock.

## If something goes wrong

- **Register stays disabled.** Both the attestation and a connected account are
  required. The hint beside the button says which is missing.
- **Pera shows a MainNet account.** It is still on MainNet. Switch the node
  setting and reconnect.
- **The page says "This is not the Arcron deployment".** The app id in the URL
  is not `769891898`. That panel is deliberate: anyone can deploy a contract
  with this ABI and box layout, so a look-alike shows the same registry and
  accepts the same register form. Every money button stays disabled until you
  explicitly continue, and the id is not remembered.
- **An execution fails.** Losing a race to another keeper costs nothing, and the
  chain rejects a failing transaction at validation rather than including it.
  That is ordinary and not an error.

## It has been done once, on 2026-08-26

Upkeep **72** on app `769891898`, registered from the console by a wallet
connected through Pera, creator
`3NQY7ZHZO6TDNGQODM4MTLGEJSQ3DBO7ZGJUXFXRUDN7H4J6FH2ODTUVT4`. Target
`769891902`, `tick()uint64`, SKIP_AHEAD, 215 rounds, five runs funded.

The first write to the Arcron contract from the console in the project's
history. What it settled:

**The cost shown is the cost charged.** The tile quoted 0.0851 ALGO and said the
account would have 10.0103 spendable afterwards. The wallet was asked to approve
five payments summing to exactly 0.0851, and the account's spendable balance on
chain afterwards was 10.010300. Both halves correct to the microalgo. This
figure was wrong the same morning, quoting 0.0741 against a real 0.0771 debit,
so it is not a formality.

**The group is the shape the documentation claims.** Five charges: 0.0621 box
MBR, 0.02 escrow, and three transaction fees of 0.001. That is
`[mbr_payment, funding_payment, app call]`, each carrying its own fee. The three
preconditions of `register` added to `arcron.md` the same day, that both
payments go to the keeper application's own account, that group order, and the
box reference for `b"u" + itob(next_upkeep_id)`, are now observed rather than
asserted.

**The balance read works against a real wallet.** `payer.service.ts` had never
seen one. It read the spendable balance correctly and computed the remainder
correctly.

What this does not settle: `execute`, `cancel` and `top_up` from the console
remain unexercised by a wallet.
