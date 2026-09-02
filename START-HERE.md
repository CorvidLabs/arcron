# Start here

**Arcron is a permissionless keeper network for Algorand.** A smart contract
cannot wake itself up, so you register a scheduled call with escrowed ALGO, and
anyone at all can execute it for the fee. No allowlist, no stake, no token.
There is still a creator, and until it calls `freeze` it can replace the
programs — see below.

It is **live on TestNet**, app
[`769891898`](https://testnet.explorer.perawallet.app/application/769891898),
and the console is at
[`corvidlabs.xyz/arcron/console/`](https://corvidlabs.xyz/arcron/console/).

**It is alpha, unaudited, and TestNet only.** No third party has reviewed the
contract. It is also still **upgradeable**: `frozen` is `0`, so its creator can
replace the programs while your escrow sits in it. That is deliberate at this
stage — a bug can be fixed without asking everybody to cancel and re-register —
and it is a real power over your money. Escrow only what you can afford to
lose, and check the state yourself before you scale up:

```bash
poetry run python -m scripts.govern status --network testnet --app-id 769891898
```

---

## Pick the thing you came to do

| I want to… | Go to | Costs |
|---|---|---|
| **Try it in ten minutes** | [`docs/first-upkeep.md`](docs/first-upkeep.md) | ~0.2 TestNet ALGO, mostly refundable |
| **Sign a Pulse register from a generic workbench** | [Arcui](https://corvidlabs.github.io/arcui/?preset=pulse) | ~0.165 TestNet ALGO, Pera on TestNet |
| **Point it at my own contract** | [`docs/integrating.md`](docs/integrating.md) | an afternoon |
| **See something actually built on it** | [CorvidLabs/arcron-rain](https://github.com/CorvidLabs/arcron-rain) | a read |
| **Run a keeper and get paid** | [`docs/hosting.md`](docs/hosting.md) | a machine, or a free GitHub Action |
| **Break it** | [below](#if-you-came-to-break-it) | your time, and we want the findings |
| **Judge whether the idea is any good** | [`docs/why.md`](docs/why.md) | ten minutes of reading |
| **See what is actually running** | [`docs/testnet.md`](docs/testnet.md) | the deployment and the registry, read from the chain |
| **Read the whole thing, front to back** | [the Working Guide](docs/book/arcron-working-guide.md) | ~16,000 words |
| **Tell us the idea is wrong** | [the open question](https://github.com/CorvidLabs/arcron/discussions/163) | we would rather hear it now |

This page branches by what you came to do and then hands you to a document. The
[Working Guide](docs/book/arcron-working-guide.md) is the other shape: one
ordered read through all of it. It is **compiled from `docs/`, and `docs/`
wins**. If the two ever disagree, the guide has a bug and
[`tests/test_book.py`](tests/test_book.py) should have caught it.

---

## The idea in one minute

Every serious chain should let you say *"call this later"* without running a
server. Algorand has no way to do that: **there is no ARC for scheduled
execution and never has been**, not even a submitted draft. We checked every PR
and issue in the Foundation's ARCs repository.

So the ecosystem hand-rolls it. The Foundation's own staking contracts carry
`// Note: ANYONE can call this` with no reward attached. The documented answer
to recurring work is still "run your own watcher on a cron."

Arcron is the shared version of that. One hourly schedule costs about **$0.27 a
month**, against roughly $2 for the cheapest server you would host a bot on.
And the bot still has to be written.

**We are not claiming to be first.** Somebody proposed the same economics on
Algorand in January 2024 and was funded 50,000 ALGO for it; it never shipped. A
keeper network did ship here in 2024 and ran three tasks in two years, all its
author's own. [`docs/prior-art.md`](docs/prior-art.md) is the full history,
including what they did differently and why we think it mattered.

**And here is the test we have staked it on**, written down so you can hold us
to it:

> If this is real infrastructure, somebody outside CorvidLabs registers an
> upkeep for something they actually wanted scheduled, within a few months of
> this being visible. If a year passes and every upkeep is still ours, the
> design was fine and the demand was not there.

---

## What is proven, and what is not

Two things in this repository have very different amounts of evidence behind
them, and it matters which one you are looking at.

**The keeper registry has been used, mostly by us.** Read from the chain on
2026-09-02, at round 66,922,643: **36 live boxes**, `next_upkeep_id` 121,
`frozen` 0. Pulse `beats` 341, `last_note` `arcron`. The last full creator
attribution is still 2026-09-01, at round 66,901,001: **32 live upkeeps** from
**seven distinct addresses**, of which our deployer registered 6 and six other
addresses registered the other **26**. That second number flatters us: five of
those six addresses share a single funding account and one of the five is an
agent [`docs/testnet.md`](docs/testnet.md) records as ours, so read them as
one operator wearing five costumes — 20 of the 26. **1,069 executions** were
recorded across the boxes on that day, and **two addresses executed in the
preceding day**:
`NUGVPQGZ…` 231 and `GCQL3M7A…` 57. Both are ours, the second being the GitHub
Actions cron keeper; all time it is the largest keeper this registry has had,
689 executions of 1,399. `CEPY52VZRWFL…` is
ours too, and it was the hardest to place: funded once by the public TestNet
dispenser and by nothing else, it deployed its own targets, registered six
upkeeps and runs its own keeper, taking no top-up from the account behind the
other five. It looked like a stranger for exactly one day, and was recorded
here as unattributed on that basis. It is an agent that funded itself the way
any developer would.

**So the count of upkeeps registered by somebody who is not us is zero**, and
every keeper that has ever executed here is one we started. `scripts/verify_build.py` proves the
deployed programs are this source, byte for byte. `fledge run health` prints
the per-upkeep and per-keeper lines but has no creator column, so the
attribution above came from decoding the boxes and querying the indexer.

**What that still does not prove:** nobody has escrowed anything but test ALGO,
nobody has audited the contract, the creator can still replace it, and every
person who has used it already knew how it worked. Usability is the least
tested thing here.

**`subscription` has no evidence at all, and its tests hide that.**
[`smart_contracts/subscription/contract.py`](smart_contracts/subscription/contract.py)
is the worked pull-payment example the integration docs point at. It has never
been deployed on TestNet or MainNet, and it has no `deploy_config.py`, so
nothing here can put it on a public network. Its unit tests run against mocks
that **record inner transactions without executing them and do not enforce
minimum balances** — precisely the two failures a contract that pays people out
is most likely to have. It does get one real-chain run, on LocalNet, via
`fledge run smoke-subscription`, which CI's LocalNet job does not include.
Copy the pattern; do not treat it as proven code.

**Rain is the honest answer to "what is this for".**
[CorvidLabs/arcron-rain](https://github.com/CorvidLabs/arcron-rain) is a hub of
scheduled prize draws that runs on this registry — upkeep 113, every ~1,286
rounds — and it lived in this repository until 2026-08-31.
[`docs/design/split.md`](docs/design/split.md) records why it left and what
this repository lost when it did.

---

## If you came to break it

Good. Please do. Two things will make your time worth more than the last
reviewer's.

**First, know what is already known.** These are disclosed, measured and not
findings:

- The contract is **upgradeable until frozen**, and `frozen` is 0 today. The
  creator can replace the programs. See
  [`docs/security.md`](docs/security.md). This is the biggest one and we would
  rather you attack something else.
- **The console's text is small, some of its controls are under the WCAG touch
  target, and its registry rows stop being a table on narrow screens.** 59
  specific measurements are recorded in
  [`web/e2e/baseline.json`](web/e2e/baseline.json) with the reason each stands:
  45 text-size, 12 table-cell (cell widths inside rows that render as cards),
  2 touch-target.
- **`CATCH_UP` can burn an upkeep's whole escrow** after an outage. Measured:
  upkeep 18 spent everything on 17 replays and advanced 41 rounds against a
  23,478 round backlog. 19 and 116 still use `CATCH_UP`; the rest of the live
  set has been `SKIP_AHEAD` wherever last decoded. The 2026-09-01 snapshot
  said 30 of 32. Do not invent a new 34 of 36 without decoding 117-120.
- **Rounds are not a clock.** TestNet measured 2.695 s a round on 2026-08-28,
  so an "hourly" upkeep fires about 2.2 minutes early every hour — roughly
  **27 hours a month** against the calendar. The ~12 hours quoted elsewhere is
  the MainNet round of 2.752 s, and Arcron has never run on MainNet.
- **Upkeep 91 was cancelled on 2026-09-01.** It had been paying keepers to
  drive hub `770130162`, the rain hub that is immutable and predates the fix
  stopping a ONE draw being aimed by tickets bought after the seed is public.
  The box is gone; [`docs/testnet.md`](docs/testnet.md) records the cancel.
  The hub itself is still live and still holds money; the registry is no
  longer paying to poke it. That is arcron issue
  [#232](https://github.com/CorvidLabs/arcron/issues/232) and
  [`docs/status.md`](docs/status.md).
- **Twelve upkeeps in the registry are starved and will stay starved.** 98 to
  109 run every 20 rounds; thirty days of that costs 192 ALGO each, so
  `fledge run topup` refuses to fund them and says to cancel instead. A cadence
  is fixed in the box at registration, so there is no other remedy.
- Every attack a previous review found is in
  [`scripts/attacks.py`](scripts/attacks.py), each asserted to be refused by its
  own guard. Run it: `poetry run python -m scripts.attacks --network localnet`.

**Second, here is what would actually be new.** Ranked by how much we would care:

1. **A path that loses money.** An upkeep that pays a keeper for work not done,
   a refund that returns more than was escrowed, a fee that exceeds its cap, a
   keeper that can be made to pay for someone else's execution.
2. **A way to make an upkeep permanently unexecutable** after its creator has
   escrowed, without the creator's consent.
3. **A griefing path that costs the attacker less than the victim.**
4. **Anything in the console that makes a stranger sign a transaction they did
   not intend.** A wrong cost, a wrong target, a look-alike deployment getting
   through [`quarantine.ts`](web/src/app/core/quarantine.ts).
5. **A claim in the docs that is false.** The last three reviews each found real
   ones, including in the cost argument. Numbers are checkable; check them.

**Where to send it:** open an issue, or reply to
[attacks and findings](https://github.com/CorvidLabs/arcron/discussions/162),
which lists the same known-already set so nobody duplicates work. If it is a
live-funds vulnerability, [`SECURITY.md`](SECURITY.md) has the private path.
Please use that rather than a public thread.

---

## If you are an agent

You have everything you need in this repository. Some orientation so you spend
your budget on the interesting parts:

- **The contract** is `smart_contracts/keeper/contract.py`, about 560 lines of
  Algorand Python. Its ABI is in `specs/keeper/`.
- **The other four contracts are not the product.** `pulse` is the demo target,
  `subscription` is a teaching example, and `resource_probe` and `sim_probe`
  are instruments that measure the keeper's own `execute()` boundary. If you
  are looking for something that holds value, there is only one.
- **Build and test:** `fledge lanes run ci`. On a real chain:
  `algokit localnet start && fledge lanes run local`.
- **The tests mock inner calls.** `algorand-python-testing` records app calls
  rather than executing them and does not enforce minimum balances, so anything
  you prove in `tests/` should be proven again in `scripts/keeper_e2e.py` on
  LocalNet before you believe it.
- **The console** is Angular 22 in `web/`, standalone components and signals.
  `bun test` for units, `fledge run web-render` for the rendered-page audit,
  which is the only thing here that asks a browser for measurements.
- **Do not trust this page's numbers.** Several of them were wrong last week and
  were corrected by a review exactly like the one you are about to do. Recompute
  anything you intend to rely on.

**A brief that produces useful output:** *"Find out whether X holds"*, with
permission to conclude that it does. Briefs of the form *"prove this is bad"*
produce confident nonsense; we have run both and only the first kind found real
bugs.

[`docs/review-brief.md`](docs/review-brief.md) is that brief, written out and
ready to paste. If somebody sent you here to review this, use it.

---

## If you are on the team

Three tasks, each one a thing nobody has done:

- [#92](https://github.com/CorvidLabs/arcron/issues/92): register an upkeep
  using **only** the docs. Tell us every place you had to guess.
- [#93](https://github.com/CorvidLabs/arcron/issues/93): run a keeper for an
  hour and say whether it was worth it. `fledge run keeper-preview` reads what
  the registry has actually been paying, and what your share of it would be,
  before you spend the hour.
- [#94](https://github.com/CorvidLabs/arcron/issues/94): point Arcron at a
  contract you wrote yourself.

The most valuable thing you can report is not a bug. It is **the moment you had
to guess**, because that is the thing no test in this repository can find.

If you looked and decided *not* to use it, that is worth more still:
[what would make you actually use this?](https://github.com/CorvidLabs/arcron/discussions/164)

---

## The state of it, honestly

Read from the chain on 2026-09-02 at round 66,922,643. Recompute rather than
quote: `fledge run health` is the live version of the runway and solvency
columns. It does not attribute creators or keepers, and on the public TestNet
endpoint its box reads can be rate-limited into an HTTP 403.

| | |
|---|---|
| Contract | Live on TestNet, **unaudited**, **unfrozen** (the creator can replace the programs), 1.0 surface complete |
| Deployment age | The app id since 2026-08-25; the programs it runs now since 2026-08-26. Days, not months |
| Registry | **36 live boxes** as of 2026-09-02, round 66,922,643; `next_upkeep_id` 121. Last full attribution 2026-09-01: 32 live from 7 addresses, all ours. Pulse `beats` 341 |
| Upkeeps registered by strangers | **Zero.** The 2026-09-01 decode found six addresses that are not our deployer, and all six are ours: five share a funding account, and the sixth, `CEPY52VZRWFL`, funded itself from the public dispenser and looked like a stranger for a day before it was attributed |
| Keepers | **Two addresses executed in the last day of that 2026-09-01 snapshot**, and both are ours: `NUGVPQGZ…` 231, `GCQL3M7A…` 57. All time, fifteen addresses have executed, eleven of them the e2e suite, and `GCQL3M7A…` is the largest: 689 of 1,399. Nobody outside has run one |
| Built on it | One thing: [CorvidLabs/arcron-rain](https://github.com/CorvidLabs/arcron-rain), which we also wrote. [Arcui](https://corvidlabs.github.io/arcui/) packs `register` from a browser against any ARC-56 |
| MainNet | Not deployed. Gated on sustained TestNet time; the creator will be `corvid.algo` |
| Review history | Every round, including the ones that said no, is in `docs/reviews/`. None of them is a paid audit |

If that reads as underselling, it is deliberate. The failure mode this project
is most likely to hit is not a bug. It is being a well-built thing that nobody
needed. We would rather find that out from you than discover it in a year.
