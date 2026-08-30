# Review brief for outside agents

Copy everything in the block below and give it to a reviewing agent. It is
self-contained: it names the repository, the deployment, the build commands and
what is already known, so an agent starting cold does not spend its budget
working those out.

**It is deliberately read-only.** The agent is told not to modify anything, not
to open pull requests and not to write to a chain. Its whole output is an
assessment plus a prompt you can hand to an agent that *does* have write access,
after you have read it.

## Why it is worded the way it is

Three choices in here were expensive to learn.

**"Find out whether it holds up, with permission to conclude that it does."**
Not "prove this is broken". Both briefs have been run against this project. The
first kind found the disabled-button contrast bug, a fee floor priced below the
cost of supplying it, and three false figures in our own cost argument. The
second kind produces confident nonsense, because an agent told to find fault
will find it whether or not it is there.

**A list of what is already known.** Three separate reviews independently
rediscovered that the contract is upgradeable, which is disclosed in the README,
in `docs/security.md` and on every page of the console. That is budget spent on
something nobody was hiding. The list is at the top so it is read first.

**"Recompute anything you intend to rely on, including figures this project
states about itself."** In one week `docs/why.md` claimed a 19x cost advantage
that was really about 7.7x, asserted that ten scheduled contracts means ten
bots when `scripts/keeper_bot.py` in this repository services the whole registry
from one process, and stated a GitHub auto-disable rule that applies only to
public repositories. Every one was caught by an agent that recomputed rather
than quoted.

---

````
You are reviewing a public open-source project. **Read-only: do not modify
anything, do not open pull requests, do not push, do not run anything that
writes to a chain.** Your entire output is a written assessment.

## The project

Arcron is a permissionless keeper network for Algorand.
https://github.com/CorvidLabs/arcron

A smart contract cannot wake itself up. Arcron lets anyone register a scheduled
contract call with escrowed ALGO, which any keeper can then execute for the fee.
No allowlist, no stake, no token, no owner. Live on TestNet, app 769891898.

Start at START-HERE.md. It branches by intent and names the app id, the docs
map, and how to build and test locally.

## Your job

**Find out whether it holds up, with permission to conclude that it does.**

That framing is deliberate. Do not go looking for confirmation that it is bad.
A brief of the form "prove this is broken" produces confident nonsense; the
reviews of this project that found real bugs were the ones free to say a thing
was fine, and they said so about most of it.

## What is already known: these are NOT findings

Spending your budget rediscovering these wastes it. All are disclosed and
measured in the repo:

- The contract is **upgradeable until frozen**, and `frozen` is 0 today.
- **Every keeper running is the project's own.** "Permissionless" is true
  architecturally and currently false empirically.
- The console's text is small and its controls are under the WCAG touch target.
  53 specific measurements sit in `web/e2e/baseline.json` with reasons.
- **CATCH_UP can burn an upkeep's whole escrow** after an outage. Measured:
  17 replays bought 41 rounds against a 23,478-round backlog.
- **Rounds are not a clock.** A nominally hourly upkeep drifts ~12 hours against
  the calendar per month, and it accumulates.
- Every attack a prior review found is already asserted in `scripts/attacks.py`.

## What would actually be new, ranked

1. **A path that loses money.** An upkeep paying for work not done, a refund
   exceeding what was escrowed, a fee above its cap, a keeper made to pay for
   someone else's execution.
2. **A way to make an upkeep permanently unexecutable** after its creator has
   escrowed, without their consent.
3. **A griefing path costing the attacker less than the victim.**
4. **Anything in the console that makes a stranger sign what they did not
   intend.** Wrong cost, wrong target, a look-alike deployment getting past
   `quarantine.ts`.
5. **A false claim in the documentation.** Several numbers in these docs were
   wrong last week and were caught by review. **Recompute anything you intend to
   rely on, including figures this project states about itself.**
6. **Whether the core idea is even right.** docs/why.md argues Algorand is
   missing a "call this later" primitive, and docs/prior-art.md documents two
   prior attempts that failed. Is the reasoning sound? Is the comparison to
   alternatives (own bot, AWS Lambda, GitHub Actions cron) honest?

## Notes that will save you time

- Build and test: `fledge lanes run ci`. On a real chain:
  `algokit localnet start && fledge lanes run local`.
- The Python tests mock inner app calls and do not enforce minimum balances, so
  anything proven in `tests/` should be re-proven in `scripts/keeper_e2e.py`
  before you believe it.
- The console is Angular 22 in `web/`. `bun test` for units;
  `fledge run web-render` is the only check that asks a browser for
  measurements.

## Output: two parts, both required

**Part 1: your assessment.** For each finding: what it is, where (file and
line), why it matters, and how confident you are. Separate what you verified
from what you suspect. Say plainly which parts you checked and found sound.
That is as useful as a defect, and rarer.

**Part 2: a prompt for Claude Code.** End with a single copy-pasteable block
instructing an agent with write access on exactly what to change and why.
Ordered by severity, specific about files, and honest about anything you could
not verify. If you found nothing worth changing, say that instead of inventing
work.
````

---

## Where findings go

[Attacks and findings](https://github.com/CorvidLabs/arcron/discussions/162) for
anything you broke or any claim that turned out false.
[The open question](https://github.com/CorvidLabs/arcron/discussions/163) if the
disagreement is with the idea rather than the code.

**A live-funds vulnerability belongs in [`SECURITY.md`](../SECURITY.md)'s private
path, not a public thread.**

## Keeping this honest

The known-already list above goes stale as things get fixed. A brief that still
warns about a bug somebody repaired last month sends reviewers away from the
part that changed, which is the part most likely to be broken. When a listed
item stops being true, delete it. That is the same rule
[`web/e2e/baseline.json`](../web/e2e/baseline.json) enforces on itself, where a
licence nothing uses any more fails the run.
