# Arcron console — follow-up review of the M1 fix

> A follow-up to [`2026-08-25-fable-5.md`](2026-08-25-fable-5.md), not a fourth
> independent pass. Scope is `web/` and `js/` only: does the trust banner close
> M1, and what else can be done to a user looking at a hostile app id.

| | |
|---|---|
| **Scope** | `web/`, `js/` — the console. Contracts not re-reviewed. |
| **Reviewing** | commit `5ae5489` "Put the trust signal where the money is committed" |
| **Branch / commit** | `deploy/alpha-2` · `0989ac0` (no `web/` or `js/` change since `d719e71`) |
| **Date** | 2026-08-26 |
| **Method** | Source read; real `decodeUpkeep` / `entryFrom` driven with crafted inputs; live algod query of app `769891898`; 6-lens adversarial fan-out with per-finding refutation (34 raised, 9 survived) |
| **Not done** | Interactive drive of the running console (no browser automation available); no hostile look-alike actually deployed to TestNet |

**Verdict — does the trust banner close M1? PARTIAL.** It closes the plain
case and it is well built. It does not close the case where the attacker
spends one box (~0.058 ALGO) to turn it off. Confidence in the console
specifically: **5 / 10**, up from roughly 2.

---

## 1. What the fix closes

Against a clean look-alike the control works. A victim following
`?network=testnet&app=<evil>` now gets a red-bordered notice saying, in
plain words, that anything escrowed there goes to whoever deployed it.
Nothing on the page said that before.

Three judgement calls in it are right:

- **Identity outranks freeze** (`web/src/app/components/trust-banner.ts:57-59`).
  A stranger's contract can report whatever it likes about itself, so there
  is no point reassuring anyone that it claims to be frozen.
- **The `BigInt()` coercion** at `web/src/app/core/arcron.service.ts:200-202`.
  A strict compare between a number `0` and `0n` is `true`, which would have
  reported every unfrozen app as frozen. Caught before it shipped.
- **The placement**, above the signer bar rather than in the docs. The
  disclosure belongs where the money is committed.

## 2. What it does not close

The whole control sits behind one gate:

```ts
// web/src/app/components/trust-banner.ts:55
if (appId === null || this.arcron.status() !== 'ready') return null;
```

`status` reaches `'ready'` only after box reads complete
(`web/src/app/core/arcron.service.ts:179`), and box contents belong to the
attacker. `readUpkeeps` decodes every box inside a bare `Promise.all` with no
per-box `try` (`web/src/app/core/arcron.service.ts:216-229`), and
`decodeUpkeep` throws on attacker bytes two different ways: the length guard
at `js/src/upkeep.ts:105-107`, and unchecked `getUint16` offsets at
`js/src/upkeep.ts:109-116`. Both were run against the real module:

```
-> Out of bounds access      (tail offset 0xFFFF in a 140-byte box)
-> Out of bounds access      (in-range tail, arg offset 60000)
-> a decode that throws rejects the whole Promise.all, not one box   ✓
```

One box, named `u` + 8 bytes so `upkeepIdFromBoxName` accepts it
(`js/src/upkeep.ts:99-101`), costing `BOX_MBR_FIXED` ≈ 0.0581 ALGO
(`js/src/upkeep.ts:19`). Every 2.5 s poll throws, `status` is pinned at
`'error'`, and **the banner never renders once**. What the victim sees:

| | |
|---|---|
| header status | **"node unreachable"** (`web/src/app/components/network-bar.ts:171`) — false, and it blames their connection |
| alert banner | `Out of bounds access` (`web/src/app/app.html:9-11`) — reads as a client bug |
| registry | *"No upkeeps on app 999… yet. **Register one below** to watch the network work."* (`web/src/app/components/registry-table.ts:61-65`) |
| register button | live — `canSubmit` never consults `arcron.status()` (`web/src/app/components/register-form.ts:359-365`), and neither does `KeeperService.send` (`web/src/app/core/keeper.service.ts:104-113`) |

The identity check needs **no chain data at all**: it compares `appId` to
`NETWORKS[network].defaultAppId`, both known before any network call. Gating
it on a chain read buys nothing and costs the entire control.

Two smaller reasons for PARTIAL: `defaultAppId` is `undefined` for LocalNet
(`js/src/networks.ts:44-52`), so the identity check cannot fire there at all;
and the banner protects users of *this* console only — a hosted copy at an
attacker's origin ships whatever banner they compile in. That second one was
always outside the M1 as written, which was specifically the link-parameter
attack against the canonical console.

---

## 3. Findings, ranked

### C1 — High · Confirmed
**A hostile app switches the anti-phishing control off.**
As above. Proved by execution. Fix: `try/catch` per box in `readUpkeeps`, and
move the identity notice above the status gate.
- `web/src/app/components/trust-banner.ts:55`
- `web/src/app/core/arcron.service.ts:213,218,182-185`
- `js/src/upkeep.ts:105-116`

### C2 — Medium-High · Confirmed
**Escrow proceeds while the trust control is known-absent.** Nothing on the
write path reads connection status. Independent of C1 this is also the
transient case: an algonode 429 during the poll removes the warning while the
button stays armed, and the page keeps rendering last-good data because the
catch clears nothing but `status` and `error`.
- `web/src/app/components/register-form.ts:359-365`
- `web/src/app/core/keeper.service.ts:104-113`
- `web/src/app/app.html:39`

### C3 — Medium · Confirmed
**The recovery path can be poisoned (last-writer-wins refresh).** `refresh()`
captures `const appId = this.appId()` at line 163 and writes
`frozen`/`appAccount`/`upkeeps`/`status` unconditionally when it resolves. No
generation counter, no `AbortController`. A suspicious victim who types the
canonical id gets `reset()` plus a new refresh — and the attacker's slow
in-flight refresh lands afterwards, repainting their `frozen = true` and their
fake registry *under the canonical app id*, where the banner shows nothing.
This is the one manual correction the UI offers.
- `web/src/app/core/arcron.service.ts:161-186,142-147`

### C4 — Medium · Confirmed
**An app-caused failure is reported as a network failure.** Every error path
prints "node unreachable". Under C1 the node is perfectly reachable; every
call before `readUpkeeps` succeeded. The label steers the victim away from the
real cause, and the error string that *is* truthful is a raw `RangeError`.
- `web/src/app/components/network-bar.ts:171`
- `web/src/app/app.html:9-11`

### C5 — Medium · Reviewer judgement (adversarial verifiers refuted this one)
**The warning is the one message that is never announced.** `role="note"` is
not a live region, and it also suppresses `<aside>`'s implicit
`complementary` landmark, so the notice is not findable by landmark
navigation either. There is no heading — `<p class="headline">`. And because
`status` starts at `'connecting'` (`web/src/app/core/arcron.service.ts:45`),
the banner is inserted after first paint on **every** load without exception.
Meanwhile `role="alert"` is on the node error (`web/src/app/app.html:10`), the
wallet error (`web/src/app/components/signer-bar.ts:64`) and the activity
error (`web/src/app/components/activity-log.ts:16`). A screen-reader user is
interrupted by "Out of bounds access" and told nothing about "your money goes
to whoever deployed this."

Contrast is fine — `--text-faint` `#50555B` on `#FAF9F6` is 7.2:1. This is
about perceivability, not colour. `aria-live="assertive"` plus an
`<h2 class="sr-only">` fixes it.

The refutation pass rejected this as "not a security defect under the stated
threat model." That is the wrong bar for a control whose entire job is to be
noticed, so it is kept, with the disagreement recorded.
- `web/src/app/components/trust-banner.ts:25`

### C6 — Medium · Confirmed
**The fix's own test does not test the fix.** `isFrozen` is defined *in the
test file* and imports nothing from `arcron.service.ts`. Reverting line 202 to
the strict `!== 0n` compare — the exact bug the commit message calls "bad
enough to pin with its own test" — leaves all 22 tests green. Separately,
`TrustBanner` has no test at all: `web/` has four test files and none touches
a component. The status gate in C1 would have been caught by one.
- `web/src/app/core/frozen.test.ts:10-14`

### C7 — Low-Medium · Confirmed
**The poisoned id persists, and there is no way back.** Proved with the real
resolver: a bare visit with no query string still opens on the attacker's app;
`rememberedAppId` returns the stored value *before* reaching `defaultAppId`; a
LocalNet→TestNet round trip does not clear it; `replaceState` rewrites the
address bar so the victim reshares the poisoned link.

Refusing to persist is the wrong fix — it breaks the self-hoster, who is the
reason "bring your own app id" exists. What is missing is a way *back*:
nothing in the UI offers "return to the published app", and the number is not
shown anywhere. Add that affordance to the banner and persistence stops
mattering.
- `web/src/app/core/arcron.service.ts:118-123,127-130`
- `web/src/app/core/entry.ts:44-47`

### C8 — Low · Confirmed, latent
**"Missing `frozen` means frozen" is a provenance claim about a stranger.**
The reasoning is true of *this project's* apps and false in general: anyone can
deploy an updatable app carrying no `frozen` key. No contract exploits it
today, because on TestNet the identity check outranks it. But it is only
load-bearing exactly where the identity check is absent — LocalNet now, and
any future network added before its `defaultAppId` is set.

**That is the real risk.** When MainNet is added, `networks.ts` will carry a
MainNet entry with no `defaultAppId` for the window between adding the network
and deploying, and in that window both checks fail open on the chain with real
money. Make `defaultAppId` required, or fail closed when it is missing.
- `web/src/app/core/arcron.service.ts:196-202`
- `js/src/networks.ts:44-52`

### C9 — Low · Confirmed
**The activity log states a hostile contract's return values as fact.**
`Refunded 50.4 ALGO (escrow plus box MBR)` — the parenthetical is the
console's own words, explaining a refund the attacker's `cancel` never made.
Same shape for `Escrow now X`. Quote these as *"the contract returned"*, which
is what the panel's subtitle already promises.
- `web/src/app/core/keeper.service.ts:47,76,86`

### C10 — Low · Confirmed by execution
**`register` can build an unsubmittable group.** Both payment legs share
sender, receiver and `suggestedParams`, with no note or lease. With the
default `tick()uint64` signature `boxMbr` is 62,100 µALGO; entering funding
`0.0621` passes every validator and produces two byte-identical transactions:

```
txid A: FW2A5NJNHHHJWPPDWQVYTLEK5A3KS2KQAWPNJTXIEVOD6BSBYTTA
txid B: FW2A5NJNHHHJWPPDWQVYTLEK5A3KS2KQAWPNJTXIEVOD6BSBYTTA
IDENTICAL: true    (still identical after assignGroupID)
```

Not security, but the register form's stated philosophy is "turn a rejected
transaction into a disabled button", and this gets past it. A `lease` or a
note on one leg fixes it.
- `js/src/keeper-txns.ts:102-110`

### C11 — Low · Confirmed
**The hosted front door opens on LocalNet.** `DEFAULT_NETWORK = 'localnet'`
with no hosted override, so a first-time visitor to the hosted console gets
LocalNet, `http://localhost:4001` (mixed-content-blocked over HTTPS), "node
unreachable", and no app id. The console's own front door is empty — which
pushes every real user to arrive by link, and links are the attack medium.
- `js/src/networks.ts:64`
- `fledge.toml:42-44`

### C12 — Informational
- `web/scripts/dev.ts:8-9` imports `../src/app/core/keeper-abi` and
  `keeper-txns`, which moved to `js/src/`. It errors on run and nothing in CI
  runs it.
- `web/src/app/components/stat-tiles.ts:74-75`: `escrowed` and `escrowedExact`
  are identical expressions, so the tile prints the same value twice.
- No lane runs AXE. `axe.min.js` is gitignored and `production-build.test.ts`
  only asserts it is excluded from the bundle, so "MUST pass all AXE checks"
  is unverified in CI.
- `bun run ng serve` did not start on the review machine: the Vite dependency
  optimizer fails to resolve `@corvidlabs/arcron`'s extensionless relative
  imports under Node 26. `ng build` is fine. Possibly environment-specific,
  but it is the documented command.

### Clean
No `innerHTML`, no `bypassSecurityTrust`, no `DomSanitizer` anywhere in
`web/src` or `js/src`. No attacker-controlled data reaches `[src]`, `[href]`
or `[style]`; the only `[src]` is wallet-library icon metadata. Escaping is
not a problem here.

---

## 4. The two questions the fix raised

**Is the ordering between the notices right?** Right for phishing, wrong for
the honest self-hoster. Someone running their own deployment sees the red
identity banner forever and therefore **never** sees the freeze warning for
their own unfrozen app — the exclusion is `if`/`return`, not a priority sort.
Render both; rank them.

**Is the signer bar enough of a backstop?** No. The group is built entirely
from the console's own `appId` (`js/src/keeper-txns.ts:92-133`), so nothing
external can slip a leg in — but what a wallet shows is an app id and an app
account address, and `769891899` is indistinguishable from `769891898` at a
glance. The register panel itself never names the app id or app account it is
about to pay. The console should not lean on the wallet here.

---

## 5. Would I register an upkeep from this console, against canonical TestNet?

**Yes — for TestNet ALGO, with the URL checked before connecting.** Nothing
found lets a third party redirect a correctly-pointed `register`; the group is
built from the console's own app id and the payments go to that app's account.

With one fact verified live rather than assumed: **app `769891898` has
`frozen = 0` right now.** Its creator can replace the programs and reach every
escrow. The banner says so, honestly and in the right place — that part of the
fix is working today. For real money it is still no, for the same reason as
before: it is a bet on a keyholder, not on bytecode.

---

## 6. Confidence in the console: 5 / 10

Up from roughly 2 — before this there was no notion of a canonical deployment
at all. It is 5 rather than 7 because the control that closes M1 is one
attacker-controlled boolean away from silent, it has no test of its own, its
regression test tests a copy of itself, and the write path never asks whether
the read path is telling the truth.

**What would take it to 8**, roughly a day of work, none of it architectural:

1. Ungate the identity notice from `status` — it needs no chain data.
2. `try`/`catch` per box in `readUpkeeps`, so one hostile box drops one row.
3. Make `canSubmit` require `status() === 'ready'`.
4. A generation guard on `refresh()`.
5. `aria-live` and a heading on the banner.
6. A component test asserting the banner survives an app that fails to decode.
7. A "return to the published app" control.

---

## 7. On `verify_build`

`scripts/verify_build.py` was run twice against the live app and passes byte
for byte. Its docstring is already honest — a match proves the deployed
programs are what this tree compiles to *right now*, and nothing more. Two
runs do not change what it proves. Four gaps worth naming:

1. **It is a snapshot on an unfrozen app.** `frozen = 0`, so the creator can
   `update_application` one round after the check and one round before
   someone's `register` lands. Running it twice samples availability; it does
   not establish a property.
2. **It prints the commit, it does not gate on it.** Lines 103-109 report HEAD
   and a dirty-tree flag, then never assert on either. A ✔ from a dirty tree
   looks identical to a ✔ from the reviewed tag. `--no-rebuild` widens that.
3. **It compares approval + clear only.** Not the state schema, extra program
   pages, or — most relevant — the **creator address**. The creator is exactly
   who holds `update` and `freeze`, so verifying bytecode says nothing about
   who can replace it.
4. **Nothing connects it to the console.** It authenticates the canonical
   deployment; M1 is the attack that changes *which* deployment you are
   looking at. The console shows no hash, no verification date, no "this is
   the app the release recorded." Putting that hash in the banner is what
   would make `verify_build` defend a user rather than a reviewer.

Minor: `_spec()` takes `sorted(glob)[0]`, so a second `*.arc56.json` in the
artifacts directory would be silently ignored.

---

## 8. Limits of this review

- The running console could not be driven interactively (no browser
  automation available on the review machine). C1's decoder throw and the
  `Promise.all` rejection are executed proofs; the resulting *screen* is
  traced through `app.html`, `network-bar.ts` and `registry-table.ts` rather
  than observed.
- No hostile look-alike was deployed to TestNet. Each piece of the C1 chain is
  verified; the assembly is not.
- Contracts were not re-reviewed. Findings H1–L3 from the 2026-08-25 pass are
  unchanged except M3 (red CI), which is now green: `bun test` in `web/` runs
  22 tests, 0 failures.
