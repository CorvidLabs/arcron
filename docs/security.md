# Security

**Arcron is unaudited.** No third party has reviewed this contract. What
follows is the project's own analysis, written down so that it can be argued
with rather than taken on trust. Every claim names how it was checked, and
the ones that are merely reasoned are marked as such.

If you escrow ALGO in this contract you are relying on that analysis. Read
[Known and accepted risks](#known-and-accepted-risks) before you do.

## The shape of the thing

Arcron holds escrow for other people and pays it out to whoever does the work.
There is no owner, no rake, and no admin key over anyone's escrow. The
upgrade path is temporary and readable on-chain, and is given up before the
network asks anyone to rely on it. That last
one is the load-bearing design decision: it means nobody can change the rules
after you have escrowed funds, and that a bug cannot be patched in place.

Three parties can act:

| | Can | Cannot |
|---|---|---|
| **Creator** | register, fund, cancel their own upkeep | touch anyone else's upkeep, change a registered call, stop a keeper executing |
| **Keeper** | execute any due, funded upkeep and collect its fee | choose what is called, alter a schedule, take more than the fee the box says |
| **Target app** | anything it likes with its own state, inside the inner call | reach the keeper's funds, re-enter Arcron, change the upkeep that called it |

## Threat model

### An adversarial keeper

- **Can it take more than the fee?** No. The fee is computed entirely from box
  state and paid to `Txn.sender`. Checked by `tests/test_keeper.py`'s
  escalation sweep and, on chain, by `keeper_e2e.py` stage 16, which asserts
  every fee the contract actually charged equals what the bot predicted.
- **Can it serve an upkeep slowly to be paid more?** With a fee ceiling, yes.
  This is escalation working as designed, and it is bounded by `fee_cap`.
  What it *cannot* do is farm the ceiling repeatedly off a backlog: a replay
  never escalates. That guard exists because without it a keeper waiting two
  intervals between replays collected the ceiling every time while the backlog
  grew without bound, measured at 100% of a 400,000 µALGO escrow across 34
  runs. Pinned by `test_a_patient_keeper_cannot_farm_the_ceiling_off_a_backlog`.
  See also [the lone-keeper caveat](#a-keeper-with-no-competition-is-paid-the-ceiling).
- **Can it drain one upkeep to pay another?** No. Each box carries its own
  balance, checked before payment. `keeper_e2e.py` stage 20 drains one upkeep
  to zero and asserts its neighbours are untouched, that it cannot execute
  again, and that the app account stays solvent after every mutation.
- **Can it grief other keepers?** It can win races, which is the mechanism
  working. A losing keeper pays nothing: Algorand rejects a failed execution
  before it reaches a block, measured in stage 14.

### An adversarial creator

- **Can it strand the app account?** No. `register` collects exactly what the
  box costs, derived from the encoded box rather than restated
  (`test_register_charges_the_real_box_mbr`), and `cancel` returns exactly
  what it collected. The invariant is that the app's *spendable* balance
  always covers the sum of every escrow; stage 20 asserts it after every
  registration, execution and cancellation.
- **Can it register an upkeep that traps its own funds?** Not any more. Three
  states used to register happily and then fail on every execution: an
  argument list longer than the fan-out, a `fee_cap` the escrow could never
  reach, and a `fee_asset` with a zero bonus. All three are now rejected at
  registration. Escrow always leaves by `cancel` if nothing else.
- **Can it point an upkeep at a hostile app to hurt keepers?** It can make
  executions fail. That costs the keeper nothing, and `scripts/keeper_bot.py`
  backs such an upkeep off exponentially and persists that across restarts.

### A malicious target app

- **Can it re-enter Arcron?** No, and not because of our ordering: the AVM
  refuses outright with `attempt to re-enter <app>`. Measured under both
  catch-up policies, with and without a backlog, by
  `scripts/spike_reentrancy.py`. Arcron also writes box state before
  submitting any inner transaction, which is the right ordering independently.
  But it is the second line of defence, not the first.
- **Can it spend the keeper's ALGO?** No. Arcron's inner transactions carry a
  zero fee, so they draw on the group's pooled fee, which the keeper sized.
  A target's own inner transactions are paid by the target.
- **Can it burn the keeper's opcode budget?** It can consume the pool it was
  given. A target is handed about 1,179 opcodes for a one-argument call,
  measured in `scripts/spike_multiarg.py`. Exhausting it fails the execution,
  which costs the keeper nothing.
- **Can it reach resources it was not given?** Only what the keeper's
  transaction makes available, which is the keeper's choice. Availability does
  flow two levels down (measured in #24), so a keeper attaching references
  should know it is enabling the target as well as Arcron.

## Invariants, and how each is checked

The full list lives in [`specs/keeper/keeper.spec.md`](../specs/keeper/keeper.spec.md).
These are the ones that protect money:

| Invariant | Checked by |
|---|---|
| The MBR collected equals what the box costs | unit test deriving it from the encoded box; e2e solvency after every mutation |
| App spendable balance ≥ Σ escrows | `keeper_e2e.py` stage 20, after each registration, execution and cancellation |
| Escrow leaves only as a keeper fee or a creator refund | code review; every `itxn.Payment` in the contract has one of two receivers |
| An upkeep pays only from its own balance | stage 20 drains one upkeep and asserts its neighbours are untouched |
| The fee is never above `fee_cap`, never below `fee_per_execution` | parametrised sweep across the whole curve, plus a randomised range test |
| A replay never escalates | `test_a_patient_keeper_cannot_farm_the_ceiling_off_a_backlog` |
| An upkeep never bids more than it holds | `test_an_escrow_below_the_escalated_fee_falls_back_to_base` |
| `SKIP_AHEAD` always lands strictly in the future | every offset across four intervals |
| State is written before any inner transaction | code review, and re-entrancy is impossible anyway |
| Registering then cancelling is balance-neutral | unit test on the refund, e2e ends at 0 spendable / 0 escrowed |

## Arithmetic

Every balance mutation is a single addition or subtraction guarded by an
assertion immediately above it. The only multiply in the contract is the
escalation interpolation:

```
fee = base + (fee_cap - fee_per_execution) * excess // interval_rounds
```

`MAX_UPKEEP_FEE` and `MAX_INTERVAL_ROUNDS` are both 10⁹, so the product is at
most 10¹⁸ against a uint64's 1.8 × 10¹⁹. That bound comes from the inputs
alone. An earlier version relied on `excess ≤ Global.round` instead, which is
true but rests on the chain never reaching ~1.8 × 10¹⁰ rounds. That is not an
argument to stake an unpatchable contract on. Tested at both ceilings
simultaneously in `test_the_escalation_multiply_cannot_overflow_at_the_extremes`.

Integer division truncates in the creator's favour: the effective fee rounds
down, never up.

The AVM panics on overflow rather than wrapping, so the failure mode
everywhere else in the contract is a rejected transaction, not a wrong number.

## Immutability: upgradeable until frozen

**Algorand applications can be upgradeable.** An `UpdateApplication` call
replaces an app's approval and clear programs in place, and plenty of
contracts allow it behind an admin key. What this contract does is a choice,
and it is a two-stage one.

A deployment starts **unfrozen**. Its creator, and only its creator, can
replace the programs. `freeze` gives that up permanently: nothing sets
`frozen` back to 0, and no later call can restore an update path, because the
only call that could is an update.

`frozen` is global state, so the promise can be checked rather than believed:

```bash
poetry run python -m scripts.govern status --network testnet --app-id <id>
poetry run python -m scripts.verify_build --network testnet --app-id <id>
```

The first says whether the creator can still change the rules. The second says
whether the deployed bytecode is the source it claims to be. Together they are
the whole trust question.

**While a deployment is unfrozen, its creator can change the rules after you
have escrowed funds.** They could redirect payouts, raise fees, or drain
escrow. No statement of intent removes that, which is why the state is
readable and why freezing happens before anybody is asked to rely on it. Treat
an unfrozen deployment as one you are trusting a person with, and a frozen one
as one you are trusting only the bytecode with.

The reason the window exists at all is that being unable to fix a bug is
expensive while nobody depends on the deployment yet. Two earlier deployments
were abandoned rather than repaired, stranding 243,000 µALGO of box minimum
balance and making every creator cancel and re-register by hand.
Whether to freeze at all is a choice rather than a rule, and both answers
are ordinary on Algorand. Checked on MainNet: the Foundation's randomness
beacon, the Reti staking validator and Folks Finance pools accept `NoOp` only
and can never be updated; Tinyman AMM v2, Pact and AlgoFi all handle
`UpdateApplication`. [`releases.md`](releases.md) asks only that the decision
be recorded, not that it go a particular way.

Consequences worth stating plainly, all of which apply from `freeze` onward,
and none of which apply to a deployment that never calls it:

1. **A bug cannot be fixed.** The response to a serious bug is to tell
   creators to `cancel`, not to patch.
2. **A struct change is a new application** at a new app id, with an empty
   registry, and every creator must cancel and re-register by hand. Nobody can
   do it for them, because `cancel` is creator-only.
3. **`DeleteApplication` is refused always**, frozen or not. Deleting an app
   with escrow in it would strand every µALGO, so there has never been a path
   to it and freezing does not add one.

## Known and accepted risks

These are real, understood, and shipped anyway. Each says why.

### A keeper with no competition is paid the ceiling

Escalation pays more for lateness, so a keeper that is the only one watching
an upkeep is better off waiting for the fee to peak. It clears a market only
when there is a market. With one keeper, `fee_cap` is not a worst case. It is
the price, and the cadence is roughly half what was asked for.

**Mitigation:** the default is no escalation (`fee_cap = 0`), and the console
says so where the number is entered. Leave it at zero unless an upkeep is
genuinely going unserviced.

### A top-up does not reset lateness

Funding a long-dormant upkeep is charged the ceiling on the very next run,
because lateness is measured from the last *service* and a top-up is not one.
Resetting it would let any creator cancel escalation for one µALGO. The
console warns where the money is about to be spent.

### An upkeep can be stranded by its own target

If a target app is updatable and its owner changes it to reject, the upkeep
becomes unexecutable and only the creator can recover the escrow. If the
creator is gone, it is stranded permanently. **Prefer immutable targets, or
ones you control.** Nothing in Arcron can fix this without an owner key,
which would defeat the design.

### A refund can fail if the creator's account is empty

Algorand rejects a payment that leaves the receiver below the 100,000 µALGO
account minimum (measured: `balance 4000 below min 100000`). A creator whose
account has been closed out cannot receive a refund smaller than that until
someone funds the account first. The keeper side of this is already defended:
`keeper_bot.py` refuses to start below `ACCOUNT_MBR + one execution`.

### Overpaid MBR is not returned

`register` accepts an MBR payment larger than the box costs and credits the
excess to nobody. It cannot be stolen (it only makes the app account *more*
solvent), but it is not refunded either. Send the exact amount; the contract
exports the formula and the console computes it.

### Registry spam degrades keepers

Anyone can register upkeeps cheaply, and every keeper scans every box each
round. Box MBR is refundable, so a spammer's only real cost is transaction
fees and locked capital. Nothing on chain prevents it; a keeper that cared
would cache boxes and re-read on change.

### An asset upkeep at the minimum fee only attracts keepers who want the asset

An execution costs a keeper 3,000 µALGO in fees, or 4,000 when an ASA bonus is
paid, because the bonus is a third inner transaction. At `MIN_UPKEEP_FEE` a
plain upkeep clears 1,000 µALGO and an asset upkeep clears exactly nothing
(measured). That is the intended shape: the ALGO covers the keeper's costs and
the asset is the pay.

The consequence is a liveness one, not a safety one. An asset upkeep at the
floor is worth running only to a keeper that values the asset, so if none do,
it goes unserviced: funded, due, and ignored. A creator who wants generic
keepers to take it as well should set an ALGO fee above the floor.

### Post-quantum keepers are covered only while bytes are free

A Falcon-1024-signed `execute` is about 13× the size of an ed25519 one
(measured in `scripts/spike_quantum.py`). Algorand charges
`max(min_fee, size × fee_per_byte)`, and that per-byte rate is zero today,
which is the only reason `MIN_UPKEEP_FEE` covers a post-quantum keeper. The
floor is permanent and cannot be raised.

## Verifying a deployment

A frozen contract has no update path, so what is deployed is what was deployed. To
confirm *which* source that is:

```
poetry run python -m scripts.verify_build --network testnet --app-id <id>
```

It rebuilds from the working tree and compares the compiled bytecode (not the
TEAL text, which loses comments and formatting on assembly) against what
algod reports for that app. With no `--app-id` it prints the local hashes,
which is what a release records so a third party can check later without
trusting us.

Reproducing the build needs Python 3.13 (**never 3.14**, because coincurve has
no wheels) and `puyapy >=5.0,<5.10`, both pinned in `pyproject.toml`. The
ARC-56 specs in `smart_contracts/artifacts/` are committed, and CI fails if
they differ from a fresh build.

## Deployer key handling

The deployer creates the app, and while that app is unfrozen the deployer
**is** the key that can replace its programs. Treat it as the most valuable
secret in the project, not as a funding account.

- **The MainNet deployer must be a fresh account**, never one that has touched
  TestNet. The TestNet deployer in this repository is a throwaway and is
  assumed compromised.
- **It can rewrite a live contract while `frozen` is 0.** Whoever holds it
  could replace `execute` with something that pays them and drain every
  escrow. That is the cost of keeping an update path, and it is why `frozen`
  is readable on-chain rather than promised in a document.
- **For MainNet it should not be a bare mnemonic on a laptop.** Set
  `ARCRON_MULTISIG_THRESHOLD` and `ARCRON_MULTISIG_ADDRESSES` and the creator
  becomes a multisig address: `govern update` and `govern freeze` then write an
  unsigned transaction for the holders to sign wherever their keys live, and
  `scripts/deploy.py` refuses to run at all rather than quietly deploying from
  the single key instead. `fledge run smoke-multisig` proves on LocalNet that
  one holder of three cannot update and two can.
- **More keys than the threshold, on different devices held by different
  people.** Any one can then be lost without losing control, and any one can be
  compromised without losing the contract. Keys in one drawer are one key.

  The MainNet deployment uses **five keys with a threshold of three**
  (`NHQU7QBDTUC4Q5I7LV3A35GGG36QUK5EL6PM4ZVBJKZ7AS6EDOU7BCRDWA`). Two can be
  lost and two can be compromised without either losing control or losing the
  contract. The LocalNet smoke test uses three keys with a threshold of two
  because it only has to prove the mechanism, not carry anything.

  Member order is part of the address. A multisig address is the hash of
  `"MultisigAddr" || version || threshold || each public key in order`, so the
  same five keys in a different order are a different account holding nothing.
- **Once frozen, the key stops mattering** to anyone but its own ALGO. There
  is no owner, no admin, and no path back to one.

## If a bug is found

There is no upgrade path, so the playbook is short:

1. **Say so publicly and immediately.** An unfixable bug that nobody knows
   about is worse than one everybody knows about.
2. **Tell creators to `cancel`.** It is permissionless for them, returns
   escrow and box MBR, and needs nothing from us.
3. **Stop the keeper bots we run**, so we are not extending the life of a
   broken deployment.
4. **Fix forward in a new app**, and expect every creator to re-register by
   hand.

Nothing here can be done on the creator's behalf, which is a direct
consequence of having no owner. That trade is the point.

## Reporting

Open an issue on [CorvidLabs/arcron](https://github.com/CorvidLabs/arcron/issues)
for anything that is already public. For anything that is not, and while there
is no published contact, do not open an issue. The repository is private and
the deployment holds test funds only.
