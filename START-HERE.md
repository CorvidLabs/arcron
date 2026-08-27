# Start here

**Arcron is a permissionless keeper network for Algorand.** A smart contract
cannot wake itself up, so you register a scheduled call with escrowed ALGO, and
anyone at all can execute it for the fee. No allowlist, no stake, no token, no
owner.

It is **live on TestNet**, app
[`769891898`](https://testnet.explorer.perawallet.app/application/769891898),
and the console is at
[`corvidlabs.xyz/arcron/console/`](https://corvidlabs.xyz/arcron/console/).

**It is alpha, unaudited, and TestNet only.** Nobody outside CorvidLabs has
registered an upkeep yet. You would be the first, which is the point of this
page.

---

## Pick the thing you came to do

| I want to… | Go to | Costs |
|---|---|---|
| **Try it in ten minutes** | [`docs/first-upkeep.md`](docs/first-upkeep.md) | ~0.2 TestNet ALGO, mostly refundable |
| **Point it at my own contract** | [`docs/integrating.md`](docs/integrating.md) | an afternoon |
| **Run a keeper and get paid** | [`docs/hosting.md`](docs/hosting.md) | a machine, or a free GitHub Action |
| **Break it** | [below](#if-you-came-to-break-it) | your time, and we want the findings |
| **Judge whether the idea is any good** | [`docs/why.md`](docs/why.md) | ten minutes of reading |
| **See what is actually running** | [`docs/testnet.md`](docs/testnet.md) | every contract and upkeep on TestNet |
| **Read the whole thing, front to back** | [the Working Guide](docs/book/arcron-working-guide.md) | ~16,000 words |
| **Tell us the idea is wrong** | [the open question](https://github.com/CorvidLabs/arcron/discussions/163) | we would rather hear it now |

This page branches by what you came to do and then hands you to a document. The
[Working Guide](docs/book/arcron-working-guide.md) is the other shape: one
ordered read through all of it. It is **compiled from `docs/`, and `docs/`
wins** — if the two ever disagree, the guide has a bug and
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

Arcron is the shared version of that. One hourly schedule costs about **$0.28 a
month**, against roughly $2 for the cheapest server you would host a bot on —
and the bot still has to be written.

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

## If you came to break it

Good. Please do. Two things will make your time worth more than the last
reviewer's.

**First, know what is already known.** These are disclosed, measured and not
findings:

- The contract is **upgradeable until frozen**, and `frozen` is 0 today. The
  creator can replace the programs. See
  [`docs/security.md`](docs/security.md) — this is the biggest one and we would
  rather you attack something else.
- **Every keeper running is ours.** "Permissionless" is true architecturally and
  currently false empirically.
- **The console's text is small and its controls are under the WCAG touch
  target.** 53 specific measurements are recorded in
  [`web/e2e/baseline.json`](web/e2e/baseline.json) with the reason each stands.
- **`CATCH_UP` can burn an upkeep's whole escrow** after an outage. Measured:
  upkeep 18 spent everything on 17 replays and advanced 41 rounds against a
  23,478 round backlog.
- **Rounds are not a clock.** An "hourly" upkeep drifts about 36 hours against
  the calendar over a month.
- Every attack a previous review found is in
  [`scripts/attacks.py`](scripts/attacks.py), each asserted to be refused by its
  own guard. Run it: `poetry run python -m scripts.attacks --network localnet`.

**Second, here is what would actually be new.** Ranked by how much we would care:

1. **A path that loses money** — an upkeep that pays a keeper for work not done,
   a refund that returns more than was escrowed, a fee that exceeds its cap, a
   keeper that can be made to pay for someone else's execution.
2. **A way to make an upkeep permanently unexecutable** after its creator has
   escrowed, without the creator's consent.
3. **A griefing path that costs the attacker less than the victim.**
4. **Anything in the console that makes a stranger sign a transaction they did
   not intend** — a wrong cost, a wrong target, a look-alike deployment getting
   through [`quarantine.ts`](web/src/app/core/quarantine.ts).
5. **A claim in the docs that is false.** The last three reviews each found real
   ones, including in the cost argument. Numbers are checkable; check them.

**Where to send it:** open an issue, or reply to
[attacks and findings](https://github.com/CorvidLabs/arcron/discussions/162),
which lists the same known-already set so nobody duplicates work. If it is a
live-funds vulnerability, [`SECURITY.md`](SECURITY.md) has the private path —
please use that rather than a public thread.

---

## If you are an agent

You have everything you need in this repository. Some orientation so you spend
your budget on the interesting parts:

- **The contract** is `smart_contracts/keeper/contract.py`, about 600 lines of
  Algorand Python. Its ABI is in `specs/keeper/`.
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

- [#92](https://github.com/CorvidLabs/arcron/issues/92) — register an upkeep
  using **only** the docs. Tell us every place you had to guess.
- [#93](https://github.com/CorvidLabs/arcron/issues/93) — run a keeper for an
  hour and say whether it was worth it.
- [#94](https://github.com/CorvidLabs/arcron/issues/94) — point Arcron at a
  contract you wrote yourself.

The most valuable thing you can report is not a bug. It is **the moment you had
to guess**, because that is the thing no test in this repository can find.

If you looked and decided *not* to use it, that is worth more still:
[what would make you actually use this?](https://github.com/CorvidLabs/arcron/discussions/164)

---

## The state of it, honestly

| | |
|---|---|
| Contract | Live on TestNet, unaudited, upgradeable, 1.0 surface complete |
| Deployment age | Days, not months |
| Upkeeps registered by strangers | **Zero** |
| Keepers not run by us | **Zero** |
| MainNet | Not deployed. Gated on sustained TestNet time and a 2-of-3 multisig |
| Review history | Every round, including the ones that said no, is in `docs/reviews/` |

If that reads as underselling, it is deliberate. The failure mode this project
is most likely to hit is not a bug — it is being a well-built thing that nobody
needed. We would rather find that out from you than discover it in a year.
