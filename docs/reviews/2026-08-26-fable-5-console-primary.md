# Arcron console, re-reviewed at the primary-money-surface bar

| | |
|---|---|
| **Target** | `main` @ `958d31f`, scope `web/` and `js/` |
| **Date** | 2026-08-26 |
| **Prior passes** | `2026-08-25-fable-5.md` (3/10), `2026-08-26-fable-5-console.md` (console 5/10), `2026-08-26-fable-5-rescore.md` (5/10) |
| **Method** | Source read at HEAD; seven `use-wallet` adapters driven directly with a crafted group; a live-chain attack run on LocalNet; the JS client's `execute` driven against LocalNet with an instrumented signer; the canonical TestNet app and all five of its boxes decoded with the shipped decoder; `bun test` in both packages (32 + 93 pass) |
| **Not done** | The running console could not be driven interactively (browser extension not connected, same as my last pass). No screen reader. No hostile look-alike deployed to TestNet. |
| **Disclosure** | I deployed one throwaway keeper app (17889) to LocalNet and registered two upkeeps in it, because one finding could only be told apart from a mock artefact on a real AVM. `algokit localnet reset` clears it. No file in the repository was changed. |

**Verdict at the new bar: 4 / 10.** Not a downgrade of the work. The console
scored 5 as a thing people look at and it is genuinely better than it was: every
item on my list to reach 8 is implemented, and I checked each one rather than
taking the claim. Re-measured as the thing people commit funds through, the
number goes down, because the bar moved further than the code did. The single
biggest reason is below and I proved it on a chain.

---

## 0. First, one claim in my brief that the code disproves

> "there is a component test asserting the banner survives an app that fails to decode"

There is not. `web/src/app/components/trust-banner.test.ts` tests `noticesFor`,
an exported pure function, with a literal `status: 'error'` passed in. It never
constructs `TrustBanner`, never touches `ArcronService`, and never exercises
`readUpkeeps`. There is no `TestBed` or `ComponentFixture` anywhere in `web/src`
(grepped). So:

- Nothing tests that `TrustBanner.notices` (`trust-banner.ts:156-165`) wires the
  real signals into `noticesFor`.
- Nothing tests that `<arcron-trust-banner />` is still in `app.html:13`.
- Nothing tests the actual C1 fix, the per-box `try`/`catch` at
  `arcron.service.ts:271-283`, or the `undecodableBoxes` counter it feeds.

The test file's own docstring describes the attack ("a hostile app plants one box
that will not decode, the read throws, status pins at error") and then asserts
against a hand-written `'error'` string. Delete the `try`/`catch` at
`arcron.service.ts:271` and all 32 tests stay green. This is the same shape as
the finding I raised last time about `frozen.test.ts`, one layer up: that one was
fixed correctly (`isFrozen` is exported at `arcron.service.ts:47` and the test
imports it at `frozen.test.ts:12`, so reverting the coercion does fail the
suite), and the same mistake was then made again in the file written to replace it.

---

## 1. What changes when this is the primary money surface

Eight things that were defensible for a console people look at and are not
defensible for one people commit funds through.

### 1.1 There is no front door

`DEFAULT_NETWORK = 'localnet'` (`js/src/networks.ts:64`), and nothing overrides
it for a hosted build. I ran the real resolver:

```
first visit, no query, nothing remembered : {"network":"localnet","appId":null}
?network=testnet, nothing remembered      : {"network":"testnet","appId":769891898}
?app=999999999 (no network named)         : {"network":"localnet","appId":999999999}
bare visit AFTER a poisoned link          : {"network":"testnet","appId":999999999}
```

The only runtime configuration hook is `window.__ARCRON__`
(`web/src/app/core/wallets.ts:26-30`), it carries exactly one field
(`walletConnectProjectId`), and **nothing anywhere sets it**: not `index.html`,
not `angular.json`, not any script, not any doc (grepped `web/`, `docs/`,
`.github/`). `fledge.toml:44` defines `web-build-hosted` with
`--base-href /arcron/console/`, no workflow in `.github/workflows/` runs it, and
no document in the repository names a hosted URL for the console.

As a demo this was cosmetic. As the primary surface it is the product:

- The hosted front page is an empty LocalNet view pointed at
  `http://localhost:4001`, which over HTTPS is mixed-content-blocked, so the
  status reads "node unreachable" (`network-bar.ts:176`) and the registry says
  "Enter a keeper app id to load its registry" (`registry-table.ts:60`).
- Therefore every real user arrives by a link somebody sent them. Links are the
  attack medium, which is the entire premise of the trust banner. The console's
  own default makes the attack the normal path.
- There is no canonical URL for a user to have bookmarked, or to compare a
  suspicious link against, or for the docs to point at.
- `?app=<evil>` with no `network=` lands on LocalNet, where `defaultAppId` is
  undefined, so the victim gets the softer "No published app is recorded for
  LocalNet" notice (`trust-banner.ts:30-38`) instead of the hard "anything you
  escrow here goes to whoever deployed it" one. An attacker who writes the link
  by hand picks which warning the victim sees.

### 1.2 The console gives affirmative identity nowhere, only absence of warning

When the app id **is** canonical, `noticesFor` returns `[]`
(`trust-banner.ts:22-50`). That is correct as a warning policy and wrong as a
trust policy: the user is about to approve a wallet prompt showing an app id and
an app account address, and the page gives them nothing to compare it to. What
is missing, all of it cheap:

- `explorerApp` is defined at `js/src/networks.ts:59` and **referenced nowhere**
  in `web/src` or `js/src` (grepped). There is no link out to a neutral third
  party for the app, the app account, the target app or a txid.
- The app account appears in exactly one place, the footer (`app.html:47-49`),
  through `shortAddress` (`js/src/format.ts:26-28`), which shows 6 leading and 4
  trailing base32 characters of a 58 character address. There is no full address
  anywhere and no copy control, so the one cross-check a careful user could
  perform is not available from the page.
- The register panel never names the app id, the app account, or the target
  app's identity at the moment of committing (`register-form.ts:33-150`).
- Nothing in the console shows the build hash `scripts/verify_build.py`
  authenticates, or a verification date. I grepped `web/src` and `js/src` for
  `sha256`, `hash` and `verify`: nothing, unchanged since my last pass.

Absence of a red banner is being asked to carry the whole weight of "this is
the real Arcron", and it trains exactly the reflex that fails in 1.3 and 2.

### 1.3 "Check the app id" is no longer a sufficient rule, and the console still teaches it

See M1 below. `register` is payer-bound now; `top_up` is not. The banner's text
("anything you escrow **here** goes to whoever deployed it") implies that a
correct app id makes you safe. For `top_up` that implication is false, and I
proved it on a chain.

### 1.4 There is no review step between the click and the signature

`register-form.ts:377-393` goes from `submit()` straight to
`KeeperService.register` to `txns.register` to `composer.execute()`, which is
the wallet prompt. No confirmation, no summary, no last look. Same for Cancel
(`registry-table.ts:347-349`, one click, no dialog) and Top up
(`registry-table.ts:351-360`).

And the one number the panel does quote is short. The "Up-front cost" tile
(`register-form.ts:347-351`) is `boxMbr + funding` and omits the group's own
fees. Measured with the shipped code at the form's own defaults:

```
register-form default: quoted up-front = 0.0741 ALGO
actual debit          = 0.0771 ALGO   (3 txns x 1000 microALGO, never shown)
```

0.003 ALGO does not matter. A cost panel on a money surface that is not the cost
does.

### 1.5 The console never looks at the user's own account

`arcron.service.ts` calls `accountInformation` only for the app address
(`:246`). The connected account's balance is never read, never shown, and never
checked against what the form is about to spend. `funding`
(`register-form.ts:200`) has a minimum and no maximum; the top-up field
(`registry-table.ts:154-161`) has `min="0.000001"` and no maximum. A user who
enters a number they cannot afford gets a wallet prompt and then a raw network
rejection in the activity panel.

### 1.6 The read path does not scale, and it is the same path the guards depend on

`readUpkeeps` (`arcron.service.ts:260-290`) issues one
`getApplicationBoxByName` per box, in a bare `Promise.all`, every
`POLL_INTERVAL_MS = 2_500` (`:18`). Per tab that is `5 + N` requests per 2.5
seconds, so `(5 + N) * 24` per minute, against `https://testnet-api.algonode.cloud`
(`networks.ts:56`), a free shared endpoint, with no backoff, no caching, no
pause on hidden tab, and no cap. The registry is at 5 boxes today
(`next_upkeep_id = 23`, verified live). At 50 upkeeps it is 1,320 requests per
minute per open tab.

This degrades safely rather than dangerously (a 429 sets `status = 'error'`,
which disables the register form and three of the registry buttons and raises
the "not showing you the current state" notice), but the failure mode of the
primary surface under its own success is that it stops working, and two write
buttons stay live through it (M6).

### 1.7 There is no record of anything

`KeeperService.activity` (`keeper.service.ts:34`) is an in-memory signal capped
at 8 entries (`:119`), lost on reload. No persistence, no export, no link to an
explorer for the txid it prints (`activity-log.ts:30`). For a demo, fine. For
the surface someone registered a year of scheduled payments through, there is no
receipt.

### 1.8 Half the product is registrable from the console and not operable from it

The register form accepts `feeAsset` and `assetFee`
(`register-form.ts:67-79, 190-191`). `KeeperService.optInAsset`
(`keeper.service.ts:52-59`) and `KeeperService.topUpAsset` (`:62-69`) exist and
are correctly wired to `js/src/keeper-txns.ts`. **No component calls either**
(grepped `web/src/app/components` and `app.html`: `feeAsset` appears only inside
`register-form.ts`). The registry table shows neither `feeAsset` nor
`assetBalance`.

So a creator can register an upkeep advertising an ASA bonus and then, from the
console: cannot opt the app in to the asset, cannot fund the bonus, and cannot
see that the bonus is zero. On chain the upkeep still executes (contract.py:425
short-circuits `asset_balance >= bonus` before touching the asset), so it runs
forever silently paying no bonus, and the console reports nothing wrong.

---

## 2. The wallet signing surface: what it actually guarantees

This is the thing I named last time as most likely wrong and did not check. I
have now checked it by execution rather than by reading.

### 2.1 Does any adapter sign by position rather than by matching sender?

**No. Not one.** I built a three transaction group `[mine, theirs, mine]`, gave
each adapter a wallet holding only `mine`, and asked it to sign indexes
`[0, 1, 2]`, calling each adapter's real `processTxns` off its prototype in
`web/node_modules`:

```
pera           returned 3 entries -> 0:SIGN 1:skip 2:SIGN
defly          returned 3 entries -> 0:SIGN 1:skip 2:SIGN
lute           returned 3 entries -> 0:SIGN 1:skip 2:SIGN
exodus         returned 3 entries -> 0:SIGN 1:skip 2:SIGN
kibisis        returned 3 entries -> 0:SIGN 1:skip 2:SIGN
kmd            returned 2 entries -> 0:SIGN 1:SIGN
walletconnect  returned 3 entries -> 0:SIGN 1:skip 2:SIGN
```

Every adapter computes `canSignTxn = this.addresses.includes(txn.sender.toString())`
and requires it **and** `indexesToSign.includes(index)` before offering a
transaction to the wallet. The one place I feared this could turn out worse than
written turns out fine. `use-wallet` 5.0.0, `@txnlab/use-wallet-*` all at 5.0.0.

### 2.2 What it does *not* guarantee, which is narrower than it reads

Three things worth stating precisely, because "matches by sender" is doing less
work than it sounds like:

1. **`this.addresses` is every account the wallet exposes, not the one the
   console shows.** The base wallet's `addresses` getter maps over
   `walletState.accounts`. So the guarantee is "some account this wallet holds",
   not "the account named in the signer bar" (`signer-bar.ts:33`). If a user has
   five accounts in Pera, a group mixing two of them is signed in one prompt and
   the console's displayed identity is not what constrained it.
2. **`transactionSigner` is handed out, not supervised.** `WalletService.signing()`
   (`wallet.service.ts:118-123`) returns `{ sender: manager.activeAddress, signer:
   manager.transactionSigner }` and the transaction layer never inspects the
   group again. That is correct for the canonical console, which composes every
   group itself from its own `appId` (`js/src/keeper-txns.ts:96-145`), and it is
   the reason the canonical console is not the delivery vehicle for M1. It is
   also the reason the console has no ability to refuse a group it did not build.
3. **KMD compacts its return array** (2 entries for 3 indexes, above), and the
   base `transactionSigner` compacts the other six too, by filtering nulls. I
   read `algosdk` 3.7.0's `AtomicTransactionComposer.gatherSignatures`
   (`dist/cjs/composer.js:345`): it assigns `signedTxns[indexes[i]] = sigs[i]`
   and then throws `Missing signatures` unless every slot is non-null. Any
   declined transaction shortens the array, so the last index is always
   `undefined` and it always throws. **Fails closed.** But the error a user would
   see says "Missing signatures", which names nothing useful.

### 2.3 WalletConnect is dead code

`publicWallets()` adds it only when `window.__ARCRON__.walletConnectProjectId` is
set (`wallets.ts:41-44`). Nothing ever sets that (1.1). So the shipped console
offers Pera, Defly, Lute, Exodus, Kibisis, and on LocalNet, KMD. That is
probably fine, but `web/README.md:30-33` describes WalletConnect as available
when configured and there is no configuration path.

### 2.4 The one script that would have tested all of this does not run

`web/scripts/wallet-kmd-e2e.ts` exists precisely to prove the console's wallet
path end to end ("what is exercised is exactly what the browser does, minus the
browser"). Executed at HEAD:

```
error: Cannot find module '../src/app/core/keeper-txns'
       from '/…/web/scripts/wallet-kmd-e2e.ts'
```

Lines 22-25 import `../src/app/core/keeper-txns`, `keeper-abi` and `upkeep`,
all of which moved to `js/src/`. `web/scripts/dev.ts:8-9` is broken the same way
(I flagged `dev.ts` last time; `wallet-kmd-e2e.ts` I did not check and it is the
more important of the two). No lane runs either.

So the answer to "what does the wallet integration guarantee" is: the right
thing, verified by me, and not verified by anything in this repository.

---

## 3. Anything that can lose money, ranked

### M1 — Critical, confirmed on a live chain
**`keeper.top_up` is not bound to its payer. The theft path I proved for
`register` was fixed at one site.**

`register` gained the binding (`smart_contracts/keeper/contract.py:212-214`).
`top_up` (`contract.py:264-284`) checks the funding payment's receiver, rekey,
close and amount, and never its sender. `cancel` pays `Txn.sender`
(`contract.py:342`) and requires `upkeep.creator == Txn.sender` (`:297`). So a
group where the app call is sent by the attacker and the payment is signed by
the victim moves the victim's ALGO into the attacker's upkeep, from which only
the attacker can withdraw it.

Run on LocalNet against a freshly deployed keeper:

```
attacker registered upkeep 0, creator = attacker
  ACCEPTED: top_up returned balance 2016000
  cancel refunded 2078100 microALGO to the attacker
  attacker net over the attack: +2075100 microALGO
  victim  net over the attack: -2001000 microALGO
```

What the victim's wallet shows: a payment to the genuine app account of the
genuine keeper app id, in a group with a `top_up` call on the genuine app.
Every check the project teaches passes, including the one the trust banner
exists to teach.

`scripts/attacks.py:142-170` covers `register` and only `register`. Its own
docstring says a fix without a standing attack behind it is a claim, and names
this exact finding as mine. `top_up_asset` (`contract.py:514-544`) and
`opt_in_asset` (`contract.py:480-510`) are unbound the same way. `opt_in_asset`
is worse in kind: `mbr_payment.amount >= ASSET_OPT_IN_MBR` has no upper bound
and the surplus is credited to no upkeep, so it is stranded in the app account
permanently, recoverable by nobody.

Contracts were out of scope for this pass. I am reporting it anyway because it
is the answer to "what can lose money on the primary surface", and because the
brief told me the payer-binding path was closed. It was closed at one of four
sites.

### M2 — High, confirmed
**The console invites you to fund a stranger's upkeep, from which only they can
withdraw.**

`registry-table.ts:316` sets `canFund: canSign` with no ownership test, so the
"Top up" button is on every row including strangers'. The drawer's hint
(`:166-177`) frames it as a feature: "Anyone can top up an upkeep, not only its
creator. Registered by {{ row.creator }}." It never says the money becomes
that creator's to reclaim. Contrast the same component's `canCancel: canSign && yours`
(`:315`), which does check ownership.

This is a footgun rather than an attack, but it has an obvious funnel: the
console deliberately surfaces starved upkeeps ("Stuck: escrow below one fee...
No keeper can execute these until the creator tops them up. Shown because hiding
the network's failures helps nobody", `upkeep-board.ts:106-110`), and the
registry table puts a Top up button on every one of them. An attacker registers
something that looks like a public good, lets it starve, and waits.

- `web/src/app/components/registry-table.ts:316,166-177`
- `smart_contracts/keeper/contract.py:342`

### M3 — Medium-High, confirmed by execution
**The console asks a wallet to sign a transaction it already knows will fail.**

`discoverResources` (`js/src/keeper-txns.ts:294-319`) simulates and never reads
`simulateResponse.txnGroups[0].failureMessage`. I read algosdk 3.7.0's
`AtomicTransactionComposer.simulate`: it does not inspect it either, so a failed
simulation resolves normally, `unnamedResourcesAccessed` is undefined,
`foldUnnamedResources` returns `known` unchanged, and `execute` proceeds. With
an instrumented signer counting prompts, against LocalNet:

```
calling execute() on an upkeep that is NOT due:
  >>> WALLET PROMPTED (1 txn(s) to sign)
  execute threw: ... status 400 (Bad Request): TransactionPool.Remember: ... logic eval
  wallet prompts before the failure surfaced: 1
  => the simulation failed and the user was still asked to sign.
```

I raised this in the rescore and it is unchanged. It matters more now: on the
keeper board, "another keeper got there first" is the *common* case, not an edge
case, because the round the console holds is up to 2.5 seconds stale and the
board is explicitly designed to make keepers race. The routine outcome of
clicking Execute on a busy board is a wallet prompt followed by a raw AVM string
in the activity panel. That is blind signing, and it is the habit that makes M1
land.

Fix is three lines: read `failureMessage`, throw with it.

- `js/src/keeper-txns.ts:305-318`

### M4 — Medium-High, confirmed
**No front door, so the attack path is the only path.** See 1.1.
`js/src/networks.ts:64`, `web/src/app/core/wallets.ts:26-30`, `fledge.toml:44`.

### M5 — Medium, confirmed
**The generation guard on `refresh()` does not cover three of its writes,
including the one that gates money buttons.**

`arcron.service.ts:203-204` establishes the guard, and `current()` is first
consulted at `:220`. Before that, unguarded:

```
206      const params = await algod.getTransactionParams().do();
207      this.genesisId.set(params.genesisID ?? null);
208      const status = await algod.status().do();
209      this.round.set(status.lastRound);
210      if (this.config().devMode !== true) this.sampleRate(status.lastRound);
```

`algod` was captured at `:195` from the *old* config. So a slow in-flight
refresh, from the previous network or the previous app id, still writes
`genesisId` and `round` after a switch. Consequences:

- `round` drives `isExecutable`, `roundsUntilDue`, `effectiveFee` and the board's
  `classify`, i.e. which Execute buttons are live and what they say they pay.
- `genesisId` drives `genesisMatches`, which drives the page-level red banner at
  `app.ts:50-52` ("Check the endpoint before trusting anything on this page").
  A stale write makes that fire spuriously, or, worse for a user learning what
  it means, makes it fire and then vanish.

The fix I asked for was a generation guard on `refresh()`. It was added, and the
two fields most connected to a money decision were left outside it. Move
`current()` up, or guard each write.

- `web/src/app/core/arcron.service.ts:206-210, 220`

### M6 — Medium, confirmed
**The stale-read guard was applied to one of two views and to three of four
buttons.**

`reads()` (`registry-table.ts:273`) gates Execute (`:123`), the Top up toggle
(`:131`) and Cancel (`:140`). It does not gate:

- **the keeper board's Execute** (`upkeep-board.ts:85-92`,
  `[disabled]="!row.canExecute || keeper.busy() !== null"`). `upkeep-board.ts`
  contains no reference to `arcron.status()` at all.
- **the top-up drawer's submit** (`registry-table.ts:163`,
  `[disabled]="keeper.busy() !== null"`). The button that opens the drawer is
  guarded; the button that spends the money is not.

And `KeeperService.send` (`keeper.service.ts:104-113`) still has no status check,
so there is no central backstop: the guard is per-component and two components
missed it. This is the same one-site pattern as M1, in the console, at HEAD.

### M7 — Medium, confirmed, latent
**A missing `frozen` key still reads as frozen.** `arcron.service.ts:47-55`.
This is a provenance claim about a stranger's app that is true of this project's
deployments and false in general: anyone can deploy an updatable app carrying no
`frozen` key. It is load-bearing exactly where the identity check cannot fire,
which is any network with no `defaultAppId`: LocalNet now, and MainNet for the
window between adding the entry to `js/src/networks.ts` and deploying to it.
`NetworkConfig.defaultAppId` is still optional (`networks.ts:27`). Unchanged
from my last pass. Make it required, or fail closed when absent.

### M8 — Low-Medium, confirmed
**Two app-id parsers, one careful and one not.** `entry.ts:35-41` validates with
`/^\d+$/`, `Number.isSafeInteger` and `> 0`. `network-bar.ts:189-192` does
`Number(value)` with none of that, and the result is written to `localStorage`
by the effect at `arcron.service.ts:151-156`. `1e999` yields `Infinity`;
non-integers pass. Self-correcting on the next load (the stored `"Infinity"`
fails `rememberedAppId`'s regex at `entry.ts:45`), but the header input is the
control the trust banner tells a suspicious user to reach for, so it should be
the strict one.

### M9 — Low, suspected (not observable without a screen reader)
**`aria-live` on the banner probably does nothing in the case it was added for.**

`trust-banner.ts:107` wraps the notices in `<div aria-live="assertive">`, and
`app.html:13` renders the banner unconditionally. `appId` is resolved
synchronously in the `ArcronService` field initialiser (`:62-64`), so on arrival
from a hostile link the identity notice is already present in the first render
of that div. Live regions announce *changes* after the region exists; content
present when the region is first inserted is generally not announced. So the
primary case, the one the control exists for, is the case where the live region
is inert, and the case it does announce is the one where the notice appears
later (a read failing mid-session).

The `<h2>` and `<aside>` half of that fix is right and does work: dropping
`role="note"` restored the implicit `complementary` landmark, and the notice is
now findable by heading and by landmark. Only the announcement half is
questionable. Verifying it needs a real screen reader, which I did not have.

- `web/src/app/components/trust-banner.ts:107-110`

### M10 — Informational, still open from earlier passes
- `stat-tiles.ts:74-75`: `escrowed` and `escrowedExact` are byte-identical
  expressions, so the tile prints the same value twice. Third pass, still there.
- `web/scripts/dev.ts:8-9` and `web/scripts/wallet-kmd-e2e.ts:22-25` both import
  paths that moved to `js/src/`. Both fail on run (executed). Nothing in CI runs
  either.
- No lane runs AXE. `axe.min.js` is gitignored and `production-build.test.ts`
  only asserts the *configuration* excludes it, so "MUST pass all AXE checks"
  remains unverified.
- No Content-Security-Policy and no SRI in `web/src/index.html`. Google Fonts is
  loaded from a CDN (`:22-24`) on the page that composes transactions. A
  stylesheet cannot execute script, so this is defence in depth rather than a
  hole, but a money surface with no CSP at all is a choice worth making
  deliberately.

### Clean, re-verified at HEAD
- Every one of my seven items to reach 8 is genuinely implemented. I checked each
  rather than reading the changelog: identity notice ungated
  (`trust-banner.ts:26-29`), per-box catch (`arcron.service.ts:271-283`),
  `canSubmit` requires ready (`register-form.ts:369`), generation guard exists
  (`:203`, with the gap in M5), `aria-live` plus `<h2>` (`:107-110`), notices
  ranked not exclusive (`:24, 156-165`), "Switch to the published app" control
  (`:112-116`).
- No `innerHTML`, no `bypassSecurityTrust`, no `DomSanitizer` anywhere in
  `web/src` or `js/src`. The only `[src]` bindings are wallet-library icon
  metadata (`signer-bar.ts:16,52`).
- The tail-offset fingerprint is now in the TypeScript decoder
  (`js/src/upkeep.ts:117-122`) and matches `keeper_bot.py`. All five live boxes
  on TestNet app `769891898` decode correctly with the shipped decoder; I ran it.
- The `register` note fix is real: the two payment legs carry distinct notes
  (`js/src/keeper-txns.ts:112-121`), so the byte-identical-txid case I found is
  closed.
- `foldUnnamedResources` and the foreign-reference population are sound; `cancel`
  and `execute` declare the fee asset even on paths that transfer nothing.
- Canonical TestNet app verified live: creator `E5M2OH5X…`, `frozen = 0`,
  `next_upkeep_id = 23`, 5 boxes. The banner's unfrozen warning is telling the
  truth today.

---

## 4. What a first-time user actually experiences

Traced through the code, not observed, since I could not drive the browser.
Assume the repo and docs are public and a stranger arrives.

**They cannot arrive.** There is no hosted URL in any document. `README.md:160-163`
and `web/README.md:11-25` both say `cd web && bun install && bun run ng serve`.
`web/README.md:19-21` then says "It opens on **LocalNet** and needs
`algokit localnet start` plus a deployed keeper app", and points at
`scripts/keeper_e2e.py` to make one. So the documented first-run for the primary
money surface is: install Bun, install Poetry and Python 3.13, install Docker,
`algokit localnet start`, run a Python end-to-end script, read an app id out of
its log, paste it into a number input. That is the path for a contributor. It is
not a path for a user.

**If they do arrive**, at a hosted build with no query string: LocalNet,
`http://localhost:4001`, mixed-content-blocked, status "node unreachable"
(`network-bar.ts:176`), a page-level red `role="alert"` banner with a raw fetch
error (`app.html:9-11`), no trust banner at all (`appId === null` returns `[]`,
`trust-banner.ts:22`), and "Enter a keeper app id to load its registry"
(`registry-table.ts:60`). Every stat tile shows `-` or `0`. There is nothing on
the page telling them TestNet exists except two unlabelled radio buttons in the
header, and no explanation of which one they want.

**If they click TestNet**, `setNetwork` reads the remembered app id and falls
back to `defaultAppId = 769891898` (`entry.ts:44-47`), so they land on the real
registry: 5 upkeeps, real balances, "This deployment is not frozen" in the
banner (honest, correct, well written). This is the first moment the console
works, and it is three clicks past where they gave up.

**Connecting a wallet**: the picker offers five wallets by icon and name
(`signer-bar.ts:44-56`) with the prompt "Reads are permissionless; connect a
wallet to register, execute or cancel upkeeps." Good. If they close the wallet
modal, `isDismissal` (`wallet.service.ts:21-29`) correctly treats it as a
decision rather than an error. Also good.

**Registering**: the form is genuinely the best part of this console. Cross-field
validators turn on-chain rejections into a disabled button with a specific
reason (`register-form.ts:208-224, 312-329`), cadences read as time as well as
rounds (`:293-298`), the runway is priced at the fee ceiling rather than the
headline fee (`:300-309`), and the catch-up/skip-ahead choice is explained in
one line each (`:105-121`). Someone who understands what an upkeep is will get
one right on the first try.

Someone who does not will be stopped by the first field. "Target app id" with no
default, "Method signature" defaulting to `tick()uint64`, "Arguments, one per
line" typed by ABI. There is no example, no link to the pulse demo app, no "try
it against our demo target" affordance, and no explanation anywhere on the page
of what an upkeep is for. The subtitle (`:37-43`) explains the mechanism, not
the use. `README.md` and `docs/` explain it well; the console does not, and the
console is now the product.

**Then they click Register**, and the wallet prompt is the first time anyone
tells them which app account is receiving their money (1.2, 1.4).

**Afterwards**: their upkeep appears with a "yours" badge
(`registry-table.ts:88-90`), which is a nice touch, and one line in an activity
log that will not survive a reload (1.7).

The gap between "the console is beautifully built for someone who already
understands Arcron" and "the console is how strangers will use Arcron" is the
whole of question 4.

---

## 5. Revised score at the new bar: 4 / 10

Not because it got worse. Because the bar moved.

At the "console people look at" bar I said 5 and listed seven fixes to reach 8.
All seven are done and I verified each. On that bar this is now a 7, maybe an 8
if M5's guard gap and the missing component test were closed. The craft is real:
the register form's validators, the ranked notices, the honest error text at
`network-bar.ts:173-177`, the decision to show starved upkeeps rather than hide
them, the `isDismissal` distinction, the comments explaining *why* each guard
exists. I have reviewed a lot of front ends and very few reason this carefully
about their own failure modes.

At the "surface people commit funds through" bar it is a 4, for four reasons in
order of weight:

1. **M1.** A live theft path against the primary write surface, proved on chain,
   in the exact shape I proved four days ago and was told was closed. It was
   closed at one of four sites. This alone caps the score.
2. **The console has no front door and no affirmative identity.** Both the URL a
   user arrives at and the identity they would verify are missing, on a product
   whose central security story is "check which app you are pointed at".
3. **No review step, no cost truth, no record.** The three things every money
   surface has and this one has none of.
4. **The guards are per-component and two components missed them** (M6), the
   generation guard has a hole (M5), and the fix's test does not test the fix
   (section 0). The pattern I named as the dominant risk in this codebase is
   still the dominant risk.

### What takes it to 8 at the new bar

Ordered. The first three are the ones that move the number; the rest close the
gap.

1. **Bind the payer in `top_up`, `top_up_asset` and `opt_in_asset**, the same two
   lines `register` got, and add each as a standing attack in `scripts/attacks.py`
   next to the one that already exists for `register`. Then go and check whether
   every other guard added in response to a review was added at every site that
   needs it, because this is the third time.
2. **Give the console a front door.** A `defaultNetwork` in the same runtime
   config that already carries `walletConnectProjectId`, set to `testnet` for
   `web-build-hosted`; a workflow that publishes it; the URL in `README.md` and
   `docs/`. Then the canonical link exists, and a suspicious link has something
   to be suspicious against.
3. **Make the happy path say what it is.** When the app id matches
   `defaultAppId`, render a positive line: the app id, the full app account
   address, a link through `explorerApp` (already configured, unused), and the
   `verify_build` hash and date. The user should be reading the same three
   values off the console and off the wallet prompt.
4. **A review step before every signature.** One panel naming the app, the app
   account, the target app, the total including group fees, and for a top-up on
   somebody else's upkeep, the sentence "only <creator> can withdraw this."
   Ownership-gate or explicitly warn the top-up button (M2).
5. **Read `failureMessage` in `discoverResources`** and throw with it (M3). Three
   lines, and it turns the commonest interaction on the keeper board from a blind
   signature into a sentence.
6. **Move `current()` above the `genesisId`/`round` writes** (M5), add `reads()`
   to `upkeep-board.ts:88` and `registry-table.ts:163`, and put a
   `status() === 'ready'` check in `KeeperService.send` so there is one backstop
   instead of four hand-placed guards (M6).
7. **One real component test.** `TestBed` with a stubbed `ArcronService`,
   asserting `TrustBanner` renders the identity notice while `status` is
   `'error'`, and one `ArcronService` test that a single undecodable box drops
   one row and raises `undecodableBoxes` rather than failing the read. Right now
   both fixes can be reverted with a green suite.
8. **Fix or delete `web/scripts/wallet-kmd-e2e.ts`**, and put it in the `local`
   lane. It is the only thing that would have answered section 2 without me.
9. **Make `defaultAppId` required** (M7), and either show `assetBalance` and add
   opt-in/top-up controls, or remove `feeAsset` from the register form (1.8).
   Shipping a field whose consequences the console cannot show is worse than not
   shipping it.
10. **Show the connected account's balance** and cap `funding`/top-up against it
    (1.5). Bound the poll: back off on failure, pause on hidden tab, and cap
    concurrent box reads (1.6).

---

## 6. The single thing most likely wrong that I did not check

**Whether the console's guards survive a real browser at all, because nothing in
this repository has ever run it.**

Every conclusion in sections 1, 4 and M5, M6, M9 is traced through source. I
could not drive the page (extension not connected, same as last time), there is
no component test, no `TestBed`, no AXE run, no Playwright, no visual check, and
the two scripts written to exercise the console outside a browser are both
broken and unrun (`web/scripts/dev.ts`, `web/scripts/wallet-kmd-e2e.ts`).
`bun test` in `web/` is 32 tests over four files, none of which constructs a
component. So the entire rendered behaviour of the primary money surface is, at
HEAD, verified by nobody: not by CI, not by a test, not by me.

Concretely, the things I would expect to be wrong and could not tell:

- Whether `aria-live` announces (M9), which decides whether the anti-phishing
  control reaches a screen-reader user at all.
- Whether the uncontrolled top-up input (`registry-table.ts:159`,
  `[value]="defaultTopUp(row)"` with no form control) is overwritten by the
  2.5-second poll while someone is typing into it. I reasoned it is safe because
  `defaultTopUp` reads only `feePerExecution` and `feeCap`, which are immutable
  per upkeep, so the bound expression never changes. That is a chain of
  reasoning about Angular's property-binding dirty check, not an observation, and
  the failure mode is silently replacing a typed amount with a different one just
  before submit.
- Whether the `@for` over notices inside a single `aria-live` div re-announces
  the whole set every 2.5 seconds when `status` flaps.
- Whether the register form is usable at all on a phone. `.grid` is
  `repeat(auto-fit, minmax(12.5rem, 1fr))` (`register-form.ts:157`) and the
  registry table collapses to two columns under 52rem
  (`upkeep-board.ts:199-201`), but the registry table itself only gets
  `overflow-x: auto` (`registry-table.ts:209`), so nine columns on a 375px
  screen is a horizontal scroll containing every money button.

**Second most likely:** whether `register` and `cancel` behave correctly through
a *real* wallet rather than through a raw `algosdk` signer. I proved the adapters
choose the right transactions (2.1), but I drove `execute` and `register` on
LocalNet with a plain keypair signer, not through `WalletManager`. The one script
that would close that gap is item 8 above, and it does not run.

**Also not checked:** contracts beyond the four `keeper` methods M1 touches; the
npm supply chain (`web/node_modules` was taken as given); the `local` and
`endurance` lanes; TestNet behaviour under algonode rate limiting; anything in
`js/src/board.ts` beyond `classify` and `executionCost`.
