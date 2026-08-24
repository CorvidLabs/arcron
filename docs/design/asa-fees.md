# Design: ASA-denominated upkeep fees

**Status:** proposed, for review
**Issue:** [#9](https://github.com/CorvidLabs/archon/issues/9)
**Shares a struct with:** [#7 and #14](scheduling-and-fees.md) and [#8](call-shapes.md)
**Reproduce every number here:** `poetry run python -m scripts.spike_asa_fee --network localnet`

Upkeep escrow and keeper fees are ALGO-only. #9 asks for rewards denominated
in an ASA, first target CORVID, and calls the profitability floor "the hard
part": an ASA-denominated fee needs a floor worth at least the keeper's real
transaction costs, and that seems to need a price.

It does not, and the reason is the whole design.

## The floor problem dissolves if the ALGO fee stays

A keeper spends roughly 3,000 µALGO of transaction fees per execution, and
those fees are paid in ALGO whatever the escrow holds. `MIN_UPKEEP_FEE` exists
to cover them. So keep it, in ALGO, mandatory — and make the ASA a **bonus on
top**, never a replacement.

Then the contract never needs to know what the ASA is worth. It only needs to
guarantee the keeper is not out of pocket, which it already does, on chain,
with no oracle and no price assumption anywhere. #9's acceptance criterion —
"profitability floor enforceable on-chain without an oracle" — is met by not
moving the floor.

This also settles #9's first design question. **Per-upkeep asset choice is
safe** once every upkeep is profitable in ALGO alone: a keeper never has to
decide "will I accept asset X" as a *safety* question, only as a ranking one.
Deciding which bonuses are worth chasing is bot policy, not consensus.

## A target cannot pay the keeper itself

Before adding an asset path to the contract, it is worth knowing whether the
contract needs one at all — a target that could pay the keeper directly would
reduce #9 to documentation. Part A asks the target who it thinks called it:

| How the target was called | Caller it sees |
|---|---|
| directly | the account that sent the transaction |
| through an Archon upkeep | **Archon's app account** |

An Archon-executed call arrives as an inner transaction, and an inner
transaction's sender is the app that submitted it. **The target never learns
who the keeper is.** So a target cannot pay, tip or credit a keeper unless
Archon tells it who to pay — which is a different design, priced and rejected
below.

## What an asset costs

Part B measures a freshly created app account:

| | Minimum balance |
|---|---|
| base app account | 100,000 µALGO |
| holding 1 asset | 200,000 µALGO |
| holding 2 assets | 300,000 µALGO |

**+100,000 µALGO per asset, permanently**, for as long as the app can hold it.
Someone has to fund that, and §4 decides who.

Part C compiles and deploys an ASA-fee variant derived from the real contract,
registers an upkeep with a 250,000-unit bonus and runs it twice:

| | Program | Box | Box MBR |
|---|---|---|---|
| today | 729 B | 97 bytes | 41,300 µALGO |
| with the ASA path | 1,218 B (+489) | 121 bytes | 50,900 µALGO |

| Execution | ALGO fee | ASA bonus |
|---|---|---|
| keeper **not** opted in to the asset | 4,000 | forfeited — cannot receive |
| keeper opted in | 4,000 | 250,000 |

That second table is the design. An un-opted-in keeper does not fail; it
executes normally, takes the full ALGO fee, and forfeits the bonus.

## Proposal

### 1. An ASA bonus alongside the ALGO fee, never instead of it

`fee_per_execution` stays in µALGO and stays above `MIN_UPKEEP_FEE`. Every
upkeep, ASA-denominated or not, remains executable at a profit by any keeper.

### 2. Three fields, per-upkeep asset

```
fee_asset: uint64        # 0 means ALGO only, which is every upkeep today
asset_fee: uint64        # bonus paid per execution, in the asset's base units
asset_balance: uint64    # bonus escrow remaining
```

`fee_asset = 0` is the default and the entire existing behaviour, so nothing
about an ALGO upkeep changes except its box getting 24 bytes longer.

The ASA escrow is funded by a separate `top_up_asset(upkeep_id, axfer)`, not
by `register`. An asset transfer cannot be an optional member of a transaction
group, so folding it into `register` would force every ALGO-only registration
to carry a zero-amount transfer of an asset it does not use. A separate method
also lets a creator add to the bonus later, exactly as `top_up` does for ALGO.

### 3. The opt-in gate: a keeper who cannot be paid still executes

The bonus is paid only when there is one, the escrow covers it, **and**
`Txn.sender.is_opted_in(asset)`. Measured above: the un-opted-in keeper's
execution succeeds and pays the ALGO fee.

The alternatives are worse. Reverting makes an upkeep unserviceable by any
keeper who has not opted in — cheap to discover, since a rejected execution
costs a keeper nothing ([#13](https://github.com/CorvidLabs/archon/issues/13)),
but it silently shrinks the keeper set for exactly the upkeeps that are paying
extra. Accruing the bonus to a claim balance needs a box per keeper-and-asset,
which is more MBR and more code than the bonus is worth.

The honest cost: the fee is not always what it says, which is the same
tension as open question 3 in [`scheduling-and-fees.md`](scheduling-and-fees.md).
A keeper can under-earn without noticing. That is a tooling problem, not a
contract one — `keeper_bot --check` should warn on startup for every asset it
is not opted in to, and the console should show the bonus with an explicit
"opt in to earn this" marker.

### 4. The app account's asset opt-in is permissionless and permanent

`opt_in_asset(mbr_payment, asset)` lets anyone pay the 100,000 µALGO and opt
the app account in. It is not refundable and there is no opt-out.

Refunding it properly needs a per-asset reference count so the last cancel can
release the MBR, which is a box per asset — more minimum balance and more code
than the 0.1 ALGO it would ever return. Restricting the opt-in to the deployer
would put an owner back into a contract that deliberately has none.

The console must say plainly that this deposit does not come back.

### 5. Cancel returns the unspent bonus

The creator gets their remaining ASA back along with their ALGO and box MBR,
which means they must be able to receive it. When `asset_balance > 0`, cancel
asserts the creator is opted in, with a message that says so, **before**
refunding anything. Costs 55 bytes of program and is not optional: an upkeep
that cannot return its escrow is not an escrow.

### 6. CORVID is not wired in

The asset id `3225439167` appears nowhere in the contract, in any default, or
in any deployment parameter. `fee_asset = 0` is the default; the ALGO path is
untouched; "no token required" stays literally true. This is a **capability**,
per [`1.0.md`](1.0.md), and a capability is only a capability if the ALGO
default is still complete on its own.

## Cost

For an upkeep that never uses it: **+24 bytes of box, +9,600 µALGO of MBR**,
refunded on cancel. Every ALGO-only upkeep pays that so the contract can offer
a feature it does not use, which is the honest price of a capability in a
contract that cannot be upgraded.

Stacked across the whole 1.0 batch (Part D — the numbers that decide whether
it can ship as one contract at all). Every row is compiled: #7 and #14 are in
the contract, and #8 and #9 are patched onto it and built by puyapy.

| Contract | Approval | Pages | Page headroom |
|---|---|---|---|
| before the batch | 729 B | 1 | 1,319 |
| the contract today, with #7 + #14 | 966 B | 1 | 1,082 |
| + #9 (ASA bonus) | 1,483 B | 1 | 565 |
| + #8 at fan-out ceiling 4 | 1,458 B | 1 | 590 |
| **the whole 1.0 batch, ceiling 4** | **1,990 B** | **1** | **58** |

**58 bytes.** The batch fits in one 2,048-byte program page and very nearly
does not. An estimate made before #7 and #14 were written put this at 141; the
real scheduling code is 966 bytes against a sketched 887, and the patches on
top of it cost more too.

A second page costs the deployer another 100,000 µALGO of minimum balance
permanently, so this margin is the real constraint on 1.0's scope. The only
dial left is #8's fan-out ceiling, and at a ceiling of 3 the batch is 1,814
bytes with 234 spare — which is the setting to take if #9 lands in the same
deployment. **Nothing else should be added to this batch without compiling it
first.**

Box MBR across the batch, for a one-argument upkeep: the fixed component
becomes **139** (9 name + 130 head), so 149 bytes and **62,100 µALGO** — up
from 41,300, or **+50%** on the entry price of an upkeep. A deposit, refunded
on cancel, not a fee.

## Considered and rejected

- **Denominate the fee in the ASA instead of ALGO.** This is what #9 literally
  asks for, and it is the one shape that needs a price: the floor has to be
  worth ~3,000 µALGO of real costs, and nothing on chain can check that
  without an oracle. A naive fixed floor makes the upkeep dormant the moment
  the asset moves.
- **One asset per deployment.** Saves 8 bytes per box, and removes the
  "which assets will I accept" question. But it fragments the keeper network
  across app ids — keepers would have to watch each deployment — and it makes
  the asset a property of the network rather than of an upkeep, which is
  exactly the commitment 1.0 says not to make.
- **Let the target pay the keeper.** Requires Archon to name the keeper in the
  call, since Part A shows the target cannot otherwise know. Priced: #8 plus
  keeper-naming is **1,730 B**, slightly *more* than #8 plus the whole ASA path
  (1,721 B), because the fan-out has to double. So it costs the same page
  budget, and the reward is not escrowed by Archon — a target that runs out
  simply stops paying while Archon keeps paying the ALGO fee. Worse on both
  axes.
- **Pack the three fields into a dynamic tail** so an ALGO-only upkeep pays 4
  bytes instead of 24. Saves 8,000 µALGO — about a fifth of a cent — per
  upkeep, in exchange for a third encoding shape in two decoders and two
  pinned vectors. Not worth it.

## What has to move together

The five-file lockstep from [#31](https://github.com/CorvidLabs/archon/issues/31):

1. `smart_contracts/keeper/contract.py` — struct, `register`, `execute`, `cancel`, `top_up_asset`, `opt_in_asset`, `BOX_MBR_FIXED`
2. `scripts/keeper_bot.py::_decode_upkeep` — three more fields
3. `web/src/app/core/upkeep.ts` — its TypeScript twin
4. `tests/test_keeper_bot.py` and `web/src/app/core/upkeep.test.ts` — the pinned box vectors
5. `specs/keeper/` — Public API, requirements, testing, Change Log

Beyond the struct:

- **`keeper_bot --check`** warns for every fee asset it is not opted in to.
  Without it the opt-in gate is a silent earnings leak.
- **The console** must display asset amounts in the asset's own decimals. The
  existing convention is ALGO with 6 decimals; that needs generalising, and
  the asset's decimals come from algod, not from the box.
- **`scripts/keeper_e2e.py`** gains the two rows from Part C — an opted-in
  keeper and an un-opted-in one — because the gate is the part most likely to
  regress silently.
- **`docs/archon.md`** records the decision, per #9's first acceptance
  criterion, and the README roadmap moves the item.

## Open questions for review

1. **Should the app be able to opt *out* of an asset?** It would release
   100,000 µALGO once no upkeep holds a balance in it. It also needs a
   zero-balance guard and more program bytes, out of 141. Recommendation: no
   for 1.0, and say so in the console.
2. **Should a keeper be able to nominate a different receiver?**
   `execute(uint64,address)` costs no struct bytes, but changes a signature
   every keeper, the console and the notifier depend on. Recommendation: no —
   the opt-in gate already removes the failure this would fix.
3. **Forfeit or accrue?** As specified, an un-opted-in keeper's bonus stays in
   escrow and eventually returns to the creator on cancel. The alternative is
   that the creator's cost is predictable but the keeper's earnings are not.
   Either is defensible; forfeiting is the one that needs no extra state.
4. **Should `asset_fee` have a minimum?** No floor is needed for
   profitability. But `fee_asset > 0` with `asset_fee == 0` is a nonsense
   state that pays 24 bytes of MBR for nothing, and rejecting it at
   registration costs almost nothing.

## Recommendation

Take it, as a bonus rather than a denomination, in the same deployment as #7,
#14 and #8 — and treat the 141-byte page margin as the reason not to add
anything else to that deployment.

Implementation order once the design is agreed: contract and spec first, then
both decoders and both pinned vectors in the same commit, then the bot's
opt-in warning, then the console's asset-decimal handling, then the e2e rows.
Deploy last, and only after `fledge lanes run local` is green on all of it.
