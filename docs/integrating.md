# Hooking your contract into Arcron

Integration is one method. This is everything else you need to know, in one
pass, so you do not have to assemble it from five places.

- [The hook](#the-hook)
- [Authorization](#authorization)
- [Making it robust](#making-it-robust) — the part people get wrong
- [The pull pattern](#the-pull-pattern) — the most useful technique here
- [Funding and operations](#funding-and-operations)
- [Testing it](#testing-it)

`examples/minimal_target.py` is a complete, compiling version of everything
below. Copy it and start editing; a test in this repo compiles it on every
run, so it cannot rot.

## The hook

Expose one **NoOp ABI method that takes no arguments of its own**:

```python
@abimethod()
def run(self) -> UInt64:
    ...
```

Arcron calls it with exactly one application argument — the method selector —
and no foreign arrays. That is the whole call shape. `tick()uint64`,
`publish()uint64`, `distribute()uint64` and `sweep()uint64` in this repo are
all the same shape.

The consequence for design: **your hook works from your own state.** It is not
handed parameters, so whatever it needs to decide must already be on-chain
when it runs. In practice that is a healthy constraint — it means anyone can
verify what the scheduled call will do before it happens.

## Authorization

Two choices, and most integrations should take the first.

**Restrict to the keeper app** — nobody else can drive your schedule:

```python
assert Txn.sender == Application(self.keeper_app.value).address, "Only the keeper app"
```

Arcron's inner call comes from the keeper application's account, so that is the
sender to check. Derive the address off-chain with
`algosdk.logic.get_application_address(app_id)`, or in Python:

```python
from algosdk.logic import get_application_address
get_application_address(769802474)
```

**Leave it permissionless** — anyone may call the hook, as `Pulse` and the
timed-release demo do. Correct when the hook is idempotent and its timing is
the only thing that matters: it means your contract still works if Arcron
disappears, and anybody can push it along. It is the wrong default when the
hook's effects depend on *when* it runs, because then anyone can choose the
moment.

Note what restricting does **not** buy you: the keeper app is permissionless,
so restricting to it means "only via a paid upkeep", not "only by someone I
trust".

## Making it robust

This is the part integrations get wrong.

### Your hook is called whether or not there is work

Arcron calls on every cadence, forever. The no-op path is the common path, so
make it cheap and make it **return** rather than fail:

```python
if self.pending.value == 0:
    return UInt64(0)      # right
    # assert False        # wrong — see below
```

### A hook that fails stops being serviced

This is the failure mode that matters most. When a target rejects, the keeper
bot marks that upkeep failed and **skips it for the rest of the run**. Keep
failing and you are simply not serviced any more, quietly.

Failing costs the keeper nothing — Algorand rejects the transaction before it
reaches a block, so no fee is charged (measured; see
[Operating a bot](arcron.md#operating-a-bot)) — but it costs *you* the
schedule. Fail soft. Record the problem in state and return.

### You have more opcode budget than you think

Opcode budget pools across the app calls in a group, and an Arcron execution
contains two — Arcron's own call and the inner call to you. Measured on
LocalNet with `smart_contracts/resource_probe/`:

| Called | Budget remaining at method entry |
|--------|----------------------------------|
| Directly, as a plain app call | **684** |
| Through an Arcron upkeep | **1,250** |

So a hook driven by Arcron has roughly **1.8× the budget** of the same method
called directly. It is not competing with Arcron for budget; it inherits the
pool Arcron's own call contributed to. Reach for `algopy.ensure_budget` only
if you exceed that.

### Assume it may run more than once, and in bursts

Scheduling advances from the *scheduled* round, not the round execution
happened, so an upkeep unattended for N intervals stays due N times and
catches up **one interval per call**. After an outage your hook may be called
several times in quick succession.

Make it idempotent, or make each call's effect depend only on current state.
If replaying missed periods is wrong for your use case — a prize draw, say —
say so on
[issue #7](https://github.com/CorvidLabs/archon/issues/7), which is deciding
that policy and needs concrete cases.

### Rounds are not a clock

A cadence is a round count. A round is ~2.8 s nominally, less in practice
(TestNet measured 2.66 s), so "daily" means "every ~30,857 rounds" and slides
against the calendar — about a day and a half over thirty cycles. Arcron
promises "not before this round", never "at 09:00". If a wall-clock moment
matters, have the *hook* check the time and no-op when early, and schedule it
often enough to catch the window.

Related: do not set a cadence so tight that ordinary keeper lateness looks
like a real condition. Arcron's minimum interval is 10 rounds, and the demos
here use a floor of 30 rounds for anything that treats lateness as a signal.

## The pull pattern

**Do the accounting in the scheduled call. Let counterparties collect in their
own transactions.**

If you take one thing from this guide, take that. Every demo in this repo is
shaped by it, and not for stylistic reasons:

**Resource availability.** An Arcron inner call reaches only what the keeper's
own transaction makes available — and nothing tells a keeper what your hook
needs. A scheduled call that tries to pay an arbitrary account, read a
balance, or call another app **fails**, because those resources are not
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

Worked examples: `smart_contracts/rain/` pulls a *resource* this way — the
scheduled draw fixes a beacon round and a participant supplies the beacon
reference when resolving. `smart_contracts/treasury/` credits recipients who
then claim. `smart_contracts/deadman/` allocates to a beneficiary who claims.

## Reaching resources your hook cannot name

A scheduled call can only touch what the executing transaction makes
available. Arcron stores no foreign arrays — but it does not need to. Resource
availability supplied on the *keeper's* transaction reaches two levels down:
to Arcron's inner call, and to your own inner transactions from it. Measured
in [#24](https://github.com/CorvidLabs/archon/issues/24), across payments,
asset transfers, balance reads, holding reads and inner app calls.

The budget is **8 references per transaction**. Arcron spends two — the upkeep
box and your app — leaving **6** in any mix of accounts, assets and apps.

What is missing is discovery: nothing on chain tells a keeper which resources
your upkeep needs. The convention Arcron proposes is a readonly view a keeper
simulates before executing:

```
resources()(address[],uint64[],uint64[])
```

Return the accounts, assets and apps your hook will reach for. A keeper
simulates it for free, attaches what it names, and executes. A target that
does not implement it fails the simulate and the keeper attaches nothing,
which is exactly today's behaviour — nothing existing breaks.

Three things make this safe, and worth knowing before you rely on it:

- **References grant availability, not authority.** Your hook already decides
  what it touches, and `call_args` is still fixed by whoever registered the
  upkeep.
- **A wrong answer is free.** A rejected execution costs a keeper nothing, so
  it can simulate and skip.
- **Six is the ceiling**, and `resources()` lets a keeper learn that before
  spending anything rather than in a rejection.

Not enforced by the contract, and not yet read by `scripts/keeper_bot.py` —
this is a convention with no user yet, and a convention with no user is one
that gets the details wrong. If you need it, say so on
[#8](https://github.com/CorvidLabs/archon/issues/8) and it becomes a keeper
feature rather than a paragraph.

## Calls with arguments

An execution carries up to **three app args, counting the selector** — enough
for an ARC-4 method of arity two:

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
changes between runs, it has to derive it — from its own state, from a
resource it pulls, or from the round. Arcron will not supply it, by design.

## An ASA bonus

An upkeep can pay a bonus in any asset **on top of** its ALGO fee, never
instead of it. That is deliberate: a keeper's real costs are ALGO, so keeping
the ALGO fee mandatory is what lets the contract guarantee profitability
without anyone having to price your token.

```
register(..., fee_asset=<asset id>, asset_fee=<base units>)
opt_in_asset(mbr_payment, upkeep_id, asset)   # 0.1 ALGO, permanent
top_up_asset(upkeep_id, asset_funding)
```

Two things to know:

- **The app must opt in before it can hold the asset**, which costs 0.1 ALGO
  of minimum balance permanently. There is no opt-out, so the deposit does not
  come back.
- **A keeper that is not opted in to your asset still executes**, takes the
  ALGO fee, and forfeits the bonus — which stays in your escrow and comes back
  on cancel. Your bonus reaches the keepers who want it and costs you nothing
  with the ones who do not.

## Funding and operations

An upkeep is funded escrow. Executions are paid from it:

```
funded runs = balance / fee_per_execution        # with no fee ceiling
funded runs = balance / fee_cap                  # with one, this is the number
```

At the 4,000 µALGO minimum fee, 1 ALGO buys 250 executions — about 10 days of
an hourly cadence, or 8 months of a daily one. Registering also costs box MBR
(`2,500 + 400 × (139 + len(encoded call_args))` µALGO, so 62,100 for a bare
4-byte selector), and **that comes back in full when you cancel**.

If you set a fee ceiling, budget against the ceiling — and read it as the
price you will *usually* pay, not the worst case.

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

The escrow also has to cover the ceiling for the upkeep to stay executable —
an upkeep with a 4,000 µALGO fee and a 12,000 µALGO ceiling goes dormant below
12,000, not below 4,000. `register` enforces one capped run up front, so a
capped upkeep can never be unexecutable from birth, but later runs can draw it
below that line.

- **Anyone can `top_up`.** Funding is permissionless, so a counterparty with an
  interest in your schedule running can pay for it. Only the creator can
  `cancel`.
- **Running dry is silent.** The upkeep goes dormant — no keeper can execute
  it — and resumes the moment someone tops it up. Nothing announces it.
- **Notice before it happens:** `poetry run python -m scripts.keeper_bot --check`
  reports an upkeep whose escrow has fallen below one fee as **starved** and
  exits non-zero, so it drops straight into cron.
- **Cancel when done.** A hook that has finished its job for good — a fired
  dead man's switch, a published embargo — keeps being called and keeps paying
  keepers to do nothing until you cancel.

## Testing it

Prove it on LocalNet before TestNet, and prove it on TestNet before you rely
on it. `scripts/keeper_e2e.py` is the reference for what that looks like; each
demo script here (`embargo_demo.py`, `rain_demo.py`, `deadman_demo.py`,
`watchdog_demo.py`, `treasury_demo.py`) is a smaller worked version.

**Unit tests will not catch the things that break integrations.**
`algorand-python-testing` mocks *record* inner app calls without executing
them, and do not enforce minimum balances. So a hook that fails when actually
invoked, or an app that cannot pay out what it owes, passes its unit tests and
fails on a real chain. Anything depending on either belongs in a LocalNet
end-to-end test.

Start here:

```bash
algokit localnet start
fledge lanes run local          # the whole suite, including five worked demos
```

Then register against the live TestNet keeper app `769802474` with
`examples/register_upkeep.py`.
