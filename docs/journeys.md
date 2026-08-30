# What the console has to let people do

> **Working document, 2026-08-26.** This records how the console's requirements
> were argued out, and its "True today" sections describe the console as it was
> when each was written rather than as it is now. Several are already wrong: the
> console defaults to TestNet, it has a published URL, and the demo-target
> criterion in J2 was cut. **The Decisions sections are authoritative**, and
> `docs/console-plan.md` supersedes this file where the two disagree. It is kept
> because the reasoning is worth more than a tidy summary would be.

Five journeys. A journey is done when somebody who has not seen Arcron before
completes it without asking us anything, not when the code that implements it
exists.

They are written as criteria rather than as descriptions so that "done" is
something we can disagree about in advance rather than after. Each one records
what is true today, because the gap is the work and it is better stated than
implied.

The console is the primary way people use Arcron. Not a demo, not an operator
dashboard. That decision is what makes these journeys the bar rather than a
wish list.

## The shape, before the journeys

Taken from NFDomains, which is the product the maintainer named as the model.
An earlier version of this section described it as "one browsable surface where
mine is a filter". That was wrong, and looking at the actual product corrected
it.

**Persistent chrome, separate destinations.** A left sidebar carries the
sections, a global search sits top centre and works from anywhere, the wallet
sits top right, and below the sections the sidebar carries documentation and
community links.

| NFD | Arcron |
|---|---|
| Home | Dashboard: what this is, action cards, recent executions |
| Mint | Register an upkeep |
| Marketplace | The registry: search, filters, everything on the network |
| Analytics | Network health: live round rate, keeper lateness, executions |
| Manage | Your upkeeps |

Browsing everyone's upkeeps and managing your own are different places that
look and behave alike, rather than one page a filter reshapes. A keeper and a
creator share the chrome and the search; what differs is where they spend
their time.

Filter chips live inside the registry, as NFD's marketplace does it: a search
box, dropdowns carrying a count, active filters shown as removable chips.

Two details worth copying on purpose. NFD's Mint page is one input and one
button, against our nine fields on one screen, which argues for progressive
disclosure. And it shows "wallet not connected" as a quiet inline warning
rather than blocking anything, so the whole product is readable before you
commit to it.

Today the console has two tabs, no search of any kind, no sidebar, no page for
an individual upkeep, no concept of "mine", a register form pinned below both
tabs, and no router at all.

---

## J1: A stranger arrives and believes it

**Who** Somebody who followed a link or typed the address, knowing nothing.

**Done when**

- They reach a live registry without connecting a wallet, choosing a network, or entering an app id.
- Within the first screen they can tell: that upkeeps are being executed, roughly how often, and that the money involved is real.
- Nothing on screen asks them for anything before they have understood what they are looking at.
- If they arrived by a link naming an app that is not the published one, they are told so before anything else, in words that say what the risk is.
- They can reach a block explorer for the app, the app account, and any execution, so nothing rests on trusting this page.

**True today**

`DEFAULT_NETWORK` is `localnet` (`js/src/networks.ts:64`) and nothing overrides
it for a hosted build, so the front door is an empty LocalNet page pointed at
`http://localhost:4001`, which over HTTPS is blocked. The status reads "node
unreachable", the registry says "Enter a keeper app id", every tile shows a
dash, and the trust banner renders nothing because there is no app id yet.
There is no hosted URL in any document. `explorerApp` is defined in
`js/src/networks.ts` and referenced nowhere.

So a stranger currently cannot arrive at all, and every real user reaches the
console by a link somebody sent them, which is the attack medium the trust
banner exists to defend against.

---

## J2: A creator registers their first upkeep

**The first one to build.**

**Who** Somebody who wants a call made on a schedule. They may not have a
contract yet.

**Done when**

- They can register something that works without having deployed a contract first, against a demo target the console offers them.
- They are asked what they want to happen and how often, in those terms, and the fields the contract needs are assembled behind that.
- Before signing they see, on the page and not only in the wallet: which app they are paying, its account address in full, what the total will cost including group fees, and how long the funding lasts.
- The cost shown is the cost charged.
- They cannot start a registration they cannot afford, and they are told which it is before the wallet opens.
- After signing they see their upkeep, and can watch it execute for the first time without hunting for it.
- Every rejection the contract could give them is a disabled control with a specific reason instead.

**True today**

The form opens with "Target app id" and asks nine fields. The method signature
defaults to `tick()uint64` (`register-form.ts:188`), which is the pulse demo
target's hook, but the target app id itself has no default and nothing on the
page offers pulse or explains what a target is. So field one cannot be filled
by somebody without a deployed contract.

The cross-field validation is genuinely good and already turns most on-chain
rejections into a disabled button with a reason. That part is closer to done
than anything else here.

The "Up-front cost" tile is box MBR plus funding and omits the group's own
fees, so at the form's own defaults it quotes 0.0741 ALGO against an actual
debit of 0.0771. The console never reads the connected account's balance:
`accountInformation` is called once and it is for the app account, not the
user's. Neither the app id nor the app account is named anywhere in the
register panel. After registering, the new upkeep appears in a table of every
upkeep with no way to filter to it.

---

## J3: A creator manages what they have

**Who** Somebody who registered something, coming back later.

**Done when**

- They can see only their own upkeeps in one action, without reading the whole registry.
- Each upkeep has a page: what it calls, how often, what it has paid out, when it last ran, how much runway is left, and what happens when that runs out.
- They can top up or cancel from there, and are told what a cancel returns before they do it.
- If an upkeep of theirs has stopped being serviced, they learn that from the console rather than from the chain going quiet.
- Topping up somebody else's upkeep is possible and is clearly labelled as a gift, because the money becomes that creator's to reclaim.

**True today**

There is no "mine" filter and no page for an individual upkeep. The `Upkeep`
type already carries `creator` (`js/src/upkeep.ts:38`), so the filter is
buildable now with no contract or decoder change. `board.ts` already computes
availability, net reward, runs remaining, overdue rounds and last execution,
which is most of what a detail page needs.

The registry table puts a Top up button on every row including strangers', and
the drawer describes it as a feature without saying the money becomes the
creator's to reclaim.

---

## J4: A keeper earns their first fee

**Who** Somebody who wants to be paid for executing due work.

**Done when**

- They can see what work is available, what each pays net of what it costs them, and how late it is.
- Losing a race costs them nothing and the console says so plainly, because it is the ordinary outcome and the thing that decides whether keeping feels viable.
- They are never asked to sign an execution that is already known to fail.
- They can tell whether keeping here is worth it after an hour, from what the console shows them.

**True today**

Closest to done of the five. The board exists, computes net reward correctly
including the ASA surcharge, classifies availability, and deliberately shows
starved upkeeps rather than hiding the network's failures.

The blind-signing case is fixed: a failed simulation now throws before the
wallet opens rather than after. This document recorded that as done before it
was merged, and a reviewer reading `main` correctly found it false. It is true
now. What is missing is any account of earnings
over time, so a keeper cannot answer "was that worth it" from the console.

---

## J5: Anyone verifies the network is honest

**Who** Somebody deciding whether to trust this with money, including us.

**Done when**

- The console states which app it is pointed at, whether that is the published one, and whether its creator can still replace its programs.
- The build hash `verify_build` authenticates is shown, with when it was last checked, so the claim is checkable rather than asserted.
- Escrow solvency is visible: the app can pay out everything it has booked.
- Every number attributed to the contract is described as the contract's claim rather than as fact.

**True today**

The furthest along in substance and the least visible. The trust banner
correctly warns on an unpublished app id and on an unfrozen deployment, ranked
rather than exclusive, and the solvency tile is on screen. `frozen` is read
from chain state.

Nothing shows the build hash or a verification date: `web/src` and `js/src`
contain no reference to `sha256`, `hash` or `verify`. When the app id is the
published one the console says nothing at all, so absence of a warning is
carrying the entire weight of "this is the real Arcron".

---

## Decisions taken

Recorded here because each of them closes off work somebody would otherwise do,
and because the reasoning is easier to argue with than the outcome.

**J2 begins after the user has a contract.** The console does not ship a demo
target and does not offer a rehearsal mode. It links out to the integration
guide instead. This makes J2 smaller and the register form simpler, and it
concedes that the first journey we build cannot be completed by somebody who
has nothing to schedule. The consequence is that a stranger with no contract
can watch Arcron work and cannot try it, so J1 and the documentation carry
that weight rather than the console.

**The console defaults to TestNet.** `scripts/network.py` already did, and
`js/src/networks.ts` said it mirrored that file and did not. LocalNet is
reachable with an explicit `?network=localnet`. The cost is a developer working
locally typing a query parameter, which is the smaller of the two prices.

**J2 ends on the upkeep's own page.** Not on a confirmation. That needs real
routing, which the console has none of today, and it pulls the first slice of
J3 forward. It is the only version where "see their upkeep and watch it execute
without hunting for it" is unambiguously true, and `js/src/board.ts` already
computes most of what that page shows.

**The creator can run their own first execution, and a real keeper is coming.**
The form defaults to a cadence of about twenty eight seconds and the only
TestNet keeper is a half hourly cron, so a first time creator currently watches
nothing happen for up to half an hour. A "run it now" control makes the journey
complete today; a keeper running every round makes the control unnecessary.
Both, in that order.

## The scenarios

Given/When/Then for all five journeys is in [`docs/ac/`](ac/), split as it was
drafted: `j1-j5.md`, `j2.md`, `j3-j4.md`. Every scenario that cannot pass today
carries a `# Today:` line naming what actually happens, with a file and a line.

Those files also carry the decisions not yet taken, and the information
architecture the journeys imply: one browsable surface, search over upkeep id,
target app, creator address and selector, filters as chips with counts, and a
page per upkeep.

## What these are not

They did not cover rain, on the reasoning that rain pays out to wallets and
needs no console screen of its own. **That was reversed on 2026-08-29**: the
rain hub is permissionless, so opening and funding one needed a surface, and
the console now carries `/rain`, `/rain/new` and `/rain/:id`. No journey was
written for them; that gap is real and unclosed.

They do not cover the SDK or the scripts, which stay the path for anybody
doing something the console does not do.
