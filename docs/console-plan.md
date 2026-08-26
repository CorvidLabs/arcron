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

## Decisions taken 2026-08-26, after driving it and after the spikes

These five supersede what the AC files say where the two disagree. Both Grok
and Kimi flagged that the plan silently re-decided things `docs/ac/` had already
settled, so each of these names what it replaces.

**The Test button grades; it never shows a flat PASS.** The simulate spike
(`scripts/spike_simulate_test_button.py`) proved the honest recipe exists and
proved the naive version causes the bug the button is for: a standalone simulate
does not pay Arcron's two-slot resource tax, so a target needing seven
references passes and is then permanently unexecutable. The button certifies
"your method exists, accepts a call from the keeper app, and stays in budget"
and grades resource use: none is safe, one to four works today, five to six is
protocol-only, more than six never runs. Supersedes the plan's earlier
"simulate it as Arcron" framing, which assumed a binary answer.

**`keeper_bot.py` is fixed to match the protocol's six references.** The
five-to-six grade above exists only because the bot sends through
algokit-utils' populator, which caps at four accounts. The spike proved the
hand-built path takes six. Fixing it deletes a grade from the button's
vocabulary and makes the reference keeper as capable as the protocol, which
matters because third-party keepers will be built by copying it.

**The console is published under `corvidlabs.xyz`.** It has had no URL, which
blocks #92, #93, #94 and the one beta gate item that needs other people. A
domain we control is also what `docs/console-plan.md` already named as the
protection against poisoned links, so Pages was the faster answer to the wrong
question.

**A link naming a non-canonical app is quarantined, not merely flagged.** The
console knows the app id it ships pointing at. Any other id shows an unmissable
"this is not the Arcron deployment" state, is never written to `localStorage`,
and leaves every money button disabled until the visitor explicitly accepts.
This closes Fable's first finding, which is that the plan makes shareable links
the growth mechanic on a product whose only known attack is a shareable link.
Supersedes "the banner warns", which gated nothing.

**The NFD shape is deferred, not adopted.** Driving the console showed the page
already reads as one continuous surface, and only Registry and Keeper board
genuinely compete. Build the router and `/u/:id`, keep the two tabs, and leave
the sidebar and the other three destinations unbudgeted until something needs a
fourth reading. Supersedes the five-destination shape copied from screenshots,
which Grok correctly called the plan's most expensive unvalidated assumption.
Closes the contradiction with `docs/ac/j3-j4.md:451-467`, which specified three
routes and not five, in the AC files' favour.

## Build order

0. Fix `keeper_bot.py`'s reference cap, and pin the boundary on LocalNet.
   Nothing above it in this list is honest until the bot matches the protocol.
1. ~~Default to TestNet.~~ Done 2026-08-26, and **this line claimed it was
   done before it was**. `js/src/networks.ts` still read `'localnet'` when it
   was written. Nothing pinned the value, so the document and the code
   disagreed silently until an agent building the hosted bundle found it
   rewriting its own address to `?network=localnet&app=none`: a published page
   would have opened every stranger against `http://localhost:4001`, which
   HTTPS blocks as mixed content. `js/test/networks.test.ts` now pins it, so
   the claim is falsifiable rather than merely written down.
2. ~~Router and a page per upkeep. Registering ends there. Three routes: `/`,
   `/u/:id`, `/register`.~~ Done; see "What items 2 and 3 landed as" below.
3. ~~Quarantine non-canonical app ids, and stop persisting them.~~ Done. It
   landed before publishing, which was the point: publishing is what makes a
   poisoned link worth sending.
4. ~~Publish under `corvidlabs.xyz`, and name the URL in the docs.~~ The path
   exists and is proven; the push is the owner's. See below.
5. The register form's honesty: real cost, name what is being paid, read the
   balance, the signature field, the graded Test button, the attestation.
6. Run now, and the keeper ping rate, which is the only part of the old item 4
   that is still missing.
7. Search, filters with counts, mine.

## The address, and how the console gets there (item 4)

**The console's address is `https://corvidlabs.xyz/arcron/console/`.**

The site is a separate repository, `CorvidLabs/site`: an Astro build deployed
to an nginx VPS by `.github/workflows/deploy.yml`, which tars `dist/`, ships it
over ssh, and rsyncs it into the web root with `--delete`. Astro copies
everything under `public/` into that build verbatim, and `public/arcsite/studio`
is already a compiled application published exactly this way. So a built bundle
committed at `public/arcron/console/` is served at `/arcron/console/`, and
pushing `main` is what deploys it. There is no second mechanism to build.

Three commands, none of which publish anything:

```bash
fledge run web-build-hosted                           # --base-href /arcron/console/
fledge run web-verify-hosted                          # prove it loads at that subpath
fledge run site-console -- --site ../../site          # stage into the site checkout
```

`web-verify-hosted` is in the `ci` lane, and it exists because the hosted build
differs from the ordinary one by a single attribute in `index.html`. Get that
attribute wrong and `ng build` still succeeds, the tests still pass, and every
script tag on the page resolves to the domain root and 404s. So the check
serves the bundle at `/arcron/console/` on a throwaway server and fetches every
file in it, including the ones no markup names: `network-bar.ts:152` injects
`brand/theme.js` from a string at runtime, so crawling index.html alone would
call a broken bundle healthy.

Two things are still owed, neither of them buildable here:

- **A 404 fallback, the moment item 2 lands.** The site's nginx serves
  `try_files $uri $uri/index.html $uri/ =404`, so `/arcron/console/u/17` will
  404 on reload the day routes exist. `CorvidLabs/site` needs
  `location ^~ /arcron/console/ { try_files $uri $uri/index.html /arcron/console/index.html; }`
  before or with the first routed publish. Shareable links are the growth
  mechanic; a link that 404s is worse than no link.
- **The default network.** `js/src/networks.ts:64` still reads
  `DEFAULT_NETWORK = 'localnet'`, so the hosted build opens on LocalNet against
  `http://localhost:4001`, which a browser blocks as mixed content over HTTPS.
  Loading the hosted bundle in Chrome rewrote its own address to
  `/arcron/console/?network=localnet&app=none`, which is item 1's bug arriving
  at the front door: the subpath is carried correctly and the chain is not.
  Build order item 1 is recorded above as done and, at `main` as of this
  writing, is not. Publishing before it is genuinely done buys a page that
  greets every stranger with an empty registry and an unreachable node.

## Where the console is today

Fable rated it 4/10 as a money surface, down from 5 as a thing people look at,
explicitly because the bar moved rather than the code getting worse. Writing
the acceptance criteria made it worse still: the cost quote is wrong every
time, the balance is never read, nothing links to an explorer anywhere, and a
stranger cannot arrive at all.

Full Given/When/Then is in `docs/ac/`, and every scenario that cannot pass
today carries what actually happens with a file and a line.

## What driving it in a browser settled (2026-08-26)

Grok named this the plan's biggest unvalidated assumption, and it was right to:
the NFD shape was copied from screenshots onto a console nobody had opened.
The console has now been loaded in Chrome against live TestNet
(`?network=testnet&app=769891898`). What that changed:

**The dev command is broken, and it fails silently.** `bun run ng serve` builds
and serves a page with the right `<title>` and an empty body. The console shows
`SyntaxError: Unexpected token 'export'` from `js/src/networks.ts`: Vite serves
the workspace TypeScript untranspiled. A previous review reported this as "did
not start", which is generous. It starts, looks like a rendering bug, and sends
you to the wrong place. `ng build` is fine, so CI never sees it. Anyone told to
run the console from `README.md` hits a blank page first.

**The NFD analogy is worse than the plan assumed, and for a new reason.** Not
because five destinations are too many for five rows, though they are. Because
the page already reads as one continuous surface and its parts do not compete:
network bar, freeze notice, connect row, four stat cards, a two-tab body, the
register form, and an activity log, top to bottom. The two tabs are a real
split, and Registry and Keeper board are genuinely different readings of the
same boxes. Nothing else on the page wants a room of its own. Build order item
2 stands, because there is no per-upkeep destination at all and no router, but
"five NFD destinations" should be treated as unbudgeted until something needs a
fourth reading.

**The register form's live selector already works.** `METHOD SIGNATURE` takes
`tick()uint64` and prints `selector 0x4d4d5f0b` beneath it as you type. Item 3
should not re-specify it. The attestation checkbox and the simulate-as-Arcron
test are genuinely absent.

**Item 4 is more done than the plan says.** Both tabs already carry per-upkeep
`Execute` buttons, and the status bar reads `2.7 s/round` with `(measured)`
shown at the interval field. What is missing from item 4 is not the button and
not the pace: it is the keeper ping rate, which no keeper currently publishes.

**One footer line is unconditional.** "Registry reads are public box state;
signing on LocalNet goes through KMD" renders with TestNet selected.

**A stat card repeats itself.** `ESCROWED 0.608 ALGO` carries the sublabel
`0.608 ALGO`.

### The live registry is not being kept, and the reason is two stacked bugs

The console reports `5 UPKEEPS`, `5 DUE NOW`, `MEDIAN LATENESS 8,117 rounds`,
`EXECUTIONS 3`, `PAID TO KEEPERS 0.012 ALGO`. Every upkeep on alpha-2 is
overdue; the oldest by about seventeen hours. Two independent causes, neither
of which announces itself:

1. **The cron keeper is green and does nothing.** `.github/workflows/keeper-bot.yml`
   runs every thirty minutes, succeeds in under a minute, and skips: the run at
   16:01 UTC logs `KEEPER_MNEMONIC:` empty and the notice "nothing was
   serviced". That skip-clean behaviour is deliberate and documented at the top
   of the file. Its cost is that roughly forty-eight green runs a day assert
   nothing, and the dashboard reads as a working keeper.
2. **The local keeper bot services the wrong app.** The running process is
   `scripts.keeper_bot --network testnet --app-id 769823086`. That is alpha-1:
   superseded, immutable, pre-governance, and listed in `docs/status.md` among
   the three deployments that "must not be used". `deploy/vps/install.sh` was
   already corrected to `769891898`; this process predates the fix and was
   never restarted.

So alpha-2 has never been serviced by a keeper. The three executions on upkeep
18 are from the deploy-time e2e, not from keeping.

Three things follow. **The 1.0 scope's dogfood claim is not true today** and
should not be cited as evidence. **The median-lateness figure measures our
misconfiguration, not keeper responsiveness** — which is Grok's §3 objection,
arriving by a shorter route than it expected: the number is not diluted by
reverting targets, it is entirely an artefact. Any "keepers are running about N
behind" sentence built on it would be a confident lie. And **upkeep 18 is
`catch-up` with 23,478 rounds of backlog at a 25-round interval**: about 939
missed intervals against 17 funded runs. The first keeper to reach it drains it
to starved within one pass. That is the contract behaving exactly as specified,
and it is also the single best live demonstration of why the policy choice
matters, so it is worth watching rather than quietly topping up first.

### The empty state renders while connecting

On load, before the first box read returns, the registry renders `0 REGISTERED ·
0 DUE` and the sentence "No upkeeps on app 769891898 yet. Register one below to
watch the network work." The status bar still reads `connecting…` at that
moment. A second later the same page reads `5 REGISTERED · 1 DUE`.

This is the failure `tests/test_app_id_consistency.py` was written about,
reached by a different route. That docstring says a visitor "would have seen an
empty registry and concluded the network was dead" because the console pointed
at a superseded app. The app id is right now; the console still says the
network is empty, just for a shorter time, and it says so in the confident
voice reserved for a fact it has checked. "No upkeeps yet" is a claim about the
chain. Before the read returns, the console does not know it.

Not knowing yet and knowing there is nothing are different states and the
registry currently collapses them. `status()` already distinguishes them.

### The keeper was started (2026-08-26)

The local bot now runs against `769891898` and alpha-2 is being serviced for
the first time. The first pass executed all five upkeeps: `+8381 µALGO` on 21,
whose fee ceiling had ramped, and `+4000` on each of the others.

Upkeep 18's catch-up backlog replays **one interval per pass**, not all at once:
its next-due advanced 66669682 to 66669707, exactly one 25-round interval. So
it drains over roughly seventeen more passes rather than instantly. Worth
watching rather than topping up, because it is the clearest demonstration the
network will produce of what the policy choice costs.

The cron keeper in `.github/workflows/keeper-bot.yml` is still a no-op: its
`KEEPER_MNEMONIC` secret is unset. Until it is set, the local bot is the only
thing keeping the network, which means the dogfood stops when this machine
does.

### What upkeep 18 proved about catch-up, within the hour

The prediction above was that 18 would drain over roughly seventeen passes. It
did, and the result is worth more than the prediction:

| | before keeping | after |
|---|---|---|
| runs | 3 | 20 |
| escrow | 68,000 µALGO | 0 |
| funded runs | 17 | 0 |
| rounds behind | 23,478 | 23,437 |

Seventeen funded runs bought **41 rounds** of catch-up against a 23,478-round
backlog: 0.17% of the way, for the whole escrow. The upkeep is now starved,
still sixteen hours late, and no better off than when it was merely unkept.

This is not a bug. `catch-up` means "replay every missed interval, one fee
each", and that is exactly what it did. But it is the first time the shape of
that promise has been visible on a real registry, and the shape is: **on a
short cadence, catch-up after any meaningful outage is an escrow incinerator
that never catches up.** The cost of the outage is `outage ÷ interval` fees,
which for a 25-round cadence and a seventeen-hour gap is about 939 fees, or
3.756 ALGO to replay a demo.

The console's register form presents the two policies as a neutral pair, with
`Skip ahead` as the default and one line of prose each:

> **Catch up** Replay every missed interval, one fee each. For work where every
> period counts.

That sentence is accurate and it is not sufficient. It does not say that the
number of fees is unbounded by the escrow, that a fast cadence multiplies the
exposure, or that the replay can consume everything and still not arrive. The
three upkeeps that were merely six hours late and on an eleven-hour cadence
caught up in one run each and cost one fee each, which is the case the sentence
describes. 18 is the case it does not.

Two changes follow, and neither is in the build order yet:

1. The register form should quantify the exposure at the moment the choice is
   made: with the interval and funding already entered, "if this goes unkept
   for a day, catch-up replays about N runs and costs about X ALGO, which is
   more than you are funding" is arithmetic the form already has every input
   for.
2. `docs/integrating.md` should carry the same warning where the policy is
   chosen in code, not only where it is defined.

18 is deliberately being left starved rather than topped up. It is the only
live example of this on the network and it costs nothing to keep as evidence.

### `ng serve` fixed

`@corvidlabs/arcron` publishes raw TypeScript through its `exports` map
(`js/package.json`), and Vite's dependency optimizer treated it as a
prebundlable JS dependency, serving `js/src/networks.ts` untranspiled. The fix
is one option on the dev-server target, not the build target:

```json
"serve": { "options": { "prebundle": { "exclude": ["@corvidlabs/arcron"] } } }
```

Excluded from prebundling, the workspace package is compiled as source like the
rest of the app. `ng build` never needed this, which is why CI never saw it.

### alpha-3, and what the update proved (2026-08-26)

`verify_build` was red against the live app and nobody had run it since the
deploy. Two commits carrying the payer-binding asserts, `f980321` and
`1499963`, landed after alpha-2 went up and were never deployed, so the
deployment everyone was pointed at was missing the fix for the theft path Fable
proved.

alpha-3 corrects it as an **update in place**. The `Upkeep` struct and the ABI
are unchanged, 33 added lines and no deletions, so the boxes survived: five
upkeeps, escrows intact, solvent, no creator asked to cancel and re-register.
`verify_build` is green and the TestNet e2e passes against the new bytecode,
including the cross-upkeep isolation stage.

That is the first exercise of the governance update path on a real chain, and
it is the argument for staying unfrozen made concretely rather than in prose:
the same change against a frozen app would have been a new app id and a
migration for every creator.

It is also a lesson about the evidence, not the contract. Nothing in
`contract.py` was wrong. What was wrong was that the tree and the chain had
drifted apart for a day and the one command that checks it was not run by
anything. `verify_build` belongs in a scheduled job against the live app, not
in a human's memory.

## What items 2 and 3 landed as (2026-08-26)

**Three routes.** `/` keeps the two tabs, `/u/:id` is new and is where
registering now ends, `/register` is the form, and an unknown path redirects to
`/` carrying its query string. `web/src/app/routes.test.ts` asserts the count
rather than the existence of each, because the count is the decision that was
taken and a fourth destination should have to argue for itself.

**The query parameters survive by policy, not by discipline.**
`withRouterConfig({ defaultQueryParamsHandling: 'preserve' })` means no link in
the console has to remember `?network=` and `?app=`, and the one place that
changes them asks for `'merge'` explicitly. That sync moved out of
`ArcronService` and into the shell: it goes through the router, because
`history.replaceState` leaves the router's own copy of the URL stale and the
next `routerLink` rebuilds the address from the stale copy. Two things were
learned doing it, both by driving it:

- `router.navigate([], { queryParams })` with no `relativeTo` resolves against
  the root, so it rewrote `/register` to `/`. The sync builds a `UrlTree` from
  the settled URL instead.
- The sync is itself a navigation, so keying focus management on "a navigation
  ended" stole focus on page load. Focus is keyed on the *path* changing.

**Quarantine gates rather than warns.** The identity comparison left the trust
banner, which now carries only what a read can tell you plus the one case with
nothing to compare against. What replaced it is a state: money is dead until
the visitor continues, the app id is never written to `localStorage`, both ids
are named, and one button goes back. LocalNet is deliberately outside it, and
`docs/ac/j3-j4.md`'s D1 was taken as B at the same time: topping up a
stranger's upkeep is a gift, so it left the table rows and lives on `/u/:id`
with wording that names the creator in full.

**What was deliberately not built:** search, filters, "mine", the graded Test
button, the honest cost quote, the ASA bonus actions on `/u/:id` (both service
methods still have no UI, and a permanent 0.1 ALGO opt-in deserves its own
wording pass), and any fourth destination.

## What is blocked on the owner, as of 2026-08-26

Build order items 0 through 5 have landed. Three things remain, and none of them
can be finished from inside this repository.

**The nginx fallback, and it is now load-bearing.** `web-build-hosted` copies
`index.html` to `404.html`. nginx does not use that by default, and an earlier
version of this section concluded from that it would not work here and proposed
a bespoke `try_files` chain. That was wrong about this site. `CorvidLabs/site`
already wires the fallback explicitly for every subpath application it hosts,
and the console should follow that convention rather than invent one:

```nginx
location ^~ /arcron/console/ {
    try_files $uri $uri/index.html $uri/ =404;
    error_page 404 = /arcron/console/404.html;
}
```

`error_page 404 = /uri` with the equals sign returns the status of the
substituted page, so a deep link gets 200 and the console's router takes it from
there. Verified against the built bundle: `/arcron/console/`, `/u/19`,
`/u/71` and `/register` all return 200 under this rule and 404 without it.

That block belongs in `deploy/vps/nginx.conf` in `CorvidLabs/site`, next to the
`/arcsite/examples/*` blocks it copies. Without it, every deep link works when
clicked inside the console and breaks when pasted or reloaded, which fails only
for the person you sent it to.

**The push itself.** `fledge run site-console -- --site ../../site` stages the
bundle and prints the git commands. Nothing has been pushed. The repository is
still private pending the audit in issue #23, and making it visible is a
decision, not a build step.

**The keeper is still a laptop.** `.github/workflows/keeper-bot.yml` remains a
no-op for want of `KEEPER_MNEMONIC`. The 30 day beta clock cannot start until a
keeper runs somewhere that is not this machine, and the notifier is not running
at all, so there is no record to point at even once it does.

### What the parallel work cost, and what it caught

Four agents worked at once and two of them shared `web/`. The merge cost five
conflicts, all real: both wanted `fledge.toml`, both changed `registry-table.ts`
(explorer links from one, router links from the other, and both were kept), and
both edited this file's build order.

It also caught something a single worker would not have. The register form's
Test button was written against a keeper bot that capped at four references, and
graded five and six as "the protocol allows this, our keeper does not". While it
was being written, the other agent removed that cap. Because the grade was built
behind one constant with a comment naming the condition, the collapse was a
one line change and a test now asserts the two numbers are equal, so the band
returns if the keeper ever falls behind the protocol again.
