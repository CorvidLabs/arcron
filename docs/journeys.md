# What the console has to let people do

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

One browsable surface, search first, where "mine" is a filter rather than a
separate application. You search or browse, you land on any individual
upkeep's page, you see what it is and who owns it, and the actions available
to you appear there. You never pick a mode.

A keeper and a creator are looking at the same registry with different
questions. They are not looking at two products. So the keeper board stops
being a place and becomes a saved filter: due now, sorted by what it pays.

Today the console has two tabs, no search of any kind, no page for an
individual upkeep, no concept of "mine", and a register form permanently
pinned below both tabs.

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
wallet opens rather than after. What is missing is any account of earnings
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

## What these are not

They do not cover rain. Rain pays out to wallets and that is how people know
it works; it needs no console screen of its own.

They do not cover the SDK or the scripts, which stay the path for anybody
doing something the console does not do.
