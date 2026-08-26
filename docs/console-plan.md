# The Arcron console plan, as decided

Worked out in conversation, scene by scene, rather than designed up front. Each
decision below was taken deliberately and several of them cut work rather than
adding it.

Context: the console has been decided as **the primary way people will use
Arcron**, not a demo and not an operator dashboard. The smart contracts and the
console have diverged: the contracts are close to finished, the console is
close to started.

## The shape

Taken from NFDomains, which the maintainer named and then showed me, because
my first description of it was wrong. I had written "one browsable surface
where mine is a filter". It is not that.

**Persistent chrome, separate destinations.** A left sidebar carries the
sections. A global search sits top centre and works from anywhere. The wallet
sits top right. Below the sections, the sidebar carries Resources
(documentation, API) and Community (Discord, X) exactly as NFD does.

| NFD | Arcron |
|---|---|
| Home | Dashboard: what this is, a few action cards, recent executions |
| Mint | Register an upkeep |
| Marketplace | The registry: search, filters, everything on the network |
| Analytics | Network health: live round rate, keeper lateness, executions |
| Manage | Your upkeeps |

So "both, but not fully separated" means shared chrome and shared search with
distinct places, rather than one page a filter reshapes. Browsing everyone's
upkeeps and managing your own are different destinations that look and behave
alike.

Filter chips still exist. They live inside the registry, the way NFD's
marketplace does it: a search box, dropdowns carrying a count badge, and the
active filters shown as removable chips with a clear all.

**Two things worth copying deliberately.**

NFD's Mint page is one input and one button. Registering an upkeep is
inherently more complex than claiming a name, but our register form is nine
fields on one screen, and that gap argues for progressive disclosure rather
than showing everything at once.

And NFD shows "Wallet not connected" as a quiet inline warning rather than
blocking the page. Everything is readable without connecting, which is exactly
what "see it working, then try it" requires.

Today Arcron has: two tabs, no search of any kind, no sidebar, no page for an
individual upkeep, no concept of "mine", a nine-field register form pinned
below both tabs, and no router at all.

## Decisions

**J2 starts after the user has a contract.** No demo target, no rehearsal mode.
The console links out to the integration guide. This makes the register form
simpler and concedes that somebody with nothing to schedule cannot complete the
first journey we build. A stranger without a contract can watch Arcron work and
cannot try it.

**The console defaults to TestNet.** `scripts/network.py` already did and
`js/src/networks.ts` claimed to mirror it and did not, so the hosted front door
is an empty LocalNet page pointed at `localhost:4001`, blocked over HTTPS. One
line. A developer working locally types a query parameter.

**Registering ends on the upkeep's own page**, not a confirmation. That needs
routing, which the console has none of, and pulls the first slice of managing
forward. It is the only version where "see their upkeep and watch it execute
without hunting for it" is true.

**The method signature stops being a single ARC-4 string.** Three plain fields:
name, parameters, returns. Assembled into a signature behind the scenes.

**A Test button simulates the call as Arcron**, not as the user. This matters:
a real execution arrives as an inner call from the keeper's app account, so a
hook checking `Txn.sender` behaves differently. Testing as the user would give
a confident wrong answer. Free, no signature, no state change.

**A checkbox where the user attests they checked it.** It does not gate on the
test passing. It is the human taking the risk, not the console granting
permission.

**The quoted cost becomes the cost charged.** Today it quotes 0.0741 ALGO and
debits 0.0771 on every configuration, missing the group's own three fees. Shown
as named lines: box deposit (returns on cancel), escrow (spent a run at a time,
returns on cancel), network fees (gone either way).

**The console reads the user's balance and blocks what they cannot afford**,
with a way to re-check without reloading. The principle behind it: the contract
is the thing that must be right, the site is free to be helpful. They are
separate, so the site can be smarter than the on-chain rules require.

**Two rates, both measured, both live.** The console conflates them today. How
fast the chain makes rounds is one thing and it drifts. How often keepers
actually turn up is another, and it is the one that decides when your work
runs. Both are measurable from data already being read: round pace is already
tracked, and lateness across the registry is exactly keeper responsiveness.
Instead of "about 28 seconds", the honest sentence is "about 28 seconds at the
current round rate; keepers here are running about 20 minutes behind, so expect
roughly that."

**Run now stays, always, not just the first time.** Anyone can execute any due
upkeep and pay themselves the fee. The language has to make clear that this is
what it is rather than hiding it.

**Mine means the connected wallet.** Not any pasted address. Simpler, and the
actions always match who you are.

**Clone to MainNet.** Somebody builds on TestNet, gets comfortable, then wants
the same upkeep on MainNet with real money. Nothing transfers between chains,
so this is a template rather than a move, and the TestNet one keeps running
unless deliberately cancelled. Cheap to design for now, awkward to retrofit.

**Expected outcomes are not errors.** Losing a race, not yet due, escrow
exhausted: the contract anticipates all of these and they should read as
information in plain words. A real error should look alarming because it means
something we did not anticipate. Today they share one pipe and come out as a
raw node string in red, so a routine race looks like a fault.

**An upkeep nobody is keeping is named as its own condition.** Different from
running out of money: that is the creator's fault and topping up fixes it.
Nobody came is the network's, and topping up does nothing. The remedies are
run it yourself, or share a link recruiting a keeper, which is a real growth
mechanic since the person who needs it most is motivated to find one.

**The fee cannot be raised after registration and that is accepted.** No
contract change. Set the fee and ceiling wrong and the upkeep is stuck, which
is the creator's own decision, so the console has to be clear at registration
rather than forgiving afterwards.

**No indexer and no backend.** A leaderboard, per-keeper history and timing
distributions all need something watching every block and writing it down. The
chain records that an upkeep ran and not who ran it. Deliberately deferred
until adoption justifies the cost. What survives without one is the question
that actually matters: how reliable is this network, answerable live from
lateness across the registry.

**Verification stays narrow.** Warn loudly on a link carrying a hostile app id,
which is a real attack. Do not build elaborate positive self-verification: the
contract is permissionless, anyone can build a front end, and a cloned console
at another domain is unaffected by anything in our UI. What protects people is
the canonical URL and app id published where they already trust.

## Build order

1. Default to TestNet. One line, unblocks arrival.
2. Router and a page per upkeep. Registering ends there.
3. The register form's honesty: real cost, name what is being paid, read the
   balance, the three signature fields, the Test button, the attestation.
4. Run now, and the two live rates.
5. Search, filters with counts, mine.

## Where the console is today

Fable rated it 4/10 as a money surface, down from 5 as a thing people look at,
explicitly because the bar moved rather than the code getting worse. Writing
the acceptance criteria made it worse still: the cost quote is wrong every
time, the balance is never read, nothing links to an explorer anywhere, and a
stranger cannot arrive at all.

Full Given/When/Then is in `docs/ac/`, and every scenario that cannot pass
today carries what actually happens with a file and a line.
