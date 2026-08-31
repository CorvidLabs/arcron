# Automated CORVID buyback on Arcron — design

Repo state verified against `/Users/leif/Development/_CorvidLabs/_apps/nest` @ `497f79f`, 2026-08-31: `smart_contracts/keeper/contract.py`, `js/src/target-test.ts`, `js/src/keeper-txns.ts`, `js/src/networks.ts`, `scripts/keeper_bot.py`, `docs/integrating.md`, `docs/arcron.md`, `docs/design/out-of-scope.md`.

---

## 0. Straight answer

**The thing you asked for — a scheduled `buyback()` that swaps on Tinyman inside the Arcron-triggered call — is not safe to build, and three of its problems are structural rather than fixable by tuning.**

1. **`execute` is permissionless and constrains nothing about its group.** `smart_contracts/keeper/contract.py:362-371` asserts only `Global.round >= due`. There is no group-size, group-index or group-composition check anywhere in the contract. So whoever calls `execute` *builds the group*, and can submit `[buy CORVID, execute (which swaps), sell CORVID]` as one atomic 5-transaction group. Same block, guaranteed ordering, no front-run risk — and they collect the 7,000 µALGO keeper margin for doing it. Your target cannot detect this: Arcron submits its inner app call alone, so `Global.group_size` inside your contract sees the inner group of 1, not the keeper's outer group. There is no target-side defence. A size cap makes the sandwich unprofitable but the attacker is subsidised by the keeper fee, so they run it at break-even anyway and you systematically execute worse than spot.
2. **An inner app call into a third-party contract cannot fail soft.** There is no try/catch on inner transactions. If Tinyman rejects — `min_output` missed, ABI changed under an `UpdateApplication` (which `docs/deploying.md:105` records Tinyman v2 as able to do), pool frozen, output transfer blocked — the whole group reverts and your target reverts with it. Constraint 4 says the target must never fail; an in-call DEX swap makes "never fails" untrue at exactly one point, permanently, and outside your control.
3. **Nothing about the DEX interface is verifiable from here.** `grep -rniE "tinyman|pactfi|haystack"` over this repo hits only prose in `docs/deploying.md:106` and `docs/security.md:196`. There is no app id, no swap group shape, no measurement of whether Tinyman v2 accepts an application account as swapper, and no measurement of whether resource availability reaches three levels deep (keeper → target → Tinyman's app call → Tinyman's inner transfer back). `docs/arcron.md:624-633` measured two levels. A swap needs three.

**The nearest buildable thing keeps every property you asked for except the venue.** The scheduled call posts a *decaying bid* to global state and moves nothing. Anyone can fill that bid by delivering CORVID straight to a burn address, in their own transaction, and taking ALGO. This is `docs/integrating.md`'s pull pattern, which the guide calls "the most useful technique here", applied to a buyback.

You still get: profits automatically buying CORVID; a zero-argument permissionless `buyback()` registered with Arcron; anyone able to execute the upkeep and earn the fee; the treasury feeding it with no key; everything verifiable on-chain; no server you have to run; and nobody having to remember. What changes is *who executes the DEX trade*: an arbitrageur does, at their own expense, with their own reference budget, routed however they like, and only when your posted price is at or better than market. You are still buying back on a DEX. You are just not the one holding the swap.

The scheduled call under this design needs **zero foreign references**, submits **zero inner transactions**, pays **zero inner fees**, and has **no code path that can revert after the sender check**. The make-or-break question of the brief is answered by making it moot.

Section 12 specifies the AMM module as an optional, separately-deployed add-on, with the seven gates it must clear first. It is not the primary plan and it is not required for the programme to work.

---

## 1. What the mechanism actually is

Once per interval, `buyback()` posts an offer:

> *"I will pay up to **Q** µALGO for CORVID delivered to the burn address, at a price starting **30% below** my running reference and rising linearly to **10% above** it over the next ~18 hours."*

An arbitrageur watching the chain sees the bid cross their cost — buy CORVID on Tinyman/Pact/Haystack/OTC at market, deliver it to the burn address, take the ALGO — and fills it. Competition between fillers means the fill happens at the first moment it is profitable, which is market price plus the smallest margin anyone will work for. Partial fills are allowed, so several fillers can split one offer.

Why this is cheaper than swapping yourself, term by term:

| Cost term | In-call AMM swap | Posted bid |
|---|---|---|
| DEX LP fee (~30 bps) | you pay it | filler pays it, prices it in |
| Price impact | you pay it, on one fixed pool | filler pays it, and can split across venues to minimise it |
| Sandwich by the executing keeper | unbounded, subsidised by the keeper fee | none — the scheduled call moves nothing, so there is nothing to wrap |
| Inner transaction fees | 3,000–5,000 µALGO per call, from your balance | 0 |
| DEX interface risk | permanent revert kills the upkeep | none; you never call a DEX |
| Foreign references | 3–4, unverified, path-dependent | 0, on every path |
| Filler margin | — | **you pay this instead**, bounded by the auction |

The bid strictly dominates on every line except the last. The honest cost of this design is the filler's margin, capped at 10% over the running reference in a monopolised market and expected to be a few tens of bps in a competitive one. See §6.

**The cost of it: you need a counterparty.** So does an AMM — it just always has one. Mitigation is the decay (the bid keeps rising until it is obviously profitable), the reference ratchet (§6.3), and a reference filler script in the repo that anyone including you can run. That script is permissionless and replaceable, exactly like a keeper. It holds no key of yours.

---

## 2. Contract architecture

One application, `Buyback`. Plus one stateless logicsig, `Burn`. **The app never holds CORVID at all** — CORVID goes from filler to burn address directly, and the app only verifies that it did.

### 2.1 Template constants (baked into the program, zero MBR, provably fixed)

| Constant | Type | Notes |
|---|---|---|
| `CORVID_ASSET` | uint64 | the only asset ever accepted |
| `BURN_ADDR` | address | the `Burn` logicsig account, §7 |
| `RECOVERY_ADDR` | address | `recover()` destination, fixed forever |
| `PRICE_SCALE` | uint64 | `2**48`. µALGO per CORVID base unit, fixed-point |
| `STALE_ROUNDS` | uint64 | 2,825,000 (~90 days at 2.752 s) |
| `MIN_SLICE_FLOOR` | uint64 | 10,000,000 µALGO — a hard lower bound `set_params` cannot go under |

### 2.2 Global state — 22 uints, 2 byteslices

| Key | Type | Set by |
|---|---|---|
| `admin` | bytes[32] | create |
| `keeper_addr` | bytes[32] | create; `set_keeper` until freeze. The Arcron **app account address**, compared directly so no app reference is needed |
| `frozen` | uint | one-way, mirrors `Keeper.freeze` |
| `ref_price` | uint | running reference, µALGO per CORVID base unit × 2⁴⁸ |
| `min_slice`, `max_slice`, `spend_bps` | uint ×3 | offer sizing |
| `min_spacing_rounds` | uint | contract-enforced cadence floor |
| `start_discount_bps`, `max_premium_bps`, `decay_rounds`, `offer_ttl`, `ref_step_bps` | uint ×5 | auction shape |
| `offer_start_round`, `offer_remaining`, `offer_start_price`, `offer_end_price` | uint ×4 | the live offer |
| `last_offer_round`, `last_fill_round` | uint ×2 | spacing and the recovery timer |
| `total_algo_spent`, `total_corvid_burned`, `cycles`, `fills`, `skips`, `last_skip` | uint ×6 | the public ledger — this is the product |

MBR: `100,000 + 22×28,500 + 2×50,000 = 827,000 µALGO` added to the **creator's** minimum balance, permanently, plus `100,000` on the app account. No asset opt-in, because the app never touches CORVID. Packing the eight tuning parameters into one byteslice would save 178,000 µALGO; not worth the decode complexity in a contract that must never fail.

### 2.3 Methods, with exact foreign-reference counts

Counted the way `foldUnnamedResources` in `js/src/keeper-txns.ts` counts: the calling app and its own account are free (Arcron already spends a reference on the target app), `Txn.sender` is free, transaction *field* reads cost nothing, and a compile-time address compared as bytes costs nothing.

| Method | Signature | Caller | Extra refs |
|---|---|---|---|
| `buyback` | `buyback()uint64` | Arcron keeper, or anyone after the stale grace | **0** |
| `fill` | `fill(axfer,uint64)uint64` | anyone | **0** (filler carries their own) |
| `quote` | `quote()(uint64,uint64,uint64,uint64)` readonly | free simulation | **0** |
| `stats` | `stats()(uint64,uint64,uint64,uint64,uint64,uint64)` readonly | free simulation | **0** |
| `config` | `config()(uint64,address,address,uint64,...)` readonly | free simulation | **0** |
| `bootstrap_price` | `bootstrap_price(uint64)void` | admin, once, only while `ref_price == 0` | **0** |
| `set_params` | `set_params((uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64))void` | admin, pre-freeze, bounded in code | **0** |
| `set_keeper` | `set_keeper(address)void` | admin, pre-freeze | **0** |
| `recover` | `recover()uint64` | anyone, timelocked | **1** (`RECOVERY_ADDR`) — never called by Arcron |
| `update` / `freeze` | bare / `freeze()void` | admin | **0** |

`buyback()` grades **`none`** on the console's Test button — `gradeReferences(0)`: *"Needs nothing beyond your app… Any keeper can service this."* The count is 0 on every path, so the path-dependence trap (a keeper simulating a quiet contract, attaching nothing, and failing the first day there is money) cannot occur.

`set_params` takes one ARC-4 struct so it stays inside method-argument limits and is bounded in code: `min_slice ≥ MIN_SLICE_FLOOR`, `max_slice ≥ min_slice`, `spend_bps ∈ [100, 10000]`, `min_spacing_rounds ∈ [10, 10⁶]`, `start_discount_bps ∈ [0, 5000]`, `max_premium_bps ∈ [0, 2000]`, `decay_rounds ∈ [100, offer_ttl]`, `ref_step_bps ∈ [10, 2000]`. Out of range is rejected. Note there is **no absolute price ceiling** anywhere — an absolute constant is exactly what kills a buyback on the success case, when CORVID appreciates past it.

**Every one of these ships with a working script in `scripts/` in the same commit, and every one is exercised on LocalNet before TestNet.** `recover`, `freeze` and `set_keeper` are the ones that will rot. The rain `abandon` lesson — a method declared in the ABI with no client is unreachable — applies hardest to the methods nobody wants to test.

### 2.4 `buyback()` — the scheduled hook

No inner transactions. No foreign references. Every failure path is a `return` with a reason code.

```
runs is implicit in cycles + skips

# 0. Authorization, with a survival hatch.
if Txn.sender != keeper_addr:
    if Global.round < last_offer_round + 3 * min_spacing_rounds:
        last_skip = SKIP_UNAUTHORIZED; return 0
    # else: three intervals with no service. The schedule has failed.
    # Anyone may push it, so a dead or redeployed Arcron cannot strand the money.

# 1. Spacing. RETURN, never assert.
if Global.round < last_offer_round + min_spacing_rounds:
    last_skip = SKIP_TOO_SOON; skips += 1; return 0

# 2. Reference must exist.
if ref_price == 0:
    last_skip = SKIP_NO_REFERENCE; skips += 1; return 0

# 3. If the previous offer expired with anything left, the market did not
#    reach our bid. Ratchet the reference up, bounded. This is what stops the
#    programme wedging below market forever, and it is the only place the
#    reference can move without a counterparty paying for it.
if offer_remaining > 0 and Global.round > offer_start_round + offer_ttl:
    step = ref_price * ref_step_bps // 10_000
    if step == 0: step = 1                      # rain lesson, literally
    ref_price = ref_price + step

# 4. Money on hand. Compare before subtracting.
if balance <= min_balance:
    last_skip = SKIP_NO_FUNDS; skips += 1; return 0
spendable = balance - min_balance
size = spendable * spend_bps // 10_000
if size > max_slice: size = max_slice
if size < min_slice:
    last_skip = SKIP_TOO_SMALL; skips += 1; return 0

# 5. Auction bounds.
start = ref_price * (10_000 - start_discount_bps) // 10_000
end   = ref_price * (10_000 + max_premium_bps)   // 10_000
if start == 0 or end <= start:
    last_skip = SKIP_MATH; skips += 1; return 0

# 6. Post. Moves nothing, calls nothing, references nothing.
offer_remaining = size; offer_start_round = Global.round
offer_start_price = start; offer_end_price = end
last_offer_round = Global.round; cycles += 1; last_skip = OK
return size
```

Nine returns, one assert (none after step 0 — and step 0 returns too). The only arithmetic is multiply-then-divide on uint64 quantities bounded by `ref_price × 12000 // 10000`; check `ref_price < 2**53` in `bootstrap_price` and `set_params` so no product can overflow, and the whole method needs no wide math at all. Opcode cost is well under 200 against the 1,135 measured at method entry through Arcron (`docs/arcron.md`).

**Why the authorization is written this way.** Restricting to the keeper address is what stops a would-be sandwicher consuming every spacing window ahead of the paid upkeep, which would leave the upkeep no-opping forever while burning escrow. Leaving it permissionless is what stops Arcron's disappearance — or a redeployment past a `freeze()` that disabled `set_keeper` — from stranding the funds behind a method nobody can call. Arcron has already superseded two deployments (769802474, 769772891). The stale grace gets both properties: while the schedule is healthy, only the keeper can open a cycle; after three missed intervals, anyone can.

### 2.5 `fill()` — the counterparty's transaction

`fill` is **allowed to fail**. It is not the scheduled target; a filler who gets it wrong pays their own fee and nothing else breaks.

Group: `[axfer of CORVID → BURN_ADDR, appcall fill(0, min_price)]`.

```
assert not frozen_flag_unrelated                # no such gate; fill always works
assert axfer.xfer_asset == CORVID_ASSET
assert axfer.asset_receiver == BURN_ADDR        # field read, no reference
assert axfer.asset_close_to == Global.zero_address
assert axfer.rekey_to == Global.zero_address
assert axfer.sender == Txn.sender
assert Txn.rekey_to == Global.zero_address
assert axfer.asset_amount > 0
assert offer_remaining > 0, "No live offer"
assert Global.round <= offer_start_round + offer_ttl, "Offer expired"

elapsed = Global.round - offer_start_round
if elapsed > decay_rounds: elapsed = decay_rounds
price = offer_start_price
       + (offer_end_price - offer_start_price) * elapsed // decay_rounds
assert price >= min_price, "Price below your bound"

pay = BigUInt(axfer.asset_amount) * BigUInt(price) // BigUInt(PRICE_SCALE)
assert pay > 0 and pay <= UInt64.MAX, "Amount out of range"
assert pay <= offer_remaining, "Offer too small — size it from quote()"

itxn.Payment(receiver=Txn.sender, amount=pay, fee=0).submit()

offer_remaining -= pay
total_algo_spent += pay; total_corvid_burned += axfer.asset_amount; fills += 1
last_fill_round = Global.round

# Reference learns from a price somebody actually paid for.
delta = |price - ref_price|
cap = ref_price * ref_step_bps // 10_000
if cap == 0: cap = 1
step = delta if delta < cap else cap
ref_price = ref_price + step  (or - step)

return pay
```

`fee=0` on the inner payment means the filler's group covers it: set the `fill` appcall's fee to 2,000 µALGO. If they do not, their own group fails and it costs us nothing — failure confined to the one claimant, which is the whole point of the pull pattern.

The `pay <= offer_remaining` assert rather than a clamp is deliberate: clamping would take more CORVID than we paid for. `quote()` returns `(price_now, offer_remaining, expiry_round, ref_price)` so a filler can size the axfer exactly. A read-only simulate costs nothing.

BigUInt is required: `asset_amount × price` at 10¹⁵ base units and a scale of 2⁴⁸ overflows uint64 by orders of magnitude. Check the quotient fits before narrowing. This is the rain integer lesson in both directions — a silent zero and a silent overflow.

### 2.6 `recover()` — the one escape hatch

```
assert Global.round > last_fill_round + STALE_ROUNDS, "Not stale"
amount = balance - min_balance
assert amount > 0
itxn.Payment(receiver=RECOVERY_ADDR, amount=amount, fee=Global.min_txn_fee).submit()
offer_remaining = 0            # so a filler cannot be paid from money that left
last_fill_round = Global.round # re-arm, so the next deposit is not swept instantly
return amount
```

Permissionless caller, compile-time destination, `last_fill_round` **initialised to `Global.round` at create**. That initialisation is not optional: a fresh uint64 is 0, and TestNet is past 55M rounds, so an uninitialised timer means the hatch is armed the moment the app exists and a griefer sweeps every deposit before it can ever be bid. Assert the boundary from both sides on LocalNet *and* re-assert it on TestNet, where the round counter is realistic.

CORVID is never recoverable, because the app never holds it.

---

## 3. Arcron registration

| Field | Value | Reasoning |
|---|---|---|
| `target_app` | Buyback app id | |
| `call_args` | `[selector("buyback()uint64")]` — one 4-byte element | Zero-argument. Uses 1 of `MAX_CALL_ARGS = 3`. **Take the selector from the built `.arc56.json`, never retyped** — `register` itself has two plausible selectors (`0x7291d904` vs the real `0x3636cfc6`) depending on how one argument is spelled |
| Encoded `call_args` | 10 bytes | 2 count + 2 offset + 2 length + 4 payload |
| **Box MBR payment** | **62,100 µALGO** | `BOX_MBR_FIXED (2,500 + 400×139 = 58,100) + 400×10`. Refunded in full on `cancel` |
| `interval_rounds` | **32,059** on TestNet, **31,395** on MainNet | 86,400 s at the measured 2.695 s / 2.752 s (`js/src/networks.ts`, measured 2026-08-28 over ~1M rounds). Nominal 2.8 is ~4% slow and compounds to hours |
| `fee_per_execution` | **10,000 µALGO** | Keeper's `EXECUTION_COST_MICROALGO = 3,000`, so it nets 7,000. The `MIN_UPKEEP_FEE` floor of 4,000 leaves 1,000, and this schedule must not go unserviced |
| `policy` | **`SKIP_AHEAD` = 1** | Below. `CATCH_UP` is `0` — the value you get by not deciding. Pass 1 explicitly and read it back from the box |
| `fee_cap` | **0** | With one keeper the ceiling *is* the price and the effective cadence halves (`docs/integrating.md`). It costs nothing here because timing carries no MEV: the scheduled call moves no money, so a keeper sitting on a due upkeep gains nothing by waiting |
| `fee_asset` / `asset_fee` | **0 / 0** | Three reasons. It would pay keepers in the token being retired; the `Keeper` opt-in costs 0.1 ALGO permanently and non-refundably; and — the one nobody notices — `execute` reads `Asset(bonus_asset)` four times when a bonus exists (`contract.py:435-442`), which makes `ARCRON_REFERENCES` 3 and cuts the target's budget from 6 to 5. Adding a bonus later to attract keepers silently eats a reference |
| **Escrow payment** | **4,000,000 µALGO** | 400 executions ≈ 13 months at the daily cadence |

Both payments go to the **keeper application account**, not its creator; on TestNet `769891898` that is `M4YFP33L5VIFRF53X53WUMQWBOWSLYQNBSSAJV2SORGF43L36XBY7OREUA`. Order is not symmetric: MBR first, escrow second.

Box reference: read the app's `next_upkeep_id` global and reference `b"u" + itob(next_upkeep_id)`. Someone registering between your read and your submit makes that reference wrong. Re-read and resubmit; it is a race, not a bug.

### 3.1 `SKIP_AHEAD`, for three reasons

1. **A buyback owes nothing for a missed period.** The ALGO is still in the contract and the next cycle bids with it, up to the cap. `CATCH_UP` is for work where every interval genuinely owes something.
2. **Upkeep 18 is the measured case:** entire escrow spent on 17 replays, schedule advanced 41 rounds against a 23,478-round backlog. Money gone, schedule still broken.
3. **Here, replays would be pure waste twice over.** `min_spacing_rounds` no-ops every replay after the first, so you would pay 10,000 µALGO of escrow per missed interval to write a skip code — and if the spacing guard were absent instead, N offers posted in one block would each reset the decay and hand a filler the last one at whatever price the burst left behind.

`SKIP_AHEAD` also keeps phase (`contract.py:411-416`): a daily upkeep stays on its time of day.

### 3.2 `min_spacing_rounds` must sit below the interval

Set **28,500** against a 32,059-round TestNet interval (0.89×). Equal values plus ordinary keeper lateness plus block-time drift would push roughly half of all executions into `SKIP_TOO_SOON` and you would pay to no-op every other day. Rounds are not a clock.

The spacing gate is also the answer to permissionless registration: anyone can point their own upkeep at `buyback()` at the 10-round minimum and pay for it themselves. The gate makes every such call a free no-op that moves nothing. **It returns, it does not assert** — asserting would fail the whole `execute`, which the keeper records as a target failure and backs off on, which is the exact mistake the integration guide documents at length.

### 3.3 The gate before escrowing anything

Run the console's Test button (`fledge run keeper-ui` → register form → Test; `js/src/target-test.ts::testTarget`) against the deployed contract. Expect grade **`none`**, count **0**. `docs/integrating.md` says to size a hook at four or fewer references if you want any keeper to serve it, and five or six only if you accept some will not. Zero is the only number that is unconditionally safe, and it should be asserted as an exact value in CI — which is only meaningful because the count here is state-independent.

Do not escrow on any other grade.

---

## 4. Funding mechanics

**Said plainly: a plain Algorand account cannot push money automatically. Something with a private key must sign. Any design that claims a keyless treasury threshold is hiding a key.**

### 4.1 Route revenue at source (recommended, and it deletes the problem)

Point the buyback share at the `Buyback` **application account** where the money is created:

- **NFT royalties** — ARC-18 policy address, or the marketplace's royalty recipient field.
- **Shuffle / mint proceeds** — the mint contract's inner payment for the buyback share.
- **App fees** — same.

An application account receives ALGO with no opt-in. There is no treasury→buyback hop, no threshold logic, no key, and no second contract. **The threshold already exists in the contract**: `buyback()` skips while `spendable × spend_bps / 10000 < min_slice`, and the drip rate is `max_slice` per interval. Splitting a lump over time is the cap's job, not a trigger's.

Anyone can also top it up by sending ALGO to the address. Community members who want to add to the buyback simply can, with no method to call and no permission to grant.

### 4.2 If revenue arrives as a lump that must be split

Put the splitter **upstream**, never inside `Buyback` — the credibility of a contract with no outbound ALGO path other than a timelocked recovery is worth more than one saved deployment.

```
sweep() -> uint64            # permissionless, zero-arg
    spendable = balance - min_balance - TREASURY_FLOOR   # compare before subtracting
    if spendable < SWEEP_THRESHOLD: return 0             # return, never assert
    to_buyback = spendable * BUYBACK_BPS // 10_000
    if to_buyback == 0: return 0
    Payment(BUYBACK_APP_ADDRESS, to_buyback, fee=Global.min_txn_fee)
    Payment(OPS_ADDRESS, spendable - to_buyback, fee=Global.min_txn_fee)
```

`BUYBACK_BPS`, `OPS_ADDRESS`, `SWEEP_THRESHOLD`, `TREASURY_FLOOR` are template constants — nobody can redirect the split after deploy. **References: 2** (the buyback app, whose reference confers its account; and the ops account). Grade `servable`. Register it as a second, weekly upkeep: `interval_rounds = 224,400`, fee 10,000, `SKIP_AHEAD`, +0.52 ALGO/yr.

Push is acceptable here only because both destinations are known-good at deploy. If the ops side ever becomes a third party, convert to credit-and-`claim`.

### 4.3 USDC

Keep it out. `Buyback` bids in ALGO only. USDC on Algorand has live freeze *and* clawback addresses, so accepting it as the quote asset imports a failure mode Circle controls. Convert USDC elsewhere and route the ALGO in.

### 4.4 Cost model

| Item | µALGO | Per year (365 daily cycles) |
|---|---|---|
| Upkeep fee, from escrow | 10,000/cycle | **3.65 ALGO** |
| Contract's own inner fees | **0** | **0** |
| Treasury sweep upkeep, weekly (optional) | 10,000/cycle | 0.52 ALGO |
| Sweep inner fees, weekly (optional) | 2,000/cycle | 0.10 ALGO |

One-off: box MBR 62,100 (refundable on `cancel`); creator MBR 827,000 (permanent); app account 100,000 (permanent); `Burn` logicsig 200,000 (permanent, unrecoverable, §7).

**Sizing.** Fixed overhead is 10,000 µALGO per cycle regardless of outcome. For overhead to stay under 10 bps of the money moved, `min_slice ≥ 10 ALGO`. The filler margin dominates above that, and the filler's margin is roughly flat in slice size until the slice is large enough to move the pool — so **10–100 ALGO per cycle is the band**, and precision inside it is not worth engineering.

| Annual buyback budget | Verdict |
|---|---|
| < 400 ALGO | **Do not automate.** 3.65 ALGO/yr is ≥0.9%. Bid by hand, or go weekly |
| 400 – 4,000 | Weekly to daily. 0.09–0.9% |
| 4,000 – 40,000 | **Daily. Slices 11–110 ALGO. The sweet spot** — overhead 0.01–0.09% |
| > 40,000 | Daily with a larger slice, or 6-hourly (14.9 ALGO/yr) |

Anchored on the Discord case — a 1,000 ALGO shuffle every month or two, so roughly 6,000–12,000 ALGO/yr — this is **daily cadence, 16–33 ALGO slices, ~3.65 ALGO/yr all in, about 0.04% of throughput.** The automation is essentially free at that size. What costs money is the filler margin, and the auction is the mechanism for minimising it.

Escrow monitoring: `fledge run health`, `fledge run topup` (prices every upkeep in days of runway), `fledge run keeper-preview` (whether the upkeep is worth serving to a third party). **`keeper_bot --check` exits zero on a starved upkeep by design** — starvation is the creator's problem, not a keeper's — so grep, do not trust the exit code:

```bash
poetry run python -m scripts.keeper_bot --check --network testnet --app-id 769891898 \
  | grep -q starved && echo "top up"
```

Anyone can `top_up`. Only the registering address can `cancel` (`contract.py:298`, asserts `upkeep.creator.native == Txn.sender`) — so the escrow and the 62,100 µALGO of box MBR are recoverable only by that address. Register from an address you will still control in five years, and not from one being retired by the asset housekeeping in §7.

---

## 5. What the keeper cannot supply, and why it does not matter here

Arcron issue #22 is closed permanently (`docs/design/out-of-scope.md`): a keeper supplying data makes every keeper a trusted party. `MAX_CALL_ARGS = 3` fixes the args at registration anyway, so even a "route" baked in would be stale within an hour.

In this design the constraint costs nothing, because **the contract does not need to know a price to post one.** It is a price *maker*, not a price taker. It states what it will pay and lets the market decide whether that is enough. The circularity that traps every in-call swap design — reading reserves to compute a bound, in the same transaction that executes against those reserves, so a manipulated state is simply accepted as the state — does not arise, because there are no reserves to read.

This is also why no aggregator is needed, or possible: routing is the filler's job, done in the filler's transaction, with the filler's eight references.

---

## 6. MEV, slippage and adverse selection

### 6.1 What is eliminated

- **Atomic keeper sandwich.** The scheduled call submits no transactions. There is no payload to wrap.
- **Cross-block front-running of a public schedule.** `next_execution_round` is readable by anyone, but knowing when the offer opens buys nothing: it opens 30% below the reference, deep out of the money, and the first person to find it profitable is the person who fills it — which is what we want.
- **Slippage in the ordinary sense.** We never touch a pool.
- **Denial of service via a price guard.** There is no skip-on-bad-price path to trigger. A manipulated market simply means nobody fills, the offer expires, and the reference ratchets. Nothing wedges.

### 6.2 What remains: adverse selection

Fillers fill when it suits them. Two bounds:

- **Structural cap.** A cycle can never pay more than `ref_price × (1 + max_premium_bps/10000)` = ref × 1.10.
- **Competition.** The bid decays upward, so the first filler willing to work at a given margin takes the whole thing. Undercutting is profitable for the undercutter at any price above their cost, so a competitive field drives the realised margin toward the marginal filler's actual cost (their DEX fee, their impact, their two transaction fees).

**Worst case, honestly:** in a market with exactly one filler, they always wait for the maximum and we pay ~10% over the running reference, with the reference dragging up at most `ref_step_bps` (5%) per fill. That is the number to watch on TestNet and the reason to set `max_premium_bps` at 1,000 rather than 2,000. **Expected case:** market plus a few tens of bps, which is better than the ~30 bps DEX fee alone that an in-call swap pays before impact.

### 6.3 Why the reference cannot be gamed into a bad state

`ref_price` moves in exactly two ways, and both are bounded:

- **Down, or up, on a fill** — by at most `ref_step_bps` toward a price a counterparty actually transacted at. Dragging it *up* requires repeatedly buying CORVID at an inflated price and selling it to us, against a field free to undercut. Dragging it *down* means selling us CORVID cheap, which is a gift.
- **Up on an unfilled expiry** — by at most `ref_step_bps` per cycle. This is what makes the mechanism self-healing when CORVID genuinely rallies faster than 10% in a cycle: the bid was too low, nobody filled, the bid rises. It converges from below and can never wedge.

There is deliberately **no absolute price ceiling**. A static floor or ceiling is what kills a buyback precisely when it succeeds — the token appreciates past the constant and the programme goes permanently, silently quiet, with the only fix disabled by whatever made the contract immutable. The relative ratchet has no such state.

### 6.4 Sizing

`size = min(spendable × spend_bps/10000, max_slice)`, floored at `min_slice`. With no pool to read there is no depth-relative cap, and none is needed: the auction has no price impact for us, because the impact happens on the filler's side and is priced into their bid. Sizing here is pacing and cost-amortisation only (§4.4). A large donation cannot force a market-moving trade — it drips at `spend_bps` per cycle.

---

## 7. Burn versus lock — concretely, what the transaction is

### 7.1 What does not work on Algorand

| Approach | Verdict |
|---|---|
| Transfer to the zero address | Rejected. Not a valid ASA receiver |
| `acfg` asset destroy | Requires the manager to sign *and* every unit to sit in the creator account. It destroys the whole supply or nothing. There is no partial burn opcode |
| "An address with no known key" | Unfalsifiable. You cannot prove a key does not exist |
| A timelock contract | A second contract with a release path and an eventual unlock. That is a lock, not a burn |

### 7.2 What this design does

**A stateless logicsig whose program permits exactly one transaction, ever: its own opt-in to CORVID.** Everything else rejects. The address can receive CORVID forever and can never send it, and anyone can recompile the published program and confirm the address.

```teal
#pragma version 10
// Permit exactly one shape: this account's own zero-amount opt-in to CORVID.
txn TypeEnum;        int axfer;              ==
txn XferAsset;       int TMPL_CORVID;        ==   &&
txn AssetAmount;     int 0;                  ==   &&
txn Sender;          txn AssetReceiver;      ==   &&
txn AssetCloseTo;    global ZeroAddress;     ==   &&
txn CloseRemainderTo;global ZeroAddress;     ==   &&
txn RekeyTo;         global ZeroAddress;     ==   &&
txn Fee;             int 1000;               <=   &&
txn AssetSender;     global ZeroAddress;     ==   &&
```

One-time setup, in this order:

1. Fund the logicsig address with **200,000 µALGO** (100,000 base + 100,000 for one asset holding). This ALGO is permanently unrecoverable — that is the price of the guarantee, and it must be spent before the first offer can be filled.
2. Send the opt-in axfer, signed by the logicsig itself. This is the only transaction that address will ever send.
3. `acfg` on CORVID, signed by the current manager: **reserve = the logicsig address.**
4. `acfg`: **freeze = zero address.**
5. `acfg`: **clawback = zero address.**
6. `acfg`: **manager = zero address.** One-way. Nothing above can ever change again.

Steps 4 and 5 are not cosmetic. A live clawback address means the issuer can retrieve "burned" tokens and the whole programme is theatre — `contract.py` already refuses to trust a clawback-capable ASA in both `cancel` and `execute`, and this should hold itself to the same standard. A live freeze address means a frozen burn holding makes every `fill` axfer reject, which is a filler-side failure rather than a scheduled-call failure, but still stops the programme dead. Step 6 makes steps 3–5 permanent; skipping it means whoever holds the manager key can undo them.

**Consequence of step 3:** every Algorand explorer and indexer computes `circulating = total − reserve balance`. Burned CORVID leaves circulating supply automatically, everywhere, with no announcement and no extra contract powers. `total_corvid_burned` in the app's global state and the reserve balance on-chain must agree to the base unit, and that reconciliation is a checklist item.

**The lock is real if and only if:** the logicsig program is published and independently recompiled to the same address; `Buyback` is not opted into CORVID at all; the compiled `Buyback` TEAL contains no `itxn_field XferAsset` anywhere; and steps 4–6 are done. Verify each, do not believe any.

### 7.3 Why burn rather than lock

The market discounts locked supply toward circulating, because everyone can see it coming back. More importantly, credibility is the product: the whole motivation is removing a future human decision, and a lock reintroduces one. A burn cannot be revoked by a future owner, a compromised key, or a change of heart.

The one honest argument against: bought CORVID could instead be paired as protocol-owned liquidity, deepening the pool and lowering impact for everyone. Better for holders **if** the LP position is trustlessly held. Considerably more complex. **Not for v1.** Revisit after a quarter on MainNet.

### 7.4 The one trust assumption, stated

`Buyback` ships **updatable until `freeze()`**, copied verbatim from `Keeper.update` / `Keeper.freeze`. The rain lesson is that a hub deployed immutably before a fix landed can never take it, and freezing a contract whose auction maths has not run a full cycle on a real chain repeats that mistake.

**So until `freeze()`, the admin can update the contract and take the unspent ALGO.** That is a real trust assumption and it must be disclosed on the page in §9. It does *not* extend to the CORVID, which lives at the logicsig and is out of the app's reach regardless of what the app becomes. This is precisely why the burn is external: it decouples "can this contract be fixed" from "can this token be un-burned".

`freeze()` is a dated decision after the soak, not a launch step.

---

## 8. Security review checklist — things to verify

Every line is something to run, read, or assert. Nothing here is something to believe.

### A — the scheduled call cannot fail

- [ ] A1 Grep the compiled `Buyback` TEAL: `buyback()` contains no `assert` and no `err` reachable after the authorization branch. Account for every occurrence.
- [ ] A2 Each of the eight `SKIP_*` codes is reached by an e2e test on LocalNet, returns 0, and writes `last_skip`. Eight tests, no exceptions.
- [ ] A3 Property test: 10,000 random `(ref_price, balance, min_balance, round, offer state, params)` states. Zero panics.
- [ ] A4 `ref_price` is bounded below `2**53` at every write point, so no product in `buyback()` can overflow uint64.
- [ ] A5 Every ratchet and step uses `if step == 0: step = 1`. Assert the behaviour at `ref_price` values small enough that `ref_price × ref_step_bps // 10000` floors to zero.
- [ ] A6 Every subtraction is preceded by a comparison. `min_balance` is read live from the account, never assumed constant.
- [ ] A7 `Global.min_txn_fee`, never a `1000` literal.
- [ ] A8 `buyback()` submits zero inner transactions. Assert the count in a test, so a later change that adds one is caught before it meets the keeper's flat `EXTRA_FEE_MICROALGO = 2_000`.

### B — references and keeper compatibility

- [ ] B1 `testTarget()` grades `none`, count **exactly 0**, in the trading state and in every skip state. Asserted as a number in CI, the way `scripts/reference_boundary.py` pins six-serviced/seven-refused.
- [ ] B2 A real `execute` through `scripts/keeper_bot.py` succeeds with the bot's flat 2,000 µALGO extra fee and nothing added. Note `populate_app_call_resources=False` at `keeper_bot.py:1180` — nothing recovers a reference the simulate missed.
- [ ] B3 No ASA keeper bonus. If one is ever proposed, re-run B1 first: `execute`'s four `Asset(bonus_asset)` reads make `ARCRON_REFERENCES` 3.
- [ ] B4 `recover()` is never registered as an Arcron target and its 1-reference cost is confirmed irrelevant.

### C — the offer and fill mechanism

- [ ] C1 `fill` rejects: wrong asset, wrong receiver, non-zero `asset_close_to`, non-zero `rekey_to` on either transaction, `axfer.sender != Txn.sender`, expired offer, `pay > offer_remaining`, `pay == 0`, `price < min_price`. Nine negative tests.
- [ ] C2 Partial fills sum exactly to `offer_remaining`, with no rounding leak in either direction across 100 random splits.
- [ ] C3 `pay` uses BigUInt and the narrowing is guarded. Test at `asset_amount = 10**15` and `price` at both ends of its range.
- [ ] C4 The decay is monotone and reaches exactly `offer_end_price` at `offer_start_round + decay_rounds`, and stays flat to `offer_ttl`.
- [ ] C5 An unfilled expiry ratchets `ref_price` by exactly one bounded step, once, not once per subsequent cycle.
- [ ] C6 The reference converges from a 50%-wrong bootstrap within the number of cycles the step size predicts. Simulate 200 cycles against a synthetic price series.
- [ ] C7 A monopolist filler who always waits for `offer_end_price` drags `ref_price` up at no more than `ref_step_bps` per fill. Measure the ceiling and write the number down.

### D — authorization, griefing, stranding

- [ ] D1 A second upkeep registered at the 10-round minimum against `buyback()` produces no-ops, never a revert, and moves nothing.
- [ ] D2 A direct call from a non-keeper address inside the grace returns `SKIP_UNAUTHORIZED` and moves nothing.
- [ ] D3 A direct call after three intervals of silence succeeds. Test the boundary from both sides.
- [ ] D4 `set_keeper` works pre-freeze and is rejected post-freeze, and D3 is re-verified post-freeze — the fallback is the only thing standing between a frozen contract and a redeployed Arcron.
- [ ] D5 `last_fill_round` is initialised to `Global.round` at create. Verify on TestNet, where the round counter is realistic, not only on LocalNet where it starts near zero.
- [ ] D6 `recover()` refuses at `STALE_ROUNDS − 1` and fires at `STALE_ROUNDS`, zeroes `offer_remaining`, and re-arms the timer.
- [ ] D7 The address that registers the upkeep is named, is not the asset manager being zeroed in §7, and its loss consequences (escrow plus 62,100 µALGO unrecoverable, since `cancel` is creator-only) are written down.

### E — the asset and the burn

- [ ] E1 CORVID clawback == zero address. **Blocking.**
- [ ] E2 CORVID freeze == zero address. **Blocking.**
- [ ] E3 CORVID `default_frozen` == false, checked before the logicsig opt-in is attempted.
- [ ] E4 CORVID reserve == the `Burn` logicsig address; circulating supply on at least two explorers reflects it.
- [ ] E5 CORVID manager == zero address, after E1–E4.
- [ ] E6 The logicsig program is published and a third party has recompiled it to the same address independently.
- [ ] E7 The logicsig account has sent exactly one transaction in its life, and holds exactly one asset.
- [ ] E8 `Buyback` is **not** opted into CORVID, and its compiled TEAL contains no `itxn_field XferAsset`.
- [ ] E9 `total_corvid_burned` reconciles to the reserve address's on-chain balance, to the base unit.

### F — registration and operations

- [ ] F1 `policy` read back from the upkeep box == 1. Not assumed from the register call.
- [ ] F2 `min_spacing_rounds` ≤ 0.9 × `interval_rounds`.
- [ ] F3 `fee_cap` == 0.
- [ ] F4 Selector taken from the built `.arc56.json`.
- [ ] F5 Both payments went to the keeper *application account*; MBR first, escrow second; box reference predicted from a fresh `next_upkeep_id`.
- [ ] F6 A starvation grep runs on a schedule. Not `--check`'s exit code.
- [ ] F7 `cancel` exercised end to end on TestNet: escrow and the 62,100 µALGO of box MBR both returned.
- [ ] F8 Every ABI method has a working client script committed in the same change and run in the `local` lane.
- [ ] F9 `last_skip` codes documented publicly, so a quiet contract can be distinguished from a broken one without replaying blocks.
- [ ] F10 The admin's pre-`freeze` power to update the contract and take unspent ALGO is disclosed on the public page.

---

## 9. TestNet plan

### Phase 0 — LocalNet

Nothing here needs a DEX, which is the point.

1. `algokit localnet start`; `fledge run deploy-localnet`.
2. Create `tCORVID` (6 decimals). Compile and fund the `Burn` logicsig; opt it in; set it as `tCORVID`'s reserve; zero freeze and clawback (leave manager live on TestNet so the fixture can be rebuilt).
3. Build and deploy `Buyback`; `bootstrap_price`; `set_params`.
4. Run checklist groups A, C, D in full. These are e2e assertions on a real chain: `algorand-python-testing` mocks record inner calls without executing them and do not enforce minimum balances, so they will not catch B2, C3 or D5.
5. Write `scripts/buyback_fill.py` — the reference filler. It reads `quote()` by simulation, sizes the axfer, and submits the two-transaction group. It is the client for `fill`, and it is also the thing that proves `fill` is reachable.
6. `fledge lanes run local` green, with the reference-count assertion (B1) inside it.

### Phase 1 — TestNet

1. Recreate the `tCORVID` and `Burn` fixtures on TestNet. Disable the suggested-params cache (`set_suggested_params_cache_timeout(0)`); fund the app account's 0.1 ALGO base MBR before anything else.
2. Deploy `Buyback`. Fund it with ~30 ALGO of simulated revenue.
3. **Test button. Grade `none`, count 0. Do not escrow on anything else.** Record the four folded counts (accounts / apps / assets / boxes, all zero) in the repo.
4. Register against `769891898` at a short cadence first — 600 rounds, ~27 min — with 1 ALGO of escrow, `SKIP_AHEAD`, `fee_cap = 0`. Run 30 rapid cycles with the reference filler active on a laptop.
5. Verify: `total_algo_spent` equals the sum of the inner payments; `total_corvid_burned` equals the reserve balance; the reference converges; partial fills reconcile; zero keeper backoff events.
6. **Break it deliberately and watch it not die.** Drain the app to zero (expect `SKIP_NO_FUNDS`, execution still succeeds). Let a whole cycle expire unfilled (expect the ratchet, once). Point a 10-round griefing upkeep at it. Stop the filler for a week and confirm the ratchet is bounded and the schedule is intact. Call `buyback()` directly from a stranger's address (expect `SKIP_UNAUTHORIZED`, then success after three intervals of silence).
7. `fledge run keeper-preview` — confirm a third-party keeper would find the upkeep worth serving. `fledge run health` clean throughout.
8. Cancel, re-register at the production cadence (32,059 rounds), escrow 4 ALGO, and **soak for 30 days minimum**.

### Phase 2 — gates before MainNet is discussed

- [ ] ≥30 consecutive scheduled executions, **zero target reverts**. One revert restarts the clock. Upkeep 87 is 40,000 rounds overdue for exactly this.
- [ ] Every `SKIP_*` code observed at least once in production, each explained.
- [ ] Reference count identical at cycle 1 and cycle 30.
- [ ] Realised filler margin measured and recorded per cycle; `max_premium_bps` set from the measurement.
- [ ] Full pass of §8, groups A–F.
- [ ] `freeze()` executed, and D3/D4 re-verified afterwards.

---

## 10. The MainNet gate

**Arcron is not deployed on MainNet, and the tooling in this repo cannot reach MainNet at all.** `js/src/networks.ts` defines `NetworkKey = 'localnet' | 'testnet'`. `CLAUDE.md` and `docs/design/1.0.md` gate MainNet on self-review plus sustained TestNet time, with any struct change restarting the clock.

Sequencing:

1. `Buyback` runs on TestNet now. That run **is** dogfood evidence for Arcron's own MainNet gate — a third target, built outside the keeper repo, on a schedule carrying real value semantics, and the first one that grades `none`. It is worth doing partly for that.
2. Arcron ships to MainNet when its own gate clears.
3. `Buyback` ships to MainNet after it.

**If a MainNet buyback is wanted before Arcron MainNet exists**, the honest answer is that this contract works fine without Arcron: deploy it on MainNet and let the stale-grace fallback path be the trigger. Anyone — you, a community member, a bot — can call `buyback()` once three grace windows have passed, and the sizing, the decay, the reference, the cap and the accounting are all still enforced on-chain rather than in someone's head. That is most of the value, and it converts cleanly to the scheduled version by calling `set_keeper` when Arcron arrives. Do not describe it as automated until it is.

MainNet deltas when the time comes: re-measure the round time and recompute `interval_rounds` (31,395 at 2.752 s); re-derive `min_slice`/`max_slice` from the real revenue rate; verify CORVID's clawback, freeze, manager and reserve **on MainNet, by reading the asset**, not from any document including this one; re-run the Test button against the MainNet Arcron deployment; and start with a small slice budget for 30 days.

---

## 11. What I am not sure about

Listed rather than smoothed over. Each is a measurement, not an opinion.

1. **Whether resource availability reaches three levels deep.** `docs/arcron.md:624-633` measured two (keeper → Arcron's inner call → the target's own inner transactions), using `smart_contracts/resource_probe/` and `scripts/spike_resources.py`. This design never needs a third level, which is one reason to prefer it — but §12's AMM module does, and the probe to measure it already exists and has not been pointed at that question.
2. **Whether a declared inner-transaction fee is charged or refunded when unused.** `scripts/keeper_bot.py:69-70` comments "Overpaying is harmless: an unused fee is simply not charged". I believe a declared flat fee is charged as set and surplus enters the pool and is spent. It does not affect this design (zero inner transactions in the scheduled path) but it means the bot may overpay 1,000 µALGO of `BONUS_FEE_MICROALGO` on non-bonus upkeeps, and it would matter a great deal to §12. Worth a five-minute LocalNet check independent of this work.
3. **Whether Algorand rejects an ASA transfer to the zero address.** I believe it does. Immaterial here — §7 uses the reserve address — but it is the assumption behind ruling out the obvious alternative.
4. **How much of a filler market actually exists for a token this size.** This is the design's central economic bet and nothing in this repo can answer it. The TestNet soak with the filler bot deliberately stopped for a week (Phase 1 step 6) is the closest available proxy, and it measures the failure mode rather than the base rate.
5. **The equilibrium filler margin.** I have modelled it as the marginal filler's DEX fee plus impact plus two transaction fees, driven down by undercutting. In a market with one filler it is the 10% cap instead. The realised number is a Phase 1 measurement and `max_premium_bps` should be set from it.
6. **Observed MEV on Algorand DEXes in practice.** I am confident about the mechanism — no fee-priority ordering, and `execute`'s lack of any group constraint means an executing keeper can wrap an in-call swap atomically. I am not confident about how often anyone actually does it. This design does not depend on the answer.
7. **Everything about Tinyman, Pact and Haystack.** No app id, no swap group shape, no fee split, no confirmation that any of them accepts an application account as swapper, no confirmation of what a v2 swap needs in its foreign arrays. Nothing in this repo can verify any of it. This design does not call a DEX, so none of it is load-bearing; §12 is entirely gated on measuring it.
8. **CORVID's current asset configuration** — clawback, freeze, manager, reserve, `default_frozen`, decimals, supply. Not read. Checklist items E1–E5, blocking.
9. **Whether `arcron-rain`'s move out of this repo on 2026-08-31** changes any of the integration guidance quoted here. The guide still points at `smart_contracts/subscription/` as the pull-pattern example, and upkeep 113 still drives the rain hub from outside, which is the more useful precedent: a target does not have to live near the keeper.

---

## 12. Appendix — the AMM module, if you insist

Deploy it as a **separate application with its own upkeep**, never folded into `Buyback`. Keeping them apart means a DEX-side failure kills one upkeep instead of the programme.

**Reference count, single-hop Tinyman v2 ALGO → CORVID, output landing in the swapper app:** validator app (1) + pool logicsig account (1) + CORVID asset (1) = **3 of 6**. The app's own account is free because Arcron already spends a reference on the target app. Reading pool reserves from the pool account's local state at the validator app reuses both references already counted — availability is a set, not a per-read charge. Two-hop USDC → ALGO → CORVID is **5** (ALGO is asset id 0 and needs no reference), which is servable but should not be built. Any aggregator is excluded outright: N pools chosen at execution time cannot be bounded at registration.

**Seven gates, all measured on TestNet against the real deployment before a line of contract code is written:**

- **G1** Tinyman v2 accepts an application account as swapper, via an inner group. If no, this module is dead and there is nothing to design around.
- **G2** The reference set is **path-independent** — the pool is read unconditionally before every guard, so a no-op simulate and a trading simulate report identical counts. Without this, the only path that trades is the only path that fails, and continuously-funded revenue makes that the steady state, not an edge case. Assert the count as an exact number in CI, in a fixed state.
- **G3** Resource availability reaches depth 3 (keeper → target → Tinyman's app call → Tinyman's inner axfer back). Measure with the `resource_probe` pattern. Unmeasured today.
- **G4** The exact inner-transaction count Tinyman emits, and therefore the exact fee. `keeper_bot.py`'s `EXTRA_FEE_MICROALGO = 2_000` is a flat constant covering Arcron's own two inners and **nothing more**; the module must set explicit `fee=` on every inner transaction from its own balance. Pin **both halves**, the way `scripts/reference_boundary.py` pins six-serviced/seven-refused: the execution succeeds with explicit fees and fails without them.
- **G5** Reserves read from the venue's own accounting, not from `pool.balance − pool.min_balance`. The pool logicsig's raw balance is writable by anyone sending it a payment, and it feeds the size cap.
- **G6** Slice capped at **f/3 ≈ 0.10% of the pool's ALGO reserve**, where f is the pool's *measured* one-way fee. The correct sandwich model is `profit(B) ≈ B(2x/R − 2f)`: break-even is `x/R = f` (0.30% at 30 bps), not `2f`, and profit is **linear in B with no interior optimum** — there is no bound on attacker size. Since the sandwicher is the executing keeper and is subsidised 7,000 µALGO to run it, the cap must sit well under break-even, not near it.
- **G7** A written operational answer to a permanent DEX-side revert: a creator `pause` on the module, and `cancel` on the upkeep. **State plainly that the never-fail guarantee stops at the venue boundary** — there is no try/catch on inner transactions, so a Tinyman `UpdateApplication` that changes the ABI reverts every execution until someone cancels.

Additional constraints if it ships: `dex_app`, `pool_addr` and the asset are immutable after creation (a settable pool is a rug); the module opts into CORVID and pushes it to `BURN_ADDR` in a **separate permissionless call**, not in the swap call, so the swap path keeps 3 references and the burn cannot fail the execution; the quote uses `op.mulw`/`op.divw` because `reserve_out × amount_in` at a 10¹⁵ base-unit reserve is ~10²², three orders past uint64; `min_output` is never zero; and the module carries the same spacing gate, the same stale-grace authorization and the same timelocked `recover()`.

Even with all seven cleared, the atomic keeper sandwich in §0 remains structural. The size cap makes it unprofitable on the trade; the keeper margin means it costs the attacker nothing to run anyway. That is the residual you are accepting, and it is why this is an appendix.