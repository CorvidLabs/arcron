# Where Arcron stands

Last updated 2026-09-02. This page is the one place to look if you want to
know what exists, what state it is in, and what happens next. If it disagrees
with anything else in this repo, this page is probably the stale one: check
[the release table](releases.md), which is generated against live app ids.

## The short version

The keeper network is live on TestNet, holds real escrow, and has been through
five rounds of adversarial review, four of them by independent language
models rather than by people. **None of that is an audit**, and `SECURITY.md`
says so in the words that matter: no third party has reviewed this contract. It is
**upgradeable**, which means bugs found now are fixable in place rather than
requiring everyone to cancel and re-register. Nothing but our own money has
ever been escrowed in it.

The honest summary is that correctness is in good shape and **usability is
untested**. Every interaction the system has ever had was with someone who
already knew how it worked.

## What is live

| | |
|---|---|
| Keeper | TestNet app [`769891898`](https://testnet.explorer.perawallet.app/application/769891898) |
| Demo target | Pulse [`769891902`](https://testnet.explorer.perawallet.app/application/769891902) |
| Programs | 2,219 bytes across two pages, sha256 `c94c6e0c…` (alpha-3) |
| Governance | **not frozen**: the creator can still replace the programs |
| Registry | 36 live boxes as of round 66,922,643 on 2026-09-02; `next_upkeep_id` 121. Last full creator attribution was 2026-09-01 (32 live, seven addresses, all ours). Stranger count still zero. |
| Pulse | `beats` 341, `last_note` `arcron`, `last_beat_round` 66,920,434 |
| Console | [corvidlabs.xyz/arcron/console/](https://corvidlabs.xyz/arcron/console/), live and current with this tree |
| Arcui | [corvidlabs.github.io/arcui](https://corvidlabs.github.io/arcui/?preset=pulse): generic ARC-56 workbench that can Sign & register the same Pulse `tick` path. Packing measured 2026-09-02 against `js/` and live boxes 19/22. |

`poetry run python -m scripts.verify_build --network testnet --app-id 769891898`
proves the deployed programs are this source, byte for byte. Anyone can run it.

Three earlier deployments are superseded and must not be used: `769823086`,
`769802474`, and `769772891`.

### Measured 2026-09-02

Read from TestNet algod at round 66,922,643. Nothing submitted.

- Keeper `769891898`: `frozen` 0, `next_upkeep_id` 121, 36 boxes named `"u"` plus a big-endian uint64. App account holds 54.097616 ALGO. Creator `E5M2OH5XNDMN…FJZQ3E`. Extra program pages: 1.
- Pulse `769891902`: `beats` 341, `last_note` `arcron`, `last_beat_round` 66,920,434.
- Live box ids: 19-22, 81, 82, 84-86, 89, 92-94, 98-120. **91 is gone.**
- Packing: Arcui `encodeCallArgs` / `boxMbr` / notes / box name match `@corvidlabs/arcron` JS and live boxes 19 and 22 byte for byte. Arcui also sets `appForeignApps: [target]`; the JS `register` helper does not. Extra, not wrong: both groups simulate-succeed. A dummy-account simulate overspends. A funded empty-sig simulate of Pulse `tick` register returned ABI `121`. Due execute of upkeeps 82 and 120 also succeeded on simulate.
- A signed register from a wallet has still not landed from Arcui. That last mile is a human Pera signature on the GitHub page.

Do not treat this page as a substitute for `fledge run health` or for regenerating [`testnet.md`](testnet.md).

## The dogfood

**Live since 2026-08-26.** It started as a `rain` draw serviced by Arcron, on a
contract that lived in this repository. The rain contract, its scripts, its bot
and its own page moved out on 2026-08-31, to
<https://github.com/CorvidLabs/arcron-rain>, served from
<https://corvidlabs.xyz/rain/>. What is recorded below is the half that was
always about the keeper: an upkeep, its escrow, and what happened to the
upkeeps before it.

Upkeep **113** services the live hub `770746178`: it calls `draw()uint64` every
1,286 rounds (about an hour), SKIP_AHEAD, paying 4,000 µALGO an execution. Its
target is maintained in another repository, and that is the ordinary case
rather than a loose end. The last full creator attribution, on 2026-09-01,
found seven addresses against targets this repository has never built. All
seven of those addresses are ours, so this is a statement about how the
registry is exercised and not about who uses it: the count of upkeeps
registered by somebody who is not us is zero. Re-read from the chain on
2026-09-02 the live set is 36 boxes and `next_upkeep_id` is 121; that
attribution was not re-run.

Upkeep **91** used to point at `770130162`, the hub rain ran on until
2026-08-31. That hub has no update path and predates the fix that stops a ONE
draw being aimed by tickets bought after the seed is public, so it could not
be repaired and rain redeployed rather than upgraded. `arcron-rain` does not
adopt the old id at all. **91 was cancelled on 2026-09-01**; the box is gone,
which [`testnet.md`](testnet.md) already records. The abandoned hub is
immutable and still holds money; the registry is no longer paying to poke it.
This was the second time: upkeep **79** was the same fault one hub earlier and
was cancelled on 2026-08-31.

Upkeep 79 had itself replaced upkeep 77, which was registered against a
selector its target does not have and so could never have executed: every
attempt died on `err` in the target's own ABI router, and a selector is fixed
in the box at registration. Cancelling refunded the escrow and box MBR in full
and cost 0.005 ALGO. The console now refuses to register a call its own Test
button has just said would fail, which is the hole that let it happen. That
story is kept here rather than moved with rain, because what it is about is
the registry: an upkeep can be registered against a method that does not
exist, and nothing on chain will ever tell you.

**Where to look when it breaks:**

- `poetry run python -m scripts.keeper_bot --check --network testnet --app-id 769891898`
  says whether upkeep 113 is stalled or starved, among everything else in the
  registry. Whether the target itself is healthy is a question for
  <https://github.com/CorvidLabs/arcron-rain>, which carries the hub's source,
  its spec, its own `verify_build` fork and the scan that reads its rain boxes.
  What it deliberately does not carry is any verification of `770130162`: that
  hub is the one it replaced, and it refuses to adopt it.
- `.github/workflows/keeper-bot.yml` still runs execution on a cron, a stopgap
  that names a long-running host as the real fix in its own header.

**Pulse is the heartbeat**, and always was the instrument the uptime clock
actually reads: [`769891902`](https://testnet.explorer.perawallet.app/application/769891902)
is a counter that cannot fail, so a count that stops incrementing is
unambiguous. Upkeeps 19 to 22 drive it under both catch-up policies.

The mechanism [`docs/design/1.0.md`](design/1.0.md) describes is a schedule
whose absence would actually be noticed, replacing a one-shot settlement that
proved nothing about sustained operation. One draw we ran ourselves was the
only evidence available when that was written. It is not the evidence any
more: the live registry is, and 1.0.md was rewritten on 2026-08-31 to gate on
that instead.

## The contracts

Four independent reviews plus three re-scores covered seven contracts in
August 2026. They were carried out by language models given the repository and
an adversarial brief, which is a useful thing and is not an audit; nobody has
paid a firm to look at this. and most of their findings landed in four of them: `treasury`
had two validation gaps, `deadman` had a total-loss trap on its default
deploy path, `embargo` let a stranger hijack a fresh instance, and `watchdog`
documented an error its own assert ordering made unreachable. None of that
was the product. Those four were cut from the repository on 2026-08-26 so
review attention concentrates on the contract that actually holds other
people's money; see the commit for the reasoning. "Ships" below means whether
it is ready to hold value belonging to someone other than us.

| Contract | What it is | Ships | Notes |
|---|---|---|---|
| `keeper` | the network itself: escrow, scheduling, keeper payment, governance | **yes** | five adversarial review rounds, none of them a paid audit; every finding indexed in [`reviews/findings.md`](reviews/findings.md), which is also where the open ones are |
| `subscription` | recurring payments, an example target | **yes** | reviewed clean; the integration example the docs recommend copying |
| `pulse` | trivial demo target | n/a | exists to be called; the heartbeat target for the dogfood's uptime clock |

`rain` was reviewed in the same rounds and is not in the table any more: it
left this repository on 2026-08-31 for
<https://github.com/CorvidLabs/arcron-rain>, taking its one blocker (a prize
asset created `default_frozen` could be received and never sent, found and
fixed) and its review record with it. The reviews themselves are unedited in
[`docs/reviews/`](reviews/), which is the point of keeping them.

The reviews also refuted one reported blocker. An extra program page is charged
to the creator account, not the app account; measured against both live
deployments the app account base is exactly 100,000 microalgo with a second
page in use.

## What we know, and what we do not

Proven, and not worth re-proving:

- A 20-stage end-to-end test passes on TestNet and finishes with the app
  account at exactly its minimum balance, so every escrowed microalgo was
  either paid to a keeper or refunded to its creator.
- A simulation ran 1,008 executions across 96 upkeeps with three keepers and
  came out exactly solvent. Read that as a busy registry rather than as
  competition: the three take turns, and until 2026-08-26 all three were
  signing as the same account, because `scripts/scenario.py` funded three and
  then let the bot take its signer from the environment.
- A losing keeper pays nothing. Algorand rejects failing transactions at
  validation rather than including them, so there is no fee to pay.
  Established twice over: by construction in `scripts/keeper_e2e.py` stage 14,
  and, since 2026-08-27, between two keepers that genuinely collided on
  TestNet.

  **The first real race**, staged by `scripts/keeper_race.py`: two keeper bots
  aligned to the same barrier both scanned round 66703234 and both found
  upkeeps 71 and 75 due. They split the registry, each winning one and losing
  the other, which is the first time anything but a queue has happened here.
  On upkeep 75 the winner's `SFOP56PA…` is in block 66703238; the loser's
  `KXTAGVSR…` is in no block and no indexer, and the loser's balance moved by
  zero. A second run at round 66703289 reproduced it the other way round, with
  `RWZRK7WB…` winning and `OMTXGJQT…` thrown away.

Genuinely unknown, and only answerable by other people:

1. ~~**Can someone get an upkeep running from the docs alone?**~~ **Tested
   2026-08-26, and the answer was no.** An agent given only `README.md`,
   `docs/arcron.md` and `docs/integrating.md`, with no access to this
   repository, did get an upkeep registered, executed by a stranger account and
   cancelled on LocalNet. It needed twelve guesses and had to disassemble the
   deployed approval program to recover the ARC-4 selectors, because no
   document contained them. `next_upkeep_id` was undocumented, which makes
   `register` a deadlock from raw algosdk. The box tail description was wrong in
   a way that returns a plausible incorrect value rather than raising. Those are
   fixed. What the exercise did not settle is whether the docs now work, since
   they were repaired by reading that agent's report: **rerun it against a fresh
   agent before treating this as answered.**
2. **Does it survive unattended time?** Still open. The earlier dogfood
   attempt was found dark on 2026-08-26 after roughly a day: the cron keeper
   was skipping for a missing secret and the local bot was pointed at a
   superseded app. The schedule described in [The dogfood](#the-dogfood)
   above went live the same day, and the clock now runs against the whole
   registry rather than against that one upkeep; nothing here claims 30
   unattended days yet, only that the mechanism answering the question exists
   and is running.
3. **Does keeping actually pay?** Nobody has run a keeper for a week and
   looked at whether it was worth the gas.

Those are what the alpha tasks below exist to answer.

## What happens next

The stages and what each one freezes are in [releases.md](releases.md). In
order:

1. **Close every finding that a MainNet create would make permanent**, and
   reach a consensus of about 90 to 95 percent confidence across independent
   reviewers. Not every open finding: an earlier version of this page said
   that, and it contradicted the line below, because one of the open findings
   is "the console has no MainNet entry" and closing that publishes the path
   to the app.
2. Run the registry unattended, with the notifier watching. This is the
   only evidence that accrues while nobody is looking, and it no longer
   depends on any one upkeep continuing to be interesting.
3. Answer the three unknowns above through the alpha tasks.
4. MainNet, deployed from `corvid.algo` and frozen promptly, with the app id unpublished
   until it is frozen.

### Why 90 to 95 and not 100

Because the deployment is upgradeable, and that is a deliberate trade rather
than a stage we have not finished yet.

A frozen contract has to be right the first time. Its only remedy for a bug is
telling every creator to cancel and re-register by hand, so the bar before
freezing is as close to certainty as a review process can get, which in
practice means a paid audit and months of unchanged bytecode.

An unfrozen one can be fixed in place. That does not make bugs acceptable; it
makes the cost of the last few percent of confidence wildly disproportionate
to what it buys, because the failure mode it protects against is one we have a
remedy for.

The honest counterpart, said plainly so nobody has to work it out: **that
allowance is ours and does not transfer.** The reason we can accept 90 rather
than 100 is that the creator can still reach every escrow, and while that is
true, anyone escrowing here is trusting a keyholder rather than bytecode.
Which is exactly why the app id stays unpublished until freeze, and why an
unexpected upkeep before then is a person to freeze for rather than a schedule
to finish.

The one rule held throughout: **do not freeze, and do not invite outside
escrow until it has run unattended for a while.** Not for a calendar, but
because "we can fix it" stops being a complete answer the moment someone
else's money is involved.

With one correction, which an outside review made and which matters:
**there is no such thing as not inviting people, on chain.** `register` is
permissionless, so anyone who learns the app id can escrow into it during
that window, and an explorer listing, a bot log, a README or a status post is
the invitation. The protection during the unattended period is not the
calendar and not our intent: it is that the MainNet app id is not published
anywhere. If an upkeep we did not create appears before freeze, that is a
real person who has trusted us, and the answer is to freeze then rather than
to wait out the remaining time.

## How to help

Three tasks, each about half an hour to an hour, each answering one of the
unknowns above:

- [#92](../../issues/92): register an upkeep using only the docs
- [#93](../../issues/93): run a keeper for an hour and say whether it was worth it
- [#94](../../issues/94): point Arcron at a contract you wrote

None of them ask you to break it. That has been done five times. What has
never happened is somebody simply **using** it.

Start at **https://corvidlabs.xyz/arcron/console/**. That address is also the
answer to "is this the real thing": the contract is permissionless, so anyone
can build a front end for Arcron, and where a console is served from is the
only thing that separates ours from a copy asking you to sign something.
Nothing else about a page proves anything, so check the address rather than
the page.

The generic workbench that packs the same `register` group is
[Arcui](https://corvidlabs.github.io/arcui/?preset=pulse). The console remains
the canonical register form. Arcui is the contract-agnostic path.

Everything is TestNet, so there is no real money anywhere in any of this.
Get test ALGO from <https://bank.testnet.algorand.network/>.
