# Rating the Arcron console plan

| | |
|---|---|
| **Target** | `docs/console-plan.md` at `main` @ `5edea87`, read against `docs/journeys.md`, `docs/ac/`, `web/src/`, `js/src/`, `smart_contracts/keeper/contract.py` |
| **Date** | 2026-08-26 |
| **Prior passes** | `2026-08-25-fable-5.md`, `2026-08-26-fable-5-console.md`, `2026-08-26-fable-5-rescore.md`, `2026-08-26-fable-5-console-primary.md` (console 4/10) |
| **Method** | Source read at HEAD. Every claim in the plan, and every `# Today:` line I relied on, was checked against the code rather than taken from the document; roughly forty of the AC's claims were spot-checked, five found false on substance. Contract read for the four payer-binding sites and for `execute`'s inner call. |
| **Not done** | No browser, no screen reader, no live chain this pass. No AXE. I did not check every one of the seventy-odd `# Today:` lines. |
| **Verdict** | **5 / 10 as a plan.** |

Short version: a real plan, with real cuts, and reasoning worth arguing with. I
would rather review this than most product documents. Its failure is systematic
rather than scattered, and it clusters in one place. Of the six decisions the
acceptance criteria explicitly escalated to the maintainer, the plan answers
one; the four it leaves open are the security ones. Its two headline additions,
routing and a link-sharing growth mechanic, both amplify the one attack this
project actually knows about, and neither is paired with a change to the
defence. And it reverses the architecture the acceptance criteria were written
against without regenerating them, so it currently has no acceptance criteria.

It is a feature plan where a risk plan was needed.

---

## 0. What is not true in the source documents

The brief said to verify rather than trust. That was the right instruction.

### 0.1 The acceptance criteria describe a product the plan does not build

`docs/ac/j1-j5.md:17-20` states its own scope: "Not the two-tab dashboard that
exists, but the shape `docs/journeys.md` sets out: **one browsable surface,
search first, 'mine' as a filter, a page per upkeep, no mode to pick**."
`docs/ac/j3-j4.md:453,466-467` says "Three routes, not two tabs" and "**You
never pick a mode.** A keeper and a creator both land on `/`". Its D5 concludes
"My read: A. 'You never pick a mode' cuts both ways; the page should not pick
one either" (`j3-j4.md:678-679`), and `j3-j4.md:511-513` requires default
arrival to be the whole registry.

`console-plan.md:15-17,23` retracts exactly that: "I had written 'one browsable
surface where mine is a filter'. It is not that. **Persistent chrome, separate
destinations**", with `Manage | Your upkeeps` as its own place.

The retraction is correct and the correction is good practice: the maintainer
looked at the real product and fixed a description made from memory. But
nothing followed through. Three agents drafted about 116KB of Given/When/Then
against the superseded shape, and it is still the ledger the plan's own closing
section points at (`console-plan.md:155-163`). **The plan has no acceptance
criteria at the moment**, only criteria for a different product that shares its
name.

### 0.2 At least six `# Today:` lines are false at HEAD

`docs/ac/j2.md:7` says it was written against `69fdbab`. HEAD is `5edea87`.
Commit `1499963` landed at 09:10:04 on 2026-08-26, fifty three seconds before
`9058cbb` committed the AC files, and the AC picked up its `contract.py`
changes and not its `keeper-txns.ts` or `web/src` changes.

1. **`j3-j4.md:337-347`** says `execute` "never reads `failureMessage`", so a
   keeper is asked to sign a call already known to fail. False.
   `js/src/keeper-txns.ts:322-328` throws on `group?.failureMessage` with a
   written sentence naming the lost race. This was my M3 and it is closed.
2. **`j1-j5.md:275-283`** says "`refresh()` never consults `genesisMatches`"
   and "every write guard in the console keys on `status() === 'ready'`".
   False. `arcron.service.ts:151-156` defines `canWrite` as
   `status === 'ready' && genesisMatches !== false && appId !== null`.
3. **`j1-j5.md:498-513`** says the keeper board's Execute has no read guard.
   False. `upkeep-board.ts:88` reads `|| !arcron.canWrite()`, and there is now a
   central backstop at `keeper.service.ts:120-129` that every write passes
   through. That is my M6 fixed properly, at the layer I asked for.
4. **`j2.md:312-317`** says a wrong-chain node leaves Register live. False.
   `register-form.ts:369` is `this.arcron.canWrite()`.
5. **`j2.md:325-330`** says a null app id leaves the button live. False, same
   clause (`arcron.service.ts:155`).
6. **`j3-j4.md:57-58`** says Top up is "enabled purely on `canFund`". Partly
   false: `registry-table.ts:131` is `[disabled]="!row.canFund || !reads()"`.
   The ownership half of that complaint is correct and remains open.

Separately, `5edea87` rewrote `docs/journeys.md`, so every `docs/journeys.md:NN`
citation inside both AC files is shifted by roughly 23 lines, and a large number
of `keeper.service.ts` and `arcron.service.ts` line numbers moved when the
backstop was inserted.

So the ledger the plan sized its work from overstates the gap in at least six
places, is shifted throughout, and describes the wrong architecture. Nothing
regenerates it.

### 0.3 The pattern you named as dominant recurred this week, inside the file written to document it

`web/src/app/core/frozen.test.ts:9-12` carries the comment: "The real function
the console runs, not a copy. An earlier version of this file declared its own,
so reverting the coercion in the service left every test here green."

Thirty two lines later, `frozen.test.ts:43-49` declares a private copy of the
`canWrite` predicate and tests that:

```ts
/** The predicate `ArcronService.canWrite` computes, isolated from signals. */
function canWrite(state: { status: string; genesisMatches: boolean | null; appId: number | null }): boolean {
    return state.status === 'ready' && state.genesisMatches !== false && state.appId !== null;
}
```

Delete `arcron.service.ts:151-156` entirely and all five of those tests stay
green. The lesson was written down, in that file, above the recurrence. This is
the single strongest argument for what section 2.1 says is missing.

---

## 1. What the plan gets wrong, ranked

### 1.1 It makes shareable links the growth mechanic on a product whose only known attack is a shareable link, and changes nothing about the defence

Every step confirmed in code.

- The plan adds routing (`console-plan.md:68-71`) so every upkeep has a URL,
  then proposes "share a link recruiting a keeper, which is a real growth
  mechanic" (`:124-126`).
- `arcron.service.ts:179-183` already rewrites the address bar on every load
  with `entryLink(...)`, and `entry.ts:82-86` always emits both `network` and
  `app`. So **every URL a user can copy already carries an explicit app id**.
  Users will be trained that Arcron links look like `?app=<number>`, which is
  the exact shape of the payload.
- `entry.ts:56-79` puts a linked app id ahead of memory, and the effect at
  `arcron.service.ts:170-176` writes it to `localStorage`. After one poisoned
  link, a later bare visit to the canonical URL still resolves to the hostile
  app. The poisoning survives the link.
- On that hostile app the banner fires correctly and loudly
  (`trust-banner.ts:39-50`) **and disables nothing**. `canWrite`
  (`arcron.service.ts:151-156`) does not consult the notices; nor does
  `register-form.ts:369`, nor `registry-table.ts:273`, nor the backstop at
  `keeper.service.ts:123`.
- `registry-table.ts:316` sets `canFund: canSign` with no ownership test, next
  to `canCancel: canSign && yours` on line 315. Top up is live on every row of
  the attacker's registry, and `contract.py:307,352` mean only `upkeep.creator`
  can withdraw it.

The plan's whole answer is `console-plan.md:140-144`: "Warn loudly on a link
carrying a hostile app id, which is a real attack." That warning already exists
and is already loud. The open question, raised as Decision 5 at
`docs/ac/j1-j5.md:630-651` and again as a hard criterion at `j2.md:296-297`
("the submit control is disabled until they acknowledge it or switch"), is
whether it disables anything. The plan does not answer it, so it preserves a
state where a victim sees a red banner and a live Register button, and then adds
two features that increase how often a victim gets there.

Cheap now and expensive later, literally: gating `canWrite` on a bad-toned
notice costs one line today, because the guard is already centralized at
`keeper.service.ts:123`. After five destinations and a detail page exist, it is
a hunt. Note that the naive form of the gate breaks self-hosting, because every
LocalNet app id and every self-hoster's TestNet app carries a notice
(`trust-banner.ts:29-38`); `j1-j5.md:630-651` already works that trade, and the
plan should resolve it rather than skip it.

### 1.2 "Verification stays narrow" cuts three things that are not self-verification, and drops a journey without saying so

The argument at `console-plan.md:140-144` is right about one thing and used for
four. Right: nothing the console says *about itself* is evidence, because anyone
can clone it. I will defend that. It is the cut I would have made.

It is then used to drop three controls that are not the console vouching for
itself:

- **Explorer links.** `explorerApp` is defined at `js/src/networks.ts:59` and
  referenced nowhere in `web/src` or `js/src` (grepped: two hits, both inside
  its own definition). There is no `explorerAccount` and no `explorerTx`.
  Linking to Pera's explorer is the opposite of self-verification: it is
  delegation to a party the attacker does not control. `docs/journeys.md:66`
  makes it a J1 completion criterion; the build order never mentions it.
- **The full app account address.** It appears once, abbreviated, in the footer
  (`app.ts:44-47`, `app.html:47-49`). It is the one value the user can compare
  against what the wallet is about to show them, and it is not on the page in a
  form they can compare.
- **The superseded tier.** `769823086` was the canonical app on 2026-08-24
  (`docs/releases.md:141`). Every link shared in that window now renders our own
  former deployment with the same phishing headline a stranger's look-alike gets
  (`trust-banner.ts:39-50`, one branch). That is not verification. It is telling
  our own users apart from an attacker, and it is live today.

The build hash is the one I would concede. In a compromised-bundle model the
page asserting its own integrity is weak. But `docs/journeys.md:181` lists it as
a J5 completion criterion, and the plan neither builds it nor records that J5
has been narrowed. A plan that cuts an acceptance criterion should say so where
the criterion lives.

The load-bearing point: `noticesFor` returns `[]` for the canonical frozen app,
for no app id at all, and for no app id with a failed read
(`trust-banner.ts:22-23`). Three states with completely different trust
meanings, one identical rendering. The phishing vector works because the reflex
the console teaches is "no red means fine". Cutting positive identity keeps
teaching it.

### 1.3 It answers one of the six decisions the acceptance criteria escalated

`docs/ac/j1-j5.md:560-694` ends with six explicit "decisions the maintainer has
to make", each with a stated trade:

| Decision | Plan |
|---|---|
| 1. Where the browser's default network lives | **Answered** (`:63-66`) |
| 2. What "the money involved is real" can mean before MainNet | **Silent** |
| 3. Where positive identity lives | **Silent** (cut by 1.2) |
| 4. Where the expected build hash comes from | **Silent** (cut by 1.2) |
| 5. Whether a trust warning should disable writes | **Silent** |
| 6. Whether shared links should pin the app id | **Silent** |

Grepped: `console-plan.md` contains zero occurrences of `hash`, `superseded`,
`retired`, `disable`, `acknowledg`, `localStorage`, `remember`, `AXE`,
`accessib`, `focus`, `mobile`, `poll`, `rate limit`, `notif`, `asset`, `ASA`,
`bonus`, `refund`, `genesis`. The word "cancel" appears twice, both inside a
cost line ("returns on cancel", `:87-88`) or an aside ("deliberately cancelled",
`:114`).

Decision 2 deserves pulling out, because it is J1's core and has no cheap
answer. `docs/journeys.md:63` requires a stranger to see "that the money
involved is real". On TestNet it is not, and `j1-j5.md:588-604` lays out the
choice honestly: rewrite the criterion and ship a page whose own honest reading
is "nothing is at stake", or accept that J1 cannot complete before a MainNet
gate `CLAUDE.md` says any struct change restarts. The plan proceeds as if J1 is
buildable and never touches it.

### 1.4 The build order does not build the plan

Half of `console-plan.md` is the NFD shape: sidebar, five destinations, global
search top centre, wallet top right, Resources and Community links (`:12-49`).
**None of the five build steps builds any of it.** Step 2 is "Router and a page
per upkeep"; step 5 is "Search, filters with counts, mine". No step produces the
sidebar, Home, Analytics, or the documentation and community links.

Counting the plan's own Decisions against its own Build order, ten of seventeen
have no step: the NFD shape itself; progressive disclosure of the nine fields
(`:42-45`); the unconnected-wallet inline warning (`:47-49`); Clone to MainNet
(`:111-114`); "expected outcomes are not errors" (`:116-120`); the
nobody-is-keeping condition (`:122-126`); explorer links; gift labelling on top
up; the be-clear-at-registration consequence of the frozen fee (`:128-131`);
and everything in the verification paragraph (`:140-144`).

That is not an ordering problem. It is a sign the shape and the work were
reasoned about in different sittings.

### 1.5 "Default to TestNet. One line, unblocks arrival" is neither

**It does not unblock arrival**, because nothing publishes the console.
`fledge.toml:44` defines `web-build-hosted` with `--base-href /arcron/console/`;
no lane runs it (`fledge.toml:59-70`, three lanes) and no workflow runs it
(`.github/workflows/` holds `ci.yml` and `keeper-bot.yml`). No document names a
hosted URL. `web/README.md:11-21` still tells a stranger to install Bun, Poetry
and Docker, run `algokit localnet start` and a Python end-to-end script, and
read an app id out of its log. Flipping the default gives a correct default to a
page nobody can reach: the fix correct at the site where the bug was found and
absent at the sibling.

The plan's own last sentence on verification makes this sharper than a missing
deploy step. `console-plan.md:143-144`: "What protects people is the canonical
URL and app id published where they already trust." That is the right answer,
and it is the one thing the build order does not produce. The defence the plan
chooses does not exist, and step 1 does not create it.

**It is also not one line in the right place.** `DEFAULT_NETWORK` lives at
`js/src/networks.ts:64`, inside the published SDK `@corvidlabs/arcron`
(`js/package.json` exports `./networks`). Flipping it changes the default for
every SDK consumer, not the console's front door. The console-shaped hook
already exists: `window.__ARCRON__` (`web/src/app/core/wallets.ts:26-36`)
carries exactly one field and nothing anywhere sets it. `j1-j5.md:577-585`
already weighs both options.

### 1.6 The attestation checkbox replaces four disabled controls the criteria require, and gates the wrong risk

`console-plan.md:81-83`: a checkbox where the user attests they tested their
hook, gating nothing, "the human taking the risk".

The criteria demand hard blocks in four places: `j2.md:92-93` ("the submit
control is disabled / and the reason names the app id and says no application
with that id exists"), `j2.md:107-108` ("disabled / the reason says the target
does not expose that method"), `j2.md:119` ("the failure is surfaced before the
wallet opens"), and the umbrella at `j2.md:726-727`, that every contract
rejection is "either unreachable by construction from this form, or prevented by
a disabled control naming that specific problem". A Test the user may fail and
proceed past is precisely the state `j2.md:718-720` says must not exist. The
existing form is *closer* to that bar than the plan is: `register-form.ts:208-224`
already turns cross-field rejections into a disabled button with a reason, which
my last pass called the best part of this console.

And the risk is inverted. A user pointed at a hostile deployment gets a warning
that gates nothing (1.1). So the plan adds a consent ritual for a mistake the
user can undo by cancelling, and declines one for a loss they cannot recover.
`j1-j5.md:648-651` already made the argument: "acknowledgement dialogs are the
control users learn to dismiss fastest." Teaching the dismiss reflex on the
harmless case and relying on attention in the harmful one is worse than neither.

### 1.7 The Test button tests the wrong transaction at the wrong moment, and cannot reproduce a real execution

`console-plan.md:76-79` is right about the thing everybody gets wrong: a real
run arrives as an inner call, so a hook reading `Txn.sender` sees the keeper's
app account. Confirmed at `contract.py:463-478`,
`itxn.ApplicationCall(...).submit()`.

Three problems follow that the plan does not see.

**It cannot be faithful.** The only way to simulate a call whose sender is the
app account is a top-level transaction under `allowEmptySignatures`, which is
what `keeper-txns.ts:305-311` already does for `execute`. A top-level call has
`Global.caller_application_id == 0`; a real inner call has it set to the keeper
app id, which is the standard way a target accepts calls only from a specific
app. The inner call also carries no foreign-reference arrays of its own
(`contract.py:463-478`). So the Test button will pass targets that fail in
production and fail targets that pass.

**It is the wrong transaction.** `j2.md:529-530` requires simulating the
*register group* at click time, and `j2.md:584-586` says only a simulation
"immediately before submission" closes the stale-`next_upkeep_id` window
(`keeper-txns.ts:142` reads the counter while the group is built, against
`contract.py:240-241`). The plan's Test button probes the target hook, earlier,
and the plan has no register-time simulation at all: `keeper.service.ts:37-42`
calls `txns.register`, and `keeper-txns.ts:96-145` contains no `simulate`.

**It may be contract work.** At registration the upkeep does not exist, so there
is no box and no `execute` to simulate. Making it faithful most likely needs a
simulate-only method on the contract, sitting inside a step labelled console
work (`console-plan.md:150-152`).

Keep the button. Change the claim to what it can prove: the target accepts the
selector and the encoded arguments. It does not prove the target accepts Arcron.

### 1.8 The plan is silent on cancel, which is a one-click money button today

`registry-table.ts:136-145` renders Cancel and `registry-table.ts:347-349` is
`protected cancel(row: Row): void { void this.keeper.cancel(row.upkeep); }`.
One click, no dialog, no refund preview. `j3-j4.md:183-196` makes "a cancel
states its refund before it is signed" a criterion, and the refund is computable
on the page today (`contract.py:346-352`, `boxMbr` at `js/src/upkeep.ts:88-90`).

The plan's cost work is scoped entirely to registration (build step 3, "the
**register form's** honesty"). Cancel, top up and execute keep their current
behaviour: no quote, no confirmation, and for cancel, no statement of what comes
back. A plan whose headline cost decision is "the quoted cost becomes the cost
charged" should apply it to the three paths that quote nothing at all.

### 1.9 "Three plain fields: name, parameters, returns" makes a naive parser authoritative

`console-plan.md:73-74`. Today the signature is one field, parsed properly by
algosdk for encoding (`js/src/keeper-abi.ts:50-51`). The only naive parse is
cosmetic: `register-form.ts:242-243` does `signature.match(/\((.*)\)/)?.[1]`
then `inner.split(',')`, wrong for any tuple argument such as
`(uint64,address)`, and today it only mis-renders a hint.

Split the signature into three fields and that arithmetic becomes the thing that
*builds* the signature. A wrongly assembled signature is not an error: it is a
different, perfectly valid selector. The upkeep registers, the box is paid for,
and every execution reverts inside the inner call forever. The fee cannot be
raised (`:128-131`), so the only exit is cancel. Three fields is the right
instinct for `tick` / `uint64`. Make the assembly paren-aware and show the
assembled signature back before signing.

### 1.10 "An upkeep nobody is keeping" names two conditions where there are three, and drops the only remedy the criteria call exact

`console-plan.md:122-126` distinguishes starved (creator's fault, top up fixes
it) from unserviced (network's fault, top up does nothing). There is a third,
and it is the one a first-time creator will hit: **the target reverts**.
`js/src/board.ts:49-56` classifies only `dormant | due | scheduled`, so a broken
upkeep and an unloved one are the same row. `j2.md:703-711` names it as the
fourth state and the plan covers three.

Two consequences:

- The plan sends a creator whose own contract is broken to "share a link
  recruiting a keeper", where the recruited keeper also cannot execute it.
- The plan's two remedies omit the one that works. `j3-j4.md:232-241` states it
  and calls it exact: "cancel and re-register at a higher fee... the contract
  has no method that changes `fee_cap`, `interval`, or `fee`, so cancel and
  re-register is the only path." Cancel returns escrow and the box MBR
  (`contract.py:346-352`). The plan replaces the exact remedy with one the
  criteria never name.

The console can already tell the third case apart: `keeper-txns.ts:322-328` has
the node's own `failureMessage`.

### 1.11 "Blocks what they cannot afford" is the wrong verb, on the wrong number, in the wrong scope

`console-plan.md:90-93`. Three problems.

The number: spendable is `amount - minBalance`, which the service already does
correctly for the app account at `arcron.service.ts:274-280`. A block on raw
`amount` is wrong in both directions, and a user with ASA opt-ins is the common
case.

The verb: a hard block means a failed balance read turns the register form into
a dead page. `arcron.service.ts:252-254` sets `status = 'error'` on any read
failure, against a free shared endpoint with no backoff (2.3). The plan's own
principle two sentences later is that "the site is free to be helpful". Helpful
is a warning naming the shortfall. Blocking is the site being stricter than the
chain, on data it may not have.

The scope: build step 3 confines it to the register form. `j3-j4.md:209-222`
wants the same for top up, where the amount is also unbounded
(`registry-table.ts:154-161`, `min` and no `max`) and the runway it buys is
already computable.

### 1.12 Three smaller ones that would each cost a day now and a redesign later

**Clone to MainNet** (`:111-114`) is the one feature that walks a user toward
real money on an app the console cannot identify. `NetworkKey` is
`'localnet' | 'testnet'` (`js/src/networks.ts:11`); `defaultAppId` is optional
(`:27`), so a MainNet entry added before a deployment makes `noticesFor` take
the **soft** branch (`trust-banner.ts:29-38`) rather than the hard one, and an
attacker hand-writing a link picks which warning the victim sees; `isFrozen`
returns `true` when the key is absent (`arcron.service.ts:47-55`, my M7,
unchanged), so a stranger's updatable app reads as immutable. Say explicitly
that the control is dark until MainNet exists and `defaultAppId` is required.

**"Mine means the connected wallet"** (`:108-109`) makes Manage the one
destination that blocks, contradicting the NFD rule the plan chose to copy at
`:47-49` ("everything is readable without connecting"). It also contradicts a
criterion directly: `j3-j4.md:143-147` has a Given of "the creator... has no
wallet connected" and a Then of "the upkeeps registered by that address are
listed", and D4 (`j3-j4.md:667`) recommends the opposite of the plan. `creator`
is already on the type (`js/src/upkeep.ts:38`) and already decoded (`:133`), so
read-by-address costs nothing. Only the buttons need the wallet.

**"Two rates, both measured, both live"** (`:95`) would delete a regression
guard. On LocalNet the rate is never measured: `arcron.service.ts:237` skips
`sampleRate` in dev mode, so `paceSource` returns `nominal`
(`arcron.service.ts:99-101`). `j2.md:165-169` carries an explicit `# Preserve:`
on the basis word, because "a confident 'every 28 s' would be a lie there". The
decision is good; the word "measured" in it is not always true.

---

## 2. What is missing

### 2.1 There is no verification step, in a plan for the surface nobody has ever driven

Largest omission, and 0.3 is the proof it matters. "Test" appears nine times in
`console-plan.md` and every one is the Test button.

State at HEAD: no `TestBed` and no `ComponentFixture` anywhere in `web/src`
(grepped); `bun test` in `web/` is 32 tests over four files and none constructs
a component; no AXE in any lane; no Playwright; `web/scripts/dev.ts:8-9` and
`web/scripts/wallet-kmd-e2e.ts:22-25` both still import
`../src/app/core/keeper-abi` and `../src/app/core/keeper-txns`, which moved to
`js/src/`, so both fail on run and no lane runs either. And
`frozen.test.ts:43-49` tests a private copy of the guard it exists to protect.

The plan then goes from two tabs to five destinations plus a detail page, which
multiplies every place a guard has to be repeated. The write guard is currently
centralized correctly (`keeper.service.ts:120-129`) and that centralization is
the thing most likely to be quietly reintroduced per page when routing arrives.

The insurance is small and specific: one `TestBed` harness with a stubbed
`ArcronService`, one test asserting `TrustBanner` renders the identity notice
while `status` is `'error'`, one asserting a single undecodable box drops one
row rather than failing the read, one asserting `KeeperService` refuses a write
when `canWrite` is false. All three of those fixes can currently be reverted
with a green suite.

### 2.2 Focus management and AXE, which this repository's own conventions make mandatory

`CLAUDE.md` says "It MUST pass all AXE checks" and "It MUST follow all WCAG AA
minimums, including focus management". A router with five destinations, a
persistent sidebar and a global search is exactly where that gets hand-placed
per page: focus moving on navigation, the route change announced, the skip link
(`app.html:1`) still landing somewhere real, the sidebar's current item exposed.
No lane runs AXE (`axe.min.js` is gitignored; `web/src/production-build.test.ts`
asserts only that the *configuration* excludes it).

I also could not check, and flagged last pass, whether the register form is
usable on a phone: `register-form.ts:157` is
`repeat(auto-fit, minmax(12.5rem, 1fr))` and `registry-table.ts:209` gets
`overflow-x: auto` on nine columns, so a 375px screen scrolls sideways through
every money button. The sidebar is a sixth layout problem the plan does not
name.

### 2.3 The information architecture is borrowed from a product with a backend

The deepest structural gap, and the plan does not see it because it took the
shape from screenshots and the data layer from a separate decision.

NFD's marketplace has search over roughly a hundred thousand names because there
is an API behind it. Arcron has decided, deliberately and I think correctly, to
have no indexer and no backend (`console-plan.md:133-138`). `readUpkeeps`
(`arcron.service.ts:287-312`) issues one `getApplicationBoxByName` per box
(`:299`) in a bare `Promise.all`, every `POLL_INTERVAL_MS = 2_500` (`:18`),
against `https://testnet-api.algonode.cloud` (`js/src/networks.ts:56`), a free
shared endpoint, with no backoff, no cache, no pause on a hidden tab and no cap.
That is `(5 + N) * 24` requests per minute per open tab, and `j3-j4.md:274-284`
already writes the fifty-upkeep case as a criterion.

Global search and filter chips over "everything on the network" is a promise
about a dataset the console fetches item by item. At five upkeeps it is fine and
unnecessary. At the scale that would justify the IA it is the failure mode of
the primary surface under its own success. Say which of the two the design is
for, and bound the read path before adding a fifth reader of it.

### 2.4 "Was that hour worth it" was cut with the leaderboard, and it should not have been

`console-plan.md:133-137` cuts "a leaderboard, per-keeper history and timing
distributions" together, on the grounds that "the chain records that an upkeep
ran and not who ran it".

That reason is true for a *global* leaderboard, and false for *your own*
earnings: the browser sent those transactions. The console already has every
number and throws it away (`keeper.service.ts:135`, an in-memory list capped at
8, lost on reload). `j3-j4.md:603-607` names the fix and it needs no backend: a
`localStorage` ledger keyed by address and app id. `docs/journeys.md` makes it a
J4 completion criterion ("they can tell whether keeping here is worth it after
an hour").

One correct conclusion generalized to a sibling where it does not hold. The same
shape as everything else in this repository.

### 2.5 The register form's own defaults produce an upkeep that dies before the creator comes back

`register-form.ts:192-200`: interval 10 rounds, fee 0.004 ALGO, funding 0.012
ALGO. That is three executions, about ninety minutes at TestNet pace, on a
network whose only keeper is a half-hourly cron
(`.github/workflows/keeper-bot.yml:37`). `j2.md:679-687` writes it as a
criterion. The plan fixes how servicing cadence is *reported* (`:95-102`, build
step 4) and never revisits the defaults that make a first registration go
dormant. J2's whole point is that they watch it execute; the defaults let them
watch it die.

### 2.6 The ASA half is registrable, not operable, and the plan hides it further

`register-form.ts:67-79,190-191` accept `feeAsset` and `assetFee`.
`KeeperService.optInAsset` (`keeper.service.ts:52`) and `topUpAsset` (`:62`)
exist and are correctly wired. **No component calls either**, and `assetBalance`
appears in no component (grepped). So a creator can register an upkeep
advertising a bonus, then cannot opt the app in, cannot fund it, and cannot see
it is zero. On chain it runs forever paying no bonus (`contract.py:435-441`
short-circuits) and the console reports nothing.

Progressive disclosure (`:42-45`) will put those two fields behind a fold, which
reduces how often it happens and makes it harder to notice when it does. Build
the two controls or remove the two fields.

There is also a cost line the plan's three-item breakdown omits and the form
already mentions: `register-form.ts:288-290` tells the user the opt-in costs 0.1
ALGO, which is not part of the register group and has no button. And the "box
deposit" line is not a constant: it is `58,100 + 400 x argument bytes`
(`contract.py:68`, `js/src/upkeep.ts:88-90`), 62,100 at the form's defaults.

### 2.7 Smaller ones, each confirmed

- **Nothing on what happens to an upkeep page after cancel.** The plan makes the
  upkeep page the destination for registration, and cancel deletes the box. The
  central new route deletes itself and nothing says what `/upkeep/12` renders
  afterwards.
- **Receipts.** `keeper.service.ts:135` caps activity at 8 in memory, and
  `activity-log.ts:30` prints the txid as plain text with no link. For the
  surface someone registered a year of scheduled payments through there is no
  record it happened.
- **A target app that has been deleted** (`j3-j4.md:260-272`) is absent from the
  plan entirely. Nothing reads the target app at all: the only
  `getApplicationByID` calls are `arcron.service.ts:263` and
  `keeper-txns.ts:89`, both on the keeper.
- **The keeper board overquotes cost to every viewer.** `js/src/board.ts:65-67`
  adds the 1,000 microALGO ASA transfer fee whenever `feeAsset > 0n`, with no
  opt-in test, while `keeper-txns.ts:225` does test it. Net reward is understated
  for exactly the upkeeps paying extra.
- **The activity log reports the wrong fee.** `keeper.service.ts:86` prints
  `upkeep.feePerExecution` where `contract.py:400` charges the escalated fee.
- **Funding has no upper bound and no safe-integer check**
  (`register-form.ts:200,387`), handed to algosdk as `Math.round(funding * 1e6)`.
- **Two rates aside**: median lateness (`js/src/board.ts:106-110`) over five
  upkeeps serviced by our own cron is a measurement of our cron schedule,
  published as a property of the network. The number is honest; its provenance is
  not, and without an indexer the console cannot know how many distinct keepers
  it observed, which is itself the answer.
- **Wallet account switch mid-flow.** `wallet.service.ts:118-123` hands out
  `activeAddress` and `transactionSigner` and nothing re-checks. Once the plan
  gates on balance and defines "mine" by connection, switching accounts in Pera
  after validation makes both stale.
- **J3's "they learn it from the console rather than from the chain going
  quiet"** (`docs/journeys.md:132`) needs push, which no-backend forbids.
  Unreconciled either way.
- **`stat-tiles.ts:74-75`**: `escrowed` and `escrowedExact` are byte-identical
  expressions, so the tile prints the same value twice. Fourth pass, still there,
  and it occupies the slot where J5's provenance label belongs.

---

## 3. What is over-built, and were the three cuts right

### The keeper leaderboard: right cut, right reason, over-applied

Confirmed rather than assumed. The `Upkeep` struct (`js/src/upkeep.ts:36-56`)
has `timesExecuted` and `lastServicedRound` and no executor field; the payment
to the keeper is an inner transaction (`contract.py:482`). The box records that
an upkeep ran and not who ran it, exactly as the plan says. Without an indexer a
leaderboard is not deferred, it is impossible.

But the cut swept up "was that hour worth it", which needs no backend and is a
J4 completion criterion. See 2.4. Cut the leaderboard, keep the ledger.

### In-console self-verification: half right, and the half that was cut is the half that works

You asked for my view specifically. Cutting the console's claims *about itself*
is correct and I would have argued for it: a green "verified" badge is a lie an
attacker can copy in ten seconds. The distinction I would hold:

- **Theatre, cut it.** Any badge, seal, "you are safe" message, or claim of
  integrity the page makes about itself. A banner that is usually reassuring is a
  banner people stop reading, which `j1-j5.md:610-616` already argues.
- **Not theatre, keep it.** Values the user reads off our page and checks
  somewhere we do not control. The app id and the app account address in full,
  next to an explorer link. The user is about to see both in the wallet prompt,
  and `register-form.ts:33-150` names neither, so the wallet prompt is the first
  place anyone tells them who is receiving their money.
- **Not verification at all, and missing.** The superseded tier, which is how our
  own users tell our former deployment from a stranger's clone.

### The demo target: wrong cut, and the most consequential decision in the plan

The plan makes J2 the first journey to build (`docs/journeys.md:86`), states the
console is the primary way people use Arcron, and then concedes that "a stranger
without a contract can watch Arcron work and cannot try it"
(`console-plan.md:60-61`). The first thing built is a journey the primary
audience cannot finish.

The cut saves close to nothing, because half the demo target is already shipped:

- `register-form.ts:188` defaults the method signature to
  `PULSE_TICK_SIGNATURE`, which is `tick()uint64` (`js/src/keeper-abi.ts:40`),
  the pulse target's hook.
- Pulse is deployed and live on TestNet at `769891902` (`docs/status.md:25`,
  `README.md:14`, `docs/releases.md:143`).
- The missing piece is one optional constant per network, a
  `defaultTargetAppId`, and one line of helper text under field one.

So today the form ships the demo target's *signature* as a default while leaving
the app id blank, which is worse than either choice: it is the half that cannot
work, and it silently teaches a first-time user that the signature field is
already right for whatever they type into field one. Four AC scenarios
(`j2.md:43`, `:55`, `:67-74`, `:129-134`) become permanently unpassable, and
`docs/journeys.md:93` still lists the demo target as a J2 completion criterion
that `docs/journeys.md:204-213` then cuts, so that document contradicts itself
and the contradiction survived the correction pass.

Restore it. It is the cheapest item in the plan and it is the difference between
"watch this work" and "try this".

### What I would cut that the plan keeps

1. **Clone to MainNet.** See 1.12. Not a design cost, a risk.
2. **Analytics as a destination.** Five upkeeps, 23 registrations ever
   (`docs/status.md:26`). A sidebar section for network health at that scale is a
   page whose honest content is two sentences, and those two sentences are what
   J1 requires **on the first screen** (`docs/journeys.md:63`). Splitting them out
   puts the number that makes a stranger believe one click from the front door.
   Fold it into Home; keep the sidebar entry for later.
3. **The attestation checkbox.** See 1.6. A consent control that gates nothing
   trains the dismiss reflex and replaces four disabled controls that are more
   useful than it is.
4. **Global search as chrome, for now.** Keep search; put it in the registry with
   the filter chips until N justifies a global control and until there is a read
   path that can serve it (2.3). NFD's search is global because NFD's dataset is.

---

## 4. The build order

The spine, roughly J1-arrival then J2 then J4 then J3, is defensible. Three
problems, one a real dependency inversion.

**The security decisions come after the surface that multiplies them.** Decision
5 (does a warning disable writes) and Decision 6 (do links pin the app id) are
one predicate and one effect today, in two files, because the write guard is
centralized at `keeper.service.ts:120-129` and the URL is pinned in one effect at
`arcron.service.ts:179-183`. Step 2 introduces routing and step 5 introduces link
sharing. Deciding afterwards means deciding across a dozen sites instead of two.
**This is the dependency the plan has backwards.**

**Step 1 does not deliver what it claims.** See 1.5. It is a default without a
deployment.

**J1's substance is in no step.** Step 1 is called arrival; the things that make
a stranger believe (what an upkeep is in plain words, explorer links, the chain
qualifier on amounts, when anything last ran as elapsed time, a positive
statement of which app this is) are in steps 4, 5 or nowhere. Every visitor takes
J1. Only somebody who already has a deployed contract can take J2, by the plan's
own decision.

One more for step 3: `register`, `topUp`, `cancel`, `optInAsset` and `topUpAsset`
do not simulate at all. Only `execute` does (`keeper-txns.ts:237`). So "expected
outcomes are not errors" (`:116-120`) is a presentation decision on five paths
where there is nothing to present until after the signature. Presentation alone
cannot deliver it.

### The order I would build

0. **One verification step, about half a day.** A `TestBed` harness and the four
   tests in 2.1; fix or delete `web/scripts/dev.ts` and
   `web/scripts/wallet-kmd-e2e.ts`, and put the latter in the `local` lane.
   First, because every later step adds sites where a guard must be repeated, and
   because the pattern recurred this week (0.3).
1. **The front door as a whole.** `defaultNetwork` in `window.__ARCRON__`; a lane
   and workflow running `web-build-hosted`; the URL in `README.md`,
   `web/README.md` and `docs/status.md`. Then a canonical link exists and a
   suspicious one has something to be suspicious against.
2. **Answer Decisions 5 and 6 and implement them.** Whether a bad-toned notice
   gates `canWrite` and how self-hosting survives it; whether a canonical app id
   stays pinned in the URL; the superseded tier in `noticesFor`; ownership-gate or
   explicitly warn Top up (`registry-table.ts:316`). One predicate and one branch
   today.
3. **Router, sidebar, the upkeep page.** Carry the full app account address and
   the explorer links onto it, since it is the page a link points at. Decide what
   `/upkeep/:id` renders after cancel.
4. **Register honesty.** Real cost including the group fees, the variable box MBR
   and the ASA opt-in line; spendable rather than balance; a warning rather than a
   block; the demo target restored; defaults that survive an afternoon; a
   paren-aware signature assembly shown back before signing; the register group
   simulated at click time; the Test button with its claim narrowed.
5. **Run now, the two rates labelled with what they measured, the third
   condition** (broken target) read from `failureMessage`, and a cancel
   confirmation quoting the refund.
6. **Search, filters, mine**, with the read path bounded first: backoff, a
   hidden-tab pause, a concurrency cap. Mine by address, actions by wallet.

Regenerate the acceptance criteria against the shape the plan actually chose,
before step 3. Otherwise the plan still has none.

---

## 5. Rating: 5 / 10

Genuinely good, and I checked each rather than reading the summary:

- It cuts. Three features removed during a planning conversation is rare, and two
  of the three cuts are right in their core.
- The two-rates decision (`:95-102`) is the best thing in it: it identifies a
  conflation nobody else caught, and both numbers are already computable from
  data the console reads.
- "The quoted cost becomes the cost charged" (`:85-88`) is exactly right and the
  gap is real, to the microALGO: `register-form.ts:347-351` is `mbr + funding`,
  62,100 + 12,000 = 0.0741, against a 0.0771 debit because
  `keeper-txns.ts:103,117,128` gives all three transactions their own fee.
- The Test-as-Arcron insight (`:76-79`) is a non-obvious property of the
  contract, correct at `contract.py:463-478`, even though the plan overstates
  what a simulation can reproduce.
- "Expected outcomes are not errors" and the nobody-is-keeping condition are the
  right frame, even though each is missing a case.
- It records reasoning, which is what makes it reviewable, and it corrected
  itself once already after looking at the real product.

What holds it at 5: four of six escalated decisions unanswered, all four the
security ones; the build order omits the plan's own headline; the two features it
adds both amplify the phishing path and neither is paired with a change to the
defence; the architecture reversal orphaned the acceptance criteria and nothing
regenerated them; and there is no verification step in a repository where the
defect you named as dominant recurred this week inside the file written to warn
about it.

The plan is better than its documents and worse than its risks.

### What would raise it

To **7**: answer Decisions 5 and 6 in the plan text; restore the demo target;
move the front door from "one line" to a lane, a workflow and a URL; add step 0;
regenerate the acceptance criteria against the chosen shape.

To **8**: put positive identity back (app id, full app account address, explorer
links, superseded tier) while keeping the self-verification cut; give the build
order a step for the sidebar and the dashboard; extend the cost work to cancel
and top up; keep the local earnings ledger the leaderboard cut swept up.

To **9**: decide what "the money involved is real" means before MainNet; cut
Clone to MainNet until a MainNet deployment exists and `defaultAppId` is
required; bound the read path before the search that depends on it; fix the
register defaults so a first upkeep outlives the afternoon; and name a usability
test with one person who has never seen Arcron, since `docs/status.md:16-18`
already says every interaction this system has ever had was with somebody who
knew how it worked.

---

## 6. The single thing most likely wrong that I did not check

**Whether the shape in the plan survives contact with a browser at all, because
nobody has ever put this console in one.** Unchanged from my last pass, and now
higher stakes, because the plan proposes replacing the layout entirely.

What I would expect to be wrong and could not tell:

- Whether a persistent sidebar, a global search and a wallet control fit on a
  375px screen at once, when the nine-column registry already needs
  `overflow-x: auto` (`registry-table.ts:209`) and the register form's grid is
  `minmax(12.5rem, 1fr)` (`register-form.ts:157`).
- Whether the `aria-live="assertive"` region (`trust-banner.ts:107-110`)
  announces on arrival at all. `appId` resolves synchronously in the field
  initialiser (`arcron.service.ts:62-64`), so the identity notice is present in
  the region's first render, and live regions generally announce changes after
  the region exists. If that is right, the anti-phishing control is inert for a
  screen-reader user in the exact case it was built for.
- Whether route changes move and announce focus, a WCAG AA requirement this
  repository's own conventions declare mandatory and which nothing in the plan or
  the lanes covers.
- Whether the uncontrolled top-up input (`registry-table.ts:159`,
  `[value]="defaultTopUp(row)"` with no form control) is overwritten by the
  2.5 second poll while somebody is typing an amount into it.

**Second most likely:** that the acceptance criteria contain more false lines
than the six confirmed here. About forty of the seventy-odd `# Today:` claims
were spot-checked and five were wrong on substance; dozens more carry line
numbers that moved. The plan was sized from this document, and it was drafted
against an architecture the plan then reversed.

**Also not checked this pass:** anything on a live chain; the contracts beyond
the four payer-binding sites and `execute`'s inner call; the npm supply chain;
whether `web-build-hosted` produces a working bundle at all.

---

## 7. Addendum: the plan changed while I was reviewing it

I rated `docs/console-plan.md` as committed at `5edea87`. The working tree has
since grown 77 lines, "What driving it in a browser settled (2026-08-26)",
recording a Chrome session against live TestNet. Read after the fact. What it
changes:

**It answers part of my section 6, and confirms two findings.** The console has
now been opened. `stat-tiles.ts:74-75` printing the same value twice is
confirmed visually, which makes it five passes. And the two-rates provenance
problem I raised in 2.7 turns out to be worse than provenance: the addendum
finds that **alpha-2 has never been serviced by a keeper at all**, because the
Actions cron skips clean on an empty `KEEPER_MNEMONIC` and the running local bot
points at `769823086`, an app `docs/status.md` lists among the three that "must
not be used". So the median-lateness figure is not a measurement of our own cron
either. It is an artefact of a misconfiguration, and any "keepers are running
about N behind" sentence built on it would be a confident lie. My 1.12 objection
to the word "measured" stands and gets sharper: on the live network the number
is not merely unmeasured, it is meaningless.

**It supplies a cause for 2.1.** `bun run ng serve` builds and serves an empty
body, with `SyntaxError: Unexpected token 'export'` from `js/src/networks.ts`,
because Vite serves the workspace TypeScript untranspiled; `ng build` is fine,
so CI never sees it. That is why nobody has driven this console: the documented
command does not work and fails in a way that looks like a rendering bug. It
raises the priority of my step 0 rather than lowering it, and it is a sixth
instance of the pattern, since the `ci` lane's `web-build` passes over exactly
the path `web/README.md:11-16` tells a newcomer to use.

**It revises the IA in the direction of my over-built item 2**, treating "five
NFD destinations" as unbudgeted until something needs a fourth reading. That is
the right correction and it goes further than I did.

**What it does not touch.** None of my ranked items 1.1 through 1.12 is
addressed: the six escalated decisions are still five-sixths unanswered, links
still pin the app id and still poison `localStorage`, the hostile-app warning
still gates nothing, Top up is still ungated by ownership, cancel still has no
confirmation, the attestation checkbox still replaces four disabled controls,
the Test button's faithfulness is still overstated, the demo target is still
cut, and there is still no verification step and no test in any of it. The
addendum is an observation log, which is exactly what the plan needed and is not
a substitute for the decisions.

**The rating is unchanged at 5.** The browser session removes my single largest
caveat about the review and adds nothing that moves the number, because what it
found is failures rather than reassurance. If the addendum's findings are folded
back into the Decisions and the Build order rather than appended after them, that
alone is part of the path to 7.
