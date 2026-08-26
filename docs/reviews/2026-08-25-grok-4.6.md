# Arcron: independent security and readiness review

- **Date:** 2026-08-25
- **Reviewer:** Grok 4.6 (xAI), independent pass
- **Scope:** breadth over depth; contracts, off-chain pieces, documentation
- **Live TestNet app:** keeper `769891898`, pulse `769891902`
- **Branch observed:** `deploy/alpha-2`

None of the findings below require an `Upkeep` struct change.

---

## 1. Overview

Arcron is an on-chain bounty board for scheduled Algorand app calls. A creator escrows ALGO (optionally plus an ASA bonus), stores the call in a box, and any account that shows up after the due round performs that exact call and is paid from that escrow. There is no protocol cut and no required token. That matches the project's description of the keeper.

What the description does not say, and what the live TestNet app actually is: the **app creator can still replace the programs**. Until `freeze()` is called, "no admin over anyone's money" is a promise, not a property. The other seven contracts are example targets, not the network; several of them hold money anyway, and they do not share the keeper's review history.

I did not find a third-party theft path in the keeper's `register` / `execute` / `cancel` logic. I did find ways people lose **their own** deposits in the example contracts, and I found documentation that states the trust model incorrectly.

---

## 2. Security concerns (by severity)

**Can anyone lose money that was not theirs to lose?**

- **Keeper, current bytecode, assuming a frozen app:** I did not find a way for a stranger to take another creator's escrow. A keeper can only take the fee the box computes. A target cannot re-enter the keeper (AVM `attempt to re-enter`). `cancel` is creator-only. ASA bonuses are best-effort so they cannot block the ALGO path.
- **Keeper, today, unfrozen:** yes. The app creator can replace `execute` and drain every box.
- **Treasury / deadman:** depositors can have funds stranded by missing MBR accounting. That is their money, made unclaimable, not stolen into someone else's pocket — unless a co-recipient claims first against an insolvent pot.
- **Rain:** donors put money into a free raffle with no ticket price. A sybil can buy the odds. That is the product, not a hidden backdoor.

### Critical — unfrozen admin key can rewrite payouts

**What breaks:** every escrow in the app.
**Who:** the app creator (`Txn.sender == Global.creator_address` on `update`).
**Cost:** all ALGO and ASA sitting in upkeep boxes.
**Confirmed** in `smart_contracts/keeper/contract.py:118–133` and `136–147`. `frozen` starts at 0 (`:116–117`).

This is documented in `docs/security.md` and the README, and it is the intended alpha window. It is still the actual answer to "is this safe for real money today": no, because the theft function is deployed and enabled.

The reference bot will sign as that same key if `KEEPER_MNEMONIC` is unset (`scripts/keeper_bot.py:426–430`). A compromised hot keeper then *is* the admin key. `deploy/keeper.env.example` tells you to set a separate mnemonic; the code does not require it.

### High — `SECURITY.md` states the opposite of the code

**What breaks:** the trust decision a serious user actually makes.
**Who:** anyone who reads the GitHub security policy and escrows on that basis.
**Confirmed.**

`SECURITY.md:20–25`:

> The contracts cannot be patched in place. They are deployed with no update or delete path.

That is false for app `769891898`. The same file later says the deployer can replace programs while unfrozen (`SECURITY.md:80–86`). Internally contradictory, and the first statement is the one that would make someone comfortable.

`docs/security.md:13–18` is closer to the truth, then overstates it: "There is no owner, no rake, and no admin key over anyone's escrow." The three-party table (`docs/security.md:22–26`) does not mention the app creator at all.

The console never reads `frozen`. It will take escrow against an upgradeable app and not say so (`web/src/app/core/arcron.service.ts` tracks solvency, not freeze).

### High — treasury can become insolvent relative to what it booked

**What breaks:** recipients cannot claim what `owed` says they are owed.
**Who:** anyone who `deposit`s, plus a creator who underpays MBR.
**Cost:** deposited ALGO locked as box MBR, or captured by whoever `claim`s first.
**Confirmed** from the contract; I did not replay it on a chain.

- `deposit` does not require `configured` (`smart_contracts/treasury/contract.py:108–115`). Rain refuses this (`rain/contract.py:217`, `190`).
- `configure` requires a payment to the app and does **not** check the amount (`treasury/contract.py:78–80`). Keeper, rain, embargo, and subscription all check.
- `balance` is increased by the full deposit and is what `distribute` allocates (`:114`, `:130–158`). Box MBR is not subtracted from `balance`.
- There is no delete, no withdraw, no top-up-of-MBR path.

Algorand will allow the box write whenever the account's raw balance covers the new minimum. The booked `balance` can then exceed spendable. First claimants succeed; later ones fail.

The demo papers over this with a 400,000 µALGO pre-fund plus a 200,000 µALGO "MBR" payment (`scripts/treasury_demo.py:54–80`). Unit tests cannot catch it: `algorand-python-testing` does not enforce minimum balance.

This is also the dogfood target. If the TestNet treasury was configured the way the demo is, it is fine until someone deposits into a clone that wasn't.

**Spec drift:** `specs/treasury/treasury.spec.md:20–21` claims nobody can front-run a distribution. `distribute` is permissionless and has no minimum interval (`treasury/contract.py:118–126`). Anyone can split the pot at any time. That is not theft — same shares, more dust remainder events — but the governance sentence is false.

### High — deadman books the full deposit as escrow and cannot pay it out unless the app was pre-funded

**What it actually is (from the code, not the name):** a one-shot, owner-armed ALGO lock. Owner check-in pushes a deadline. After the deadline, **anyone** may `sweep`, which moves `escrow` → `allocated`. Only the beneficiary can `claim`. There is no disarm, no top-up, no change of beneficiary, no owner recovery. That is the security-critical property: silence plus a successful `sweep` is a permanent transfer.

**What breaks:** `claim` of the full `allocated` amount against an app that was not pre-funded for its own 0.1 ALGO MBR.
**Who:** an integrator who copies the contract and not the demo.
**Cost:** the entire escrow, stuck, no delete path.
**Confirmed** in `smart_contracts/deadman/contract.py:91` (`escrow += deposit.amount`) and `:143–144` (pays `allocated` with no reserve). The demo funds 200,000 µALGO first (`scripts/deadman_demo.py:55–60`). The deploy config does not (`smart_contracts/deadman/deploy_config.py`).

Related property, **confirmed**, not a bug if you want "alive means can still sign": `check_in` does not require the deadline to be in the future (`deadman/contract.py:96–104`). After the deadline and before anyone sweeps, the owner can reset. Firing is a race, not a clock. The beneficiary is the party with incentive to win it. A keeper is optional for liveness of the fire.

### Medium — embargo `schedule` is first-writer-wins

**What it actually is:** a public box plus a future round. `publish` after that round emits an event and sets `published_round`. It does not reveal anything; the box is readable from the moment of `schedule`. Security-critical property: cannot publish early (`embargo/contract.py:105`); cannot alter or cancel (`:77`); author's cooperation is not required for `publish`.

**What breaks:** a newly created instance.
**Who:** anyone, before the intended author calls `schedule`.
**Cost:** ~0.006 ALGO to lock junk into the instance forever.
**Confirmed:** no sender check on `schedule` (`embargo/contract.py:66–93`). `author` is overwritten to `Txn.sender` (`:90`) and then never consulted. Spec presents this as "the author commits" (`specs/embargo/embargo.spec.md:18–19`) and claims MBR collected is "exactly" the box cost (`:68`); the code is `>=` (`:86`).

### Medium — rain is a free, permanent raffle; resolution is not unit-tested

**What it actually is:** tickets are boxes that never die; `draw` snapshots count and a future beacon round; `resolve` inner-calls `must_get`; the winner pulls. Security-critical property: the scheduled call cannot bias the winner (it never sees the beacon); `commit_round` is in the future (`rain/contract.py:287`); `abandon` exists so a missed window cannot lock the pot (`:377–403`).

**Confirmed economic shape:** `enter` charges box MBR only (`:182–212`). There is no ticket price. One 0.019 ALGO box enters **every future draw**. Ungated, a sybil buys the pot. Gating is "holds any asset this creator minted" (`:200–204`), so a plentiful token from the same minter is a ticket.

**Confirmed coverage gap:** `tests/test_rain.py:7–9` says mocks record inner calls without executing them, so the beacon decode (`:412–426`, `extract` of 32 bytes at offset 6) is only exercised in `scripts/rain_demo.py` against the stub. I did not re-run that demo. I did not verify the Foundation beacon's output length or grinding resistance. I **suspect** the decode is right for a 32-byte ARC-4 `byte[]`; I **confirmed** they do not check the length prefix.

Creator chooses `beacon_app` once. A malicious beacon is a malicious creator. Users must inspect that global.

### Medium — docs claim keepers discover foreign refs; the JS path does not

**What breaks:** any upkeep whose target touches an account, asset, or app that is not the target itself and not the fee ASA.
**Who:** anyone executing from the console or `js/src/keeper-txns.ts`.
**Cost:** the call fails; no fee taken; the upkeep looks "due and ignored".

**Confirmed:** `js/src/keeper-txns.ts:181–205` attaches the target app and the fee asset, nothing else, and does not simulate. The file even notes that Python algokit-utils fills resources and this client does not (`:163–164`).

README (`README.md:74–75`) and `docs/arcron.md:617` say a keeper simulates and attaches what the node reports. That is true of the Python typed client, not of the published JS client or the console execute button.

`#8` as a struct field was withdrawn. `docs/design/1.0.md:32` still lists "Resource declaration" as in 1.0. Off-chain discovery is the real 1.0. If you ever need an on-chain list, that **would** be a struct change. Flagged only as future cost, not a current bug.

### Medium — integration guide and console copy are on the pre-#8 call shape

**Confirmed.** `docs/integrating.md:20–31` still says the whole call shape is one app arg and no parameters. Later the same file describes three args (`:275+`). README: "Integration is one zero-argument method." Register form subtitle: "The call is a NoOp carrying one app arg, a method selector" (`web/src/app/components/register-form.ts:38–40`) while the form now encodes arguments. People will build the old shape and think arguments are impossible.

### Low — watchdog is a silence flag, not a feed

**What it actually is:** creator names a reporter and a threshold once. Reporter may write any `uint64`. `check_freshness` (permissionless, never fails) sets `stale` if silence exceeds the threshold. Next `update` clears it and records an episode. Security-critical property: **it never looks at the value** (`watchdog/contract.py:8–11`, `:90–109`, `:157–159`). Using `reading()` as an oracle is a misuse the spec is honest about.

**Confirmed gap:** `configure` does not reject a zero reporter (`:77–87`). Deadman, treasury, and subscription all reject the zero address as a stranded-funds trap. A zero reporter can never `update`; the feed can only be flagged. Creator marksman, no money in the contract.

`is_stale` is the last sweep, not live arithmetic. Spec admits this.

### Low — pulse is a permissionless counter

**What it actually is:** anyone may `tick` or `tick_with`. Security-critical property: none. `tick_with` caps the increment at 1,000,000 so a single call cannot wedge every later `tick` on overflow (`pulse/contract.py:46–47`). Spec says "No asserted error paths" (`specs/pulse/pulse.spec.md:67`) which is false.

### Low — spec drift I confirmed (not theft, but the specs are supposed to match)

| Spec claim | Code |
|---|---|
| Keeper global state is `next_upkeep_id` only (`specs/keeper/keeper.spec.md:48`) | also `frozen` (`contract.py:116–117`) |
| `MAX_CALL_ARGS` keeps the program in one 2,048-byte page (`keeper.spec.md:38`) | approval is **2104 bytes, two pages** (measured from `Keeper.arc56.json`) |
| Register MBR is "exactly" the box cost (`keeper.spec.md:65`) | `>=` (`contract.py:196`); overpay is accepted and not refunded |
| Pulse types omit `last_note` | `pulse/contract.py:23` |
| Rain types omit `gate_creator`, `prize_asset` | `rain/contract.py:103–105` |
| `docs/arcron.md:596–605` still talks about 1.0 in the future tense, including resource declaration | alpha-2 is already that surface |

`docs/deploying.md:56–61` is right about two pages. Its sample `govern status` output still shows 1932 bytes and `frozen absent` (`:83–86`), which is the previous generation.

### Low — operational mismatches for MainNet

**Confirmed:** `scripts/deploy.py` refuses to run when a multisig **is** configured (`:54–64`). MainNet-from-multisig cannot use this script; it has to follow the hand-rolled create in `scripts/multisig_e2e.py` (extra pages, schema). Easy to get wrong; extra pages cannot be added later.

**Confirmed:** docs specify 2-of-3 (`docs/security.md:292–298`, `docs/deploying.md:201–205`). The prompt for this review said 3-of-5. Those are different threat models. The contract will accept either; the runbooks will train people on 2-of-3.

**Confirmed:** MainNet requires `ARCRON_ALLOW_MAINNET=1` (`scripts/network.py:58–65`). It does **not** require a multisig. A single-key MainNet create is one forgotten env block away, and the on-chain creator cannot be changed afterwards.

**Confirmed:** JS `networks.ts` has no MainNet entry. The console cannot point at MainNet without a code change. Fine for now; a gap if you ship.

---

## 3. Pros

These are real, and you should keep them.

- **Money movement is pull-shaped everywhere it matters.** Keeper pays `Txn.sender`. Rain/treasury/deadman/subscription allocate, then the interested party claims. That is the correct response to inner-call resource limits and to "one closed account wedges everyone".
- **State before inner transactions on `execute`** (`keeper/contract.py:404–411` then `:418–447`). Combined with the AVM re-entry ban, that is two independent reasons a target cannot drain a window. They measured it (`scripts/spike_reentrancy.py`).
- **ASA path is best-effort, ALGO is not.** Clawback, freeze, and "creator not opted in" cannot strand ALGO escrow (`cancel` `:269–316`, `execute` `:378–400`). They learned this the hard way; the comments are load-bearing and match the code.
- **Escalation cannot farm a backlog.** Replay does not escalate (`:348`). Escrow below the escalated fee falls back to base (`:364–365`). Interval and fee caps make the only multiply overflow-safe without appealing to chain age (`:36–44`, `:358`).
- **Puya positions the two `register` payments at `GroupIndex-2` and `GroupIndex-1`.** I confirmed in TEAL (`Keeper.approval.teal:121–136`). You cannot double-count one payment as both MBR and escrow. Inner fees are explicitly 0 (`:1418–1419`, `:1438–1439`) even where Python omits `fee=0`.
- **Box decoder refuses foreign structs** (`keeper_bot.py:207–216`, tail offset must be 130). Same layout in `js/src/upkeep.ts`. They are pinned to one recorded box. That is how you do not scan a superseded app and invent fees.
- **`verify_build` compares bytecode, not TEAL text.** Freeze is one-way and readable. `govern show` before `sign` prints rekey/close. Falcon addresses are rejected as multisig members with a real curve check (`scripts/multisig.py:46–62`). Those are the right instincts for a key that can rewrite a live contract.
- **Never-fail hooks** on `draw`, `distribute`, `sweep`, `check_freshness`, and a too-soon `charge`. A failing target plus exponential backoff is a killed schedule; they treated that as a solvency-class bug.
- **Subscription's `min_rounds_per_period`** (`subscription/contract.py:127–137`) is the one example that actually authenticates a *schedule*, not just a messenger. They documented why: anyone can register an upkeep.
- **Console solvency tile** (`web/src/app/core/arcron.service.ts:102–105`) is the invariant that matters, on screen.
- **MainNet is not a typo away** (`ARCRON_ALLOW_MAINNET`). Deploy is not in CI. Alpha is named alpha.

---

## 4. Cons

- **The example contracts will be used as products.** Deadman and rain especially. They have no `update`, no `freeze`, no delete. A bug is a new app and a manual migration, and they have not had the keeper's five review rounds. SECURITY.md files them as "illustrations, lower priority". The code and the examples site file them as things you run with money.
- **MBR is a recurring class of bug** and only the keeper really internalized it. Treasury `configure` does not even check the amount. Deadman does not reserve the app's own 0.1. Mocks hide both. The first TestNet keeper already stranded 243,000 µALGO this way.
- **"Scheduled" is mostly a social convention.** `distribute`, `draw`, `sweep`, `publish`, `check_freshness` are all permissionless with no cadence check. Only `subscription.charge` and `embargo.publish` (time lock) enforce anything on-chain. Specs that say "nobody can front-run the moment" are describing the hoped-for keeper, not the contract.
- **Discovery of foreign refs lives in algokit, not in the protocol.** One client does it; the other does not. The next keeper written from `js/` or from raw algosdk will silently not service the interesting upkeeps.
- **Docs are a generation behind in the places users trust first.** `SECURITY.md`, `integrating.md`'s opening, README's "one zero-argument method", `docs/arcron.md` "what 1.0 will be", keeper spec's one-page claim, deploying.md's sample status output.
- **The hot path and the admin path can be the same mnemonic.** Fallback to `DEPLOYER` is convenient for LocalNet and poisonous on anything with value.
- **No outside money, no paid audit, alpha gates unchecked.** `docs/releases.md` beta still has empty checkboxes, including 30 days of continuous TestNet uptime. MainNet is "soon" in the review prompt and "after rc's 60 days of unchanged bytecode" in the repo. Those cannot both be true unless the gates are being skipped.
- **Program is on a second page with ~1992 bytes of mostly empty page.** Fine today. The next feature that is not a struct change still has to fit or you pay another 0.1 forever, and extra pages cannot be added by `update`.

---

## 5. Next steps (ranked)

1. **Do not put MainNet money in an unfrozen app, and do not call it ownerless until it is frozen.** Freeze is the product. The 3-of-5 (or 2-of-3 — pick one and write it down) should create the app, verify bytecode, then freeze before the first outside upkeep.
2. **Rewrite `SECURITY.md` so it cannot be read as "immutable, no admin".** Same pass: console must show `frozen`, and refuse or scream if it is 0. The solvency tile already proves you know how to put an invariant on the dashboard.
3. **If treasury or deadman will hold anything but your own TestNet ALGO, fix MBR in those contracts.** Treasury: require `configured` before `deposit`; require `mbr_payment.amount` to cover the actual box. Deadman: do not book more escrow than spendable, or require a funded app at `arm`. These are not struct changes on the keeper; they are new app ids for those demos, which is cheap while they are still illustrations.
4. **Make the JS execute path simulate and populate resources, or stop claiming that keepers discover refs.** Right now that sentence is true of one binary.
5. **Align the runbooks.** One multisig shape. A MainNet create path that is a first-class command, not a LocalNet e2e script. Bot must not fall back to `DEPLOYER` when `frozen` is 0. `set_keeper(0)` should be rejected.
6. **Fix the specs that are wrong** (`frozen` global, program pages, treasury front-run, pulse errors, embargo MBR "exactly", rain globals). Drift in the specs is how the next review starts from a lie.
7. **Then** run the rc clock you already wrote down. Do not skip it because the contract "feels done".

---

## 6. Confidence: 4 / 10 that this is safe to put real money into today

That number is for "MainNet, other people's ALGO, this week". The keeper logic is better than 4. The deployment state, the docs, and the example contracts pull it down.

**To raise it**

- Frozen MainNet app, creator is a real multisig, `govern status` and `verify_build` published, console shows freeze.
- `SECURITY.md` matches the bytecode.
- Treasury/deadman either fixed or explicitly not offered as places to put value.
- The beta/rc gates in `docs/releases.md` actually ticked, including the soak.
- I would want to run `fledge lanes run local` myself and watch stage 20 hit zero spendable. I did not run it.

**Most likely thing I got wrong that I did not check**

Whether algokit-utils on create already leaves the demo app accounts with enough spare ALGO that the treasury/deadman MBR insolvency is hard to hit in *your* demos — and whether the Foundation beacon's `must_get` log is exactly the 38-byte ARC-4 blob rain extracts. I confirmed the code paths; I did not confirm them against a live beacon or a naked create.

I also did not re-derive every keeper fee-curve case, did not read all 20 e2e stages, did not inspect the live TestNet globals, did not review VPS/hosting, and did not attempt proposer grinding on the beacon.

---

## 7. Would I escrow my own money in this on MainNet today?

**No.**

The function that steals every escrow is deployed and turned on. The security policy a careful user would read first says that function does not exist. The console that would take my transaction does not show the flag. The release document says this stage is alpha and the gates to leave it are empty. I would not be the exception.

After a freeze on a multisig-created MainNet app, with `SECURITY.md` repaired and a few weeks of that frozen bytecode doing the dogfood loop: I would escrow a small amount in **the keeper**, against an immutable target I control, with `fee_cap = 0`. I would not put estate money in deadman, and I would not fund a public rain pot, without the MBR and ticket-price issues closed.

---

## What I looked at, and what I did not

**Looked at from source:** `keeper`, `rain`, `subscription`, `treasury`, `watchdog`, `deadman`, `embargo`, `pulse`; `beacon_stub` and `resource_probe` as supporting material. Off-chain: `scripts/keeper_bot.py`, `scripts/govern.py`, `scripts/multisig.py`, `scripts/deploy.py`, `scripts/network.py`, `scripts/notifier.py` (opening), `js/src/{upkeep,keeper-txns,keeper-abi,networks}.ts`, `web/src/app/core/{arcron.service,keeper.service,entry}.ts`, register form, network bar. Docs: `SECURITY.md`, `docs/security.md`, `docs/arcron.md`, `docs/releases.md`, `docs/deploying.md`, `docs/integrating.md`, `docs/design/1.0.md`, `docs/design/call-shapes.md`, README. Specs for all eight named contracts (spot-checked against code). Selected tests and demos. Keeper approval TEAL around payment positioning and inner-txn fees. Measured approval program size from `Keeper.arc56.json` (2104 bytes, two pages).

**Did not:** run tests or e2e; inspect live TestNet globals; re-do the five keeper adversarial rounds in depth; verify Foundation beacon grinding or log layout on chain; review VPS/hosting; review GitHub workflow permissions in depth; review web accessibility; look at `corvid_vault` (no source in tree).
