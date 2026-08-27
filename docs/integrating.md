# Hooking your contract into Arcron

Integration is one method. This is everything else you need to know, in one
pass, so you do not have to assemble it from five places.

- [The hook](#the-hook)
- [Authorization](#authorization)
- [Making it durable](#making-it-durable) (the part people get wrong)
- [The pull pattern](#the-pull-pattern) (the most useful technique here)
- [Funding and operations](#funding-and-operations)
- [Reaching resources your hook cannot name](#reaching-resources-your-hook-cannot-name)
- [Calls with arguments](#calls-with-arguments)
- [An ASA bonus](#an-asa-bonus)
- [Four things that will cost you an hour](#four-things-that-will-cost-you-an-hour)
- [Testing it](#testing-it)
- [Getting a keeper to test against](#getting-a-keeper-to-test-against)

`examples/minimal_target.py` is a complete, compiling version of everything
below. Copy it and start editing; a test in this repo compiles it on every
run, so it cannot rot.

## The hook

Expose one NoOp ABI method that takes no arguments of its own:

```python
@abimethod()
def run(self) -> UInt64:
    ...
```

Arcron calls it with the method selector as the only application argument.
That is the simplest shape and the common one: `tick()uint64`,
`publish()uint64`, `distribute()uint64` and `sweep()uint64` in this repo are
all built that way.

A method taking arguments works too. The creator fixes the whole argument list
at `register`, so the selector goes first and each ARC-4 encoded argument
follows it, up to `MAX_CALL_ARGS` entries (three, counting the method selector). What a keeper cannot do is choose
or alter any of them, which is the guarantee the whole design rests on: a
keeper decides *when* your call happens, never *what* it says. See
[Calls with arguments](#calls-with-arguments) below.

The consequence for design: your hook works from your own state. It is not
handed parameters, so whatever it needs to decide must already be on-chain
when it runs. In practice that is a healthy constraint. It means anyone can
verify what the scheduled call will do before it happens.

## Authorization

Two choices, and most integrations should take the first.

**Restrict to the keeper app.** Nobody else can drive your schedule:

```python
assert Txn.sender == Application(self.keeper_app.value).address, "Only the keeper app"
```

Arcron's inner call comes from the keeper application's account, so that is the
sender to check. Derive the address off-chain with
`algosdk.logic.get_application_address(app_id)`, or in Python:

```python
from algosdk.logic import get_application_address
get_application_address(769891898)
```

**Leave it permissionless.** Anyone may call the hook, as `Pulse` and the
timed-release demo do. Correct when the hook is idempotent and its timing is
the only thing that matters: it means your contract still works if Arcron
disappears, and anybody can push it along. It is the wrong default when the
hook's effects depend on *when* it runs, because then anyone can choose the
moment.

Note what restricting does *not* buy you: the keeper app is permissionless,
so restricting to it means "only via a paid upkeep", not "only by someone I
trust".

## Making it durable

This is the part integrations get wrong.

### Your hook is called whether or not there is work

Arcron calls on every cadence, forever. The no-op path is the common path, so
make it cheap and make it return rather than fail:

```python
if self.pending.value == 0:
    return UInt64(0)      # right
    # assert False        # wrong, see below
```

### A hook that fails stops being serviced

This is the failure mode that matters most. When a target rejects, the keeper
bot backs that upkeep off exponentially: it waits 1, then 2, then 4 of the
upkeep's own intervals, up to 8, capped at about an hour in absolute terms
(1,286 rounds). That state is written to disk, so it survives a restart and a
`--once` cron invocation does not retry a doomed upkeep every run. Losing a
race to another keeper does not count as a failure and never backs off. Keep
failing and you are serviced more and more rarely, quietly.

Failing costs the keeper nothing. Algorand rejects the transaction before it
reaches a block, so no fee is charged (measured; see
[Operating a bot](arcron.md#operating-a-bot)). But it costs *you* the
schedule. Fail soft. Record the problem in state and return.

### You have more opcode budget than you think

Opcode budget pools across the app calls in a group, and an Arcron execution
contains two: Arcron's own call and the inner call to you. Measured on
LocalNet with `smart_contracts/resource_probe/`:

| Called | Budget remaining at method entry |
|--------|----------------------------------|
| Directly, as a plain app call | **684** |
| Through an Arcron upkeep | **1,250** |

So a hook driven by Arcron has roughly 1.8× the budget of the same method
called directly. It is not competing with Arcron for budget; it inherits the
pool Arcron's own call contributed to. Reach for `algopy.ensure_budget` only
if you exceed that.

### Assume it may run more than once, and in bursts

Scheduling advances from the *scheduled* round, not the round execution
happened, so an upkeep unattended for N intervals stays due N times and
catches up one interval per call. After an outage your hook may be called
several times in quick succession.

Make it idempotent, or make each call's effect depend only on current state.
If replaying missed periods is wrong for your use case (a prize draw, say),
say so on
[issue #7](https://github.com/CorvidLabs/arcron/issues/7), which is deciding
that policy and needs concrete cases.

### Rounds are not a clock

A cadence is a round count. A round is ~2.8 s nominally, less in practice
(TestNet measured 2.66 s), so "daily" means "every ~30,857 rounds" and slides
against the calendar by about a day and a half over thirty cycles. Arcron
promises "not before this round", never "at 09:00". If a wall-clock moment
matters, have the *hook* check the time and no-op when early, and schedule it
often enough to catch the window.

Related: do not set a cadence so tight that ordinary keeper lateness looks
like a real condition. Arcron's minimum interval is 10 rounds, and the demos
here use a floor of 30 rounds for anything that treats lateness as a signal.

### Authorization to the keeper app is not authorization of cadence

The check everyone reaches for:

```python
assert Txn.sender == Application(self.keeper_app.value).address, "Only the keeper app"
```

proves the keeper application called you. It does **not** prove that the
interval you registered has elapsed, because registering an upkeep is
permissionless. Anyone may point their own upkeep at your hook, on the
shortest interval the keeper allows, and pay the fees themselves.

That is harmless for a hook whose effect depends only on current state, which
is most of them. It is not harmless for a hook that *counts* something. A
billing hook that advances a period on every call can be fast-forwarded by
anybody willing to spend two minimum fees per call, and whoever benefits from
the count has an incentive to do it.

If your hook counts, meters, or accrues, enforce the interval yourself. Note
what this does and does not do:

```python
if Global.round < self.last_run.value + self.min_rounds.value:
    return self.count.value      # too soon: nothing to do, and no rejection
self.count.value += 1
self.last_run.value = Global.round
```

**Return, do not assert.** An earlier version of this guide said to assert,
and it was wrong in a way worth explaining, because the mistake is easy to
repeat.

Under `CATCH_UP`, an upkeep that fell behind stays due, so a keeper draining a
backlog calls again in the same round. An assert rejects that call, which
fails the whole `execute`, which the keeper bot records as a failure and backs
the upkeep off. Keep failing and the schedule stops entirely: exactly the
outcome the never-fail rule exists to prevent, arrived at through the code
that was supposed to protect you.

Returning refuses the work without refusing the call. The griefer still pays
the fee and still moves nothing, which is the whole point, and an honest
replay is a no-op rather than a fatal error.

One consequence to price in: under `CATCH_UP` those no-op replays are still
paid executions, so an outage costs one fee per missed interval while billing
advances once. If that matters, register `SKIP_AHEAD`, which does not replay a
backlog at all. `CATCH_UP` is the default because most hooks want every period,
and a metering hook usually does not.

`smart_contracts/subscription/` is the worked example. It had both bugs in
turn: it recorded `last_charged_round` on every call and never read it, and
then it asserted.

## The pull pattern

**Do the accounting in the scheduled call. Let counterparties collect in their
own transactions.**

If you take one thing from this guide, take that. Every demo in this repo is
shaped by it, and not for stylistic reasons:

**Resource availability.** An Arcron inner call reaches only what the keeper's
own transaction makes available, and nothing tells a keeper what your hook
needs. A scheduled call that tries to pay an arbitrary account, read a
balance, or call another app fails, because those resources are not
available to it. (Measured; the table is in
[arcron.md](arcron.md#what-an-arcron-triggered-call-can-reach).)

**Failure isolation.** A push payout to a closed or hostile account fails the
whole execution, which wedges the schedule for *everyone* your contract serves.
Pull confines that risk to the one claimant.

In practice:

```
scheduled_hook()   snapshot state, credit allocations, emit an event.
                   Move nothing. Call nothing.
claim()            the counterparty sends this themselves, and is therefore
                   always an available resource.
```

Worked examples: `smart_contracts/rain/` pulls a *resource* this way. The
scheduled draw fixes a beacon round and a participant supplies the beacon
reference when resolving. `smart_contracts/subscription/` credits the
provider on `settle`, who then `claim`s it themselves.

## Reaching resources your hook cannot name

A scheduled call can only touch what the executing transaction makes
available. Arcron stores no foreign arrays. It turns out it does not need to,
and neither do you.

Resource availability supplied on the *keeper's* transaction reaches two levels
down: to Arcron's inner call, and to your own inner transactions from it.
Measured in [#24](https://github.com/CorvidLabs/arcron/issues/24) across
payments, asset transfers, balance reads, holding reads and inner app calls.
The budget is 8 references per transaction. Arcron spends two of them, the
upkeep box and your app, leaving 6.

**A keeper does not have to be told which ones you need.** Simulation reports
the resources a call *would* have required, so a keeper simulates first,
attaches what the simulation names, and then sends. `algokit-utils` does this
by default, through its `populate_app_call_resources` send parameter, so a
keeper built on it services your hook with no configuration and no cooperation
from you.

Measured on LocalNet against a target that reaches for an account no argument
names:

| | |
|---|---|
| A raw transaction with no references | fails with `unavailable Account …` |
| The same call through a keeper that simulates first | **succeeds** |

So there is nothing to declare and nothing to implement. **Write your hook to
reach for what it needs and let the keeper discover it.**

An earlier version of this guide proposed a `resources()` view for your app to
declare them. That was unnecessary: simulation answers the same question for
every target, including ones whose needs change between runs, and including
ones written before the convention existed. It is not implemented and will not
be.

Two things still worth knowing:

- **Six is a real ceiling.** A hook that reaches for more than six distinct
  accounts, assets and apps cannot be serviced, however it is discovered. That
  is the constraint the *pull* pattern exists to sidestep, by allocating in
  the scheduled call and letting each recipient's own transaction carry its
  own availability.
- **Simulation sees the state at simulation time.** A hook whose resource needs
  depend on state that changes between the simulate and the send can still be
  mis-served. Keep what a scheduled hook touches predictable.

## Calls with arguments

An execution carries up to three app args, counting the selector. That is
enough for an ARC-4 method of arity two:

```python
@abimethod()
def settle(self, market_id: UInt64) -> UInt64: ...
```

For anything wider, declare the arguments as a single struct or tuple. That is
the trick ARC-4 itself uses at arg 15, and it makes any arity reachable:

```python
class Settlement(arc4.Struct):
    market_id: arc4.UInt64
    epoch: arc4.UInt64
    limit: arc4.UInt64

@abimethod()
def settle(self, settlement: Settlement) -> UInt64: ...
```

Every argument is fixed at registration. If your hook needs a value that
changes between runs, it has to derive it from its own state, from a resource
it pulls, or from the round. Arcron will not supply it, by design.

## An ASA bonus

An upkeep can pay a bonus in any asset on top of its ALGO fee, never
instead of it. That is deliberate: a keeper's real costs are ALGO, so keeping
the ALGO fee mandatory is what lets the contract guarantee profitability
without anyone having to price your token.

```
register(..., fee_asset=<asset id>, asset_fee=<base units>)
opt_in_asset(mbr_payment, upkeep_id, asset)   # 0.1 ALGO, permanent
top_up_asset(upkeep_id, asset_funding)
```

**Can you pay keepers *only* in your token?** In effect, yes. Set the ALGO
fee at the 0.004 ALGO floor and it stops being a reward and becomes a cost
reimbursement. An execution costs a keeper 0.003 ALGO in transaction fees, or
0.004 when a bonus is paid, so at the floor an asset upkeep hands the keeper
back exactly what it spent and your token is the entire pay. Measured, not
estimated.

What you cannot do is remove the ALGO altogether, and that is Algorand's
constraint rather than Arcron's: every transaction costs ALGO, so a keeper
paid purely in your token is out of pocket in ALGO until it sells some. A
contract cannot check that your token is worth more than the keeper's burn
without a price feed, so guaranteeing that anyone is ever willing to run your
upkeep would mean trusting an oracle. Reimbursing the ALGO instead keeps the
guarantee on-chain and costs about 1.5 ALGO a year for a daily upkeep.

Three things to know:

- **An asset upkeep at the minimum ALGO fee only attracts keepers who want
  your asset.** They break exactly even in ALGO, so the token has to be worth
  their while. If you want generic keepers to take it too, pay more ALGO.
- **The app must opt in before it can hold the asset**, which costs 0.1 ALGO
  of minimum balance permanently. There is no opt-out, so the deposit does not
  come back.
- **A keeper that is not opted in to your asset still executes**, takes the
  ALGO fee, and forfeits the bonus, which stays in your escrow and comes back
  on cancel. Your bonus reaches the keepers who want it and costs you nothing
  with the ones who do not.

Verified on TestNet with a real, freshly created account that had never opted
into a live bonus asset, not just in the LocalNet mocks: the execution went
through, paid only the ALGO fee, and the escrowed bonus was untouched. See
[docs/security.md](security.md#an-unopted-keeper-still-executes-and-the-bonus-stays-in-escrow)
for the transaction ids.

## Funding and operations

An upkeep is funded escrow. Executions are paid from it:

```
funded runs = balance / fee_per_execution        # with no fee ceiling
funded runs = balance / fee_cap                  # with one, this is the number
```

At the 4,000 µALGO minimum fee, 1 ALGO buys 250 executions, about 10 days of
an hourly cadence or 8 months of a daily one. Registering also costs box MBR
(`2,500 + 400 × (139 + len(encoded call_args))` µALGO, so 62,100 for a bare
4-byte selector), and that comes back in full when you cancel.

If you set a fee ceiling, budget against the ceiling. Read it as the price
you will *usually* pay, not the worst case.

Escalation pays more for lateness, so a keeper that is the only one watching
an upkeep has every reason to wait for the fee to peak before executing. It
clears a market only when there is a market: with several keepers competing,
one of them takes the work early at a lower price, and the ceiling is rarely
reached. With one keeper, the ceiling *is* the price, and the cadence is
roughly half what you asked for. Arcron's TestNet deployment currently has one
keeper.

So: leave the ceiling at zero unless an upkeep is genuinely going unserviced.
It buys reliability from a competitive keeper set and buys nothing from a
single one.

A ceiling does not raise the balance at which an upkeep stops being
executable. When the escalated fee is more than the escrow holds, the contract
drops the fee back to `fee_per_execution`, so an upkeep with a 4,000 µALGO fee
and a 12,000 µALGO ceiling stays executable down to 4,000, not 12,000.

That fallback is not a convenience. Lateness only ever grows, so the escalated
price only ever rises: without it, an escrow that once fell below the escalated
fee could never reach it again, and the upkeep would sit holding up to a full
ceiling of escrow that no keeper could spend and only its creator could
recover. What a ceiling costs you is the escrow that a late run can consume,
not the point at which the upkeep goes quiet.

- **Anyone can `top_up`.** Funding an upkeep that *already exists* is
  permissionless, so a counterparty with an
  interest in your schedule running can pay for it. Only the creator can
  `cancel`.
- **Running dry is silent.** The upkeep goes dormant (no keeper can execute
  it) and resumes the moment someone tops it up. Nothing announces it.
- **Notice before it happens:** `poetry run python -m scripts.keeper_bot --check`
  reports an upkeep whose escrow has fallen below one fee as **starved** and
  exits non-zero, so it drops straight into cron.
- **Cancel when done.** A hook that has finished its job for good, like a
  one-shot task that already ran, keeps being called and keeps paying keepers
  to do nothing until you cancel.

## Four things that will cost you an hour

Found by writing an integration from this guide and hitting them. None is
Arcron's doing; all four are the toolchain, and all four look like your
contract is wrong when it is not.

**Returning a computed value trips mypy before Puya sees it.** An ARC-4
field's `.native` is a `UInt64` at run time, but mypy reads it as `Any`, so
returning one fails the build:

```
error: Returning Any from function declared to return "UInt64"  [no-any-return]
```

The obvious fix makes it worse. Wrapping in `UInt64(...)` is a conversion Puya
rejects outright (`error: unexpected argument type`), because the value is
already a `UInt64`. Bind through a typed local instead:

```python
balance: UInt64 = box.value.balance.native
return balance
```

**A zero-argument method has no generated `Args` class.** The client generator
emits one only for methods that take arguments, so `ClaimArgs` does not exist
for `claim()`. Call it with no `args` at all:

```python
app.send.claim(params=algokit_utils.CommonAppCallParams(sender=..., signer=...))
```

**A create method with arguments is reached through `create` twice.**
`factory.send.create` is an object, not a function, and the ABI creator is a
method on it. Bare creation reads `factory.send.create.bare()`, and an ABI
creator reads:

```python
app, _ = factory.send.create.create(args=CreateArgs(...))
```

**An inner payment submitted with `fee=0` is paid for by the caller.** That is
the right way to write it, since it keeps the contract from spending its own
balance on fees, but the calling transaction then has to cover both. Without
`extra_fee` the group is underfunded and the call fails:

```python
app.send.claim(
    params=algokit_utils.CommonAppCallParams(
        sender=provider.address,
        signer=provider.signer,
        extra_fee=algokit_utils.AlgoAmount(micro_algo=1_000),
    )
)
```

## Testing it

Prove it on LocalNet before TestNet, and prove it on TestNet before you rely
on it. `scripts/keeper_e2e.py` is the reference for what that looks like; each
demo script here (`rain_demo.py`, `community_rain_demo.py`,
`subscription_demo.py`) is a smaller worked version.

**Unit tests will not catch the things that break integrations.**
`algorand-python-testing` mocks *record* inner app calls without executing
them, and do not enforce minimum balances. So a hook that fails when actually
invoked, or an app that cannot pay out what it owes, passes its unit tests and
fails on a real chain. Anything depending on either belongs in a LocalNet
end-to-end test.

Start here:

```bash
algokit localnet start
fledge lanes run local          # the whole suite, including seven worked demos
```

Then register against the live TestNet keeper app `769891898` with
`examples/register_upkeep.py`.

## Getting a keeper to test against

Everything above assumes an Arcron deployment exists. On TestNet one does, and
its app id is `769891898`. On LocalNet you have to make one, and nothing else
on this page tells you how:

```bash
algokit localnet start
fledge run deploy-localnet      # deploys the keeper and the pulse demo target
```

That prints the app id to register against. It is idempotent, so running it
against a LocalNet you have used before **reuses the existing app**, complete
with whatever upkeeps are already in its registry. Do not be surprised when a
keeper bot reports more upkeeps than you created, or executes somebody else's.
`algokit localnet reset` gives you an empty chain.

The exact ARC-4 signatures, the addresses the two `register` payments go to,
and the box reference the `register` group must carry are in
[`arcron.md`](arcron.md#public-api). You cannot build a `register` group
without all three, and none of them is derivable from this page.
