# Keeper contract security audit, 2026-09-01

By Claude Opus 5, in this repository, with LocalNet. Scope is
`smart_contracts/keeper/contract.py` (562 lines, TestNet 769891898) and only
what can reach the money it holds. Unlike the 2026-08-25 passes this is not a
whole-repo review: it reads the compiled TEAL, runs the contract on a real AVM
under conditions the mocks cannot produce, and writes down what it could not
establish.

The convention here is that a finding is not real until a test reproduces it.
One finding got a test. Everything else got an experiment and a number.

## What was done

- Read the contract, `Keeper.approval.teal` (the router at lines 1–140 and
  every `itxn_field` block), the four prior review rounds in this directory,
  `scripts/attacks.py`, `tests/test_keeper.py`, `deploy_config.py` and
  `govern.py::create`.
- Ran `poetry run pytest tests/ -q`: 504 passed before this audit, 508 after.
- Ran `scripts/attacks.py` on LocalNet: all 3 refused for the right reason.
- Ran `scripts/spike_reentrancy.py` on LocalNet: all 3 variants refused by
  the AVM with `attempt to re-enter <keeper>`.
- Built a hostile target (`see()` logs what it can observe; `burn(n)` spends
  opcode budget; `fail()` refuses), deployed it and a **fresh keeper created
  with no funding** on LocalNet, and drove five experiments through raw
  transaction groups. Their numbers are below.

## Findings, ranked by money lost

### F1 · Low · The app account's own solvency is never enforced, and nothing live watched it

**Path.** `register` charges the box its exact MBR (`BOX_MBR_FIXED +
400·len(call_args)`, proven equal to the ledger's figure by
`test_register_charges_the_real_box_mbr`). `opt_in_asset` charges the exact
0.1 ALGO holding deposit. Nothing charges the 0.1 ALGO base minimum balance
the app account needs to exist. `deploy_config.py` sends it after creation;
`govern.py::create`, the MainNet path, logs "Afterwards: fund the app
account's base minimum balance" and cannot do more, because the app address
does not exist until the create confirms. No script that reads the live
deployment (`health`, `clock`, `verify_build`, `govern status`) compared the
account's spendable balance to the sum of the boxes.

**Worked exploit, measured on LocalNet against a keeper created and never
funded.** There is no attacker; the victim is the first creator.

| step | result |
|---|---|
| register, MBR payment exactly 62,100, funding 120,000 | refused by the ledger: `balance 62100 below min 100000`. The check is per transaction, so the first payment alone must reach 0.1 ALGO. |
| same, MBR payment overpaid to 100,000 | accepted. Box says balance 120,000. Account 220,000, min-balance 162,100, **spendable 57,900**. |
| execute ×14 at 4,000 | accepted |
| execute #15, box still says 64,000 | refused: `balance 160000 below min 162100` |
| creator `cancel` | refused: `balance 37900 below min 100000` |
| a stranger pays the account 100,000 | creator `cancel` accepted, refund 126,100 (64,000 escrow + 62,100 MBR) |

The creator's 37,900 overpayment stays in the account forever, as surplus
does by design. Their 126,100 was stranded until someone else's 0.1 ALGO
arrived. Ceiling across the whole registry: 0.1 ALGO, once, because surplus
only ever accumulates and nothing pays it out. No third party can lose
anything: the ledger refuses the payment rather than letting `cancel` or
`execute` spend another box's MBR.

**Why it is Low rather than nothing.** A console user pays the exact MBR and
is refused at simulate time with a message that names the app's balance, so
the common case is loud. The stranded case needs an overpaying first creator
and an operator who skipped the runbook step. It is here because the MainNet
create path relies on a log line, and because "the boxes are the book" is an
invariant the contract assumes and cannot check.

**Fix.** Two parts, one done here.

1. `scripts/registry_health.py` now reads the app account and prints
   `app account  N ALGO spendable  M ALGO escrowed`, flagging
   `THE APP CANNOT PAY OUT X uALGO IT HOLDS IN ESCROW` whenever the sum of the
   boxes exceeds `amount − min-balance`. Against TestNet 769891898 today:
   54.217 spendable, 54.217 escrowed, solvent to the microalgo.
2. Treat funding as a step of the create runbook with a check, not a log line:
   `govern status` should print the same line, and `docs/runbooks` should
   say a keeper is not created until `health` shows it solvent.

**Verify.** `tests/test_registry_health.py::TestRegistrySolvency` (4 tests;
the first two use the LocalNet numbers above). They fail without the change
because `RegistrySolvency` does not exist.

### F2 · Known, now confirmed on chain · A keeper can sandwich the target

**Path.** `execute` puts no constraint on the outer group. The inner call to
the target runs in its own group.

**Measured.** Outer group `[pay, execute, pay]` (size 3). The target's
`see()` logged: `group_size = 1`, `group_index = 0`, `caller_application_id =
<keeper>`, `sender = <keeper app address>`. The target cannot see the outer
group, cannot learn the keeper's address, and cannot tell a sandwiched call
from a plain one.

**Who profits.** Whoever runs the keeper, by whatever the target's state
change is worth when bracketed: for a buyback, the price impact of the buy.
The defence is a target-side property. A group scan in `execute` (no other app call in the outer group may
name `target_app`) would stop the naive shape and not the routed one, at the
cost of opcode budget on every execution, and is not recommended.

### F3 · Known · Until `freeze`, the creator key is the contract

Confirmed from the router: `update` requires `OnCompletion ==
UpdateApplication`, `Txn.sender == creator` and `frozen == 0`; there is no
bare update, no delete path, and extra pages and schema are fixed at create.
`freeze` genuinely closes it. Until then an unfrozen keeper holding real money
is exactly as safe as its creator's signing setup, which is why MainNet
requires the `corvid.algo` multisig. Nothing new; it remains the largest
number on this page.

## Informational

- **I1 · A keeper can decline the fee fallback.** When the escalated fee
  exceeds the escrow, `execute` drops back to base so the upkeep stays
  executable. A keeper can instead `top_up` the difference in the same group
  and collect the cap. Upkeep with base 4,000, cap 40,000, escrow 30,000, one
  interval late: fallback pays 4,000; top up 10,000 and execute pays 40,000,
  netting 30,000, and the escrow reads 0 instead of 26,000. The creator pays
  exactly the cap they agreed to for exactly the lateness that occurred, one
  execution earlier than the fallback would have. No third-party loss; no test.
- **I2 · `top_up_asset` accepts `asset_amount == 0`** (a no-op that costs
  the caller a fee) and accepts a clawback-sourced transfer, because it binds
  `sender`, not `asset_sender`. Only the asset's clawback address can do the
  latter and it already controls every holding of that asset.
- **I3 · `opt_in_asset` griefing is bounded.** Opting the app into junk
  costs the attacker 0.1 ALGO plus a box per asset and harms nobody; a
  duplicate opt-in keeps a second deposit as surplus.
- **I4 · Tooling.** `fledge run health` returned HTTP 403 from the public
  TestNet node on two of three runs today, before reaching any code changed
  here. It is a rate limit, not a finding, and it means the notifier cannot
  rely on one attempt.

## Examined and found sound

1. **`execute` against a hostile target.** Reentry into `execute`, `cancel`
   or `register` is refused by the AVM (`attempt to re-enter`), measured. A
   target that burns budget fails with `dynamic cost budget exceeded` and a
   target that asserts fails with `err opcode executed`; in both cases the
   keeper's account moved by 0 µALGO, because a rejected transaction is not
   in the ledger and carries no fee. The box is written before the inner call
   and reverts with it. Boxes are readable only by their app, so there is no
   state a target can diverge.
2. **Hostile keeper.** No group constraints exist to abuse; the only outflows
   are `fee ≤ box.balance` and the refund in `cancel`. `Global.round >= due`
   plus the write-before-call means two keepers racing produce one payment
   (`scripts/keeper_race.py`).
3. **Escalation and replay.** `last_serviced_round` is set to the
   registration round, so a first execution one interval later is on time
   at base. The `due > last_serviced` gate limits a neglect episode to one
   cap payment; every replay under CATCH_UP pays base. `(cap − base) · excess`
   is at most 10¹⁸ and cannot overflow; `lateness` cannot underflow because
   `last_serviced ≤ round` always.
4. **Payment validation.** All four sites bind receiver, sender (`==
   Txn.sender`), `rekey_to` and `close_remainder_to`; amounts are checked
   against the exact MBR, `max(fee, cap)`, and `> 0`. Double counting is
   structurally impossible: Puya reads the payments at `GroupIndex − 2` and
   `− 1` with a type assert. Measured: `[pay, pay, register, top_up]` fails
   at `top_up`'s type check because the transaction before it is the app call.
   A zero funding payment fails `Funding must cover at least one execution`.
5. **`cancel`.** Refund is `balance + exact box MBR`; the asset is best
   effort and checked (opt-in, holding, both freeze flags) before the ALGO
   leaves; `clawback_e2e.py` proves clawed, frozen and destroyed assets on
   chain. Draining below the account's minimum is refused by the ledger
   rather than bricking anyone else's box (F1 is the only way to reach it).
6. **Box accounting.** MBR is computed on the bytes stored, and Puya
   validates the `byte[][]` encoding at dispatch, so a crafted argument
   cannot underpay; the key `b"u" + itob(id)` never repeats.
7. **ASA paths.** Every balance and freeze read is guarded by an opt-in
   check; `asset_balance > 0` implies the app holds the asset; the bonus is
   skipped, never reverted, when the keeper cannot receive it.
8. **Inner transactions.** Every one sets `Fee 0` and no rekey or close
   field; the app account never pays a fee from escrow.
9. **Governance.** Bare calls only on create; unknown selectors `err`; no
   OptIn, CloseOut or Delete route.

## What could not be established

- Anything about MainNet that differs from LocalNet's consensus parameters.
  The experiments assume they match, which they do today.
- The economics of a keeper set that colludes to let every escalating upkeep
  run one interval late. The contract pays the cap the creator agreed to;
  whether that market clears is not a property of this code.
- That the public node's 403s are only rate limiting.

## Would I deploy this to MainNet holding real money?

The contract, as written: yes, with two conditions that are outside the
contract. First, the create runbook funds the base minimum balance and proves
it with `health` before the app id is shared; the check now exists. Second,
the creator is the multisig and the freeze decision is made on a date, not
on a feeling: everything on this page is smaller than an unfrozen deployment
signed by a single key. No line of `contract.py` needs to change first, and
after this audit I would say the same thing the four earlier rounds said in
their own words: the contract is the strongest part of the repository, and the
risk lives around it.
