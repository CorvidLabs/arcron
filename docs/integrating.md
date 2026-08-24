# Hooking your contract into Archon

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

Archon calls it with exactly one application argument — the method selector —
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

Archon's inner call comes from the keeper application's account, so that is the
sender to check. Derive the address off-chain with
`algosdk.logic.get_application_address(app_id)`, or in Python:

```python
from algosdk.logic import get_application_address
get_application_address(769802474)
```

**Leave it permissionless** — anyone may call the hook, as `Pulse` and the
timed-release demo do. Correct when the hook is idempotent and its timing is
the only thing that matters: it means your contract still works if Archon
disappears, and anybody can push it along. It is the wrong default when the
hook's effects depend on *when* it runs, because then anyone can choose the
moment.

Note what restricting does **not** buy you: the keeper app is permissionless,
so restricting to it means "only via a paid upkeep", not "only by someone I
trust".

## Making it robust

This is the part integrations get wrong.

### Your hook is called whether or not there is work

Archon calls on every cadence, forever. The no-op path is the common path, so
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
[Operating a bot](archon.md#operating-a-bot)) — but it costs *you* the
schedule. Fail soft. Record the problem in state and return.

### You have more opcode budget than you think

Opcode budget pools across the app calls in a group, and an Archon execution
contains two — Archon's own call and the inner call to you. Measured on
LocalNet with `smart_contracts/resource_probe/`:

| Called | Budget remaining at method entry |
|--------|----------------------------------|
| Directly, as a plain app call | **684** |
| Through an Archon upkeep | **1,250** |

So a hook driven by Archon has roughly **1.8× the budget** of the same method
called directly. It is not competing with Archon for budget; it inherits the
pool Archon's own call contributed to. Reach for `algopy.ensure_budget` only
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
against the calendar — about a day and a half over thirty cycles. Archon
promises "not before this round", never "at 09:00". If a wall-clock moment
matters, have the *hook* check the time and no-op when early, and schedule it
often enough to catch the window.

Related: do not set a cadence so tight that ordinary keeper lateness looks
like a real condition. Archon's minimum interval is 10 rounds, and the demos
here use a floor of 30 rounds for anything that treats lateness as a signal.

## The pull pattern

**Do the accounting in the scheduled call. Let counterparties collect in their
own transactions.**

If you take one thing from this guide, take that. Every demo in this repo is
shaped by it, and not for stylistic reasons:

**Resource availability.** An Archon inner call reaches only what the keeper's
own transaction makes available — and nothing tells a keeper what your hook
needs. A scheduled call that tries to pay an arbitrary account, read a
balance, or call another app **fails**, because those resources are not
available to it. (Measured; the table is in
[archon.md](archon.md#what-an-archon-triggered-call-can-reach).)

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

## Funding and operations

An upkeep is funded escrow. Executions are paid from it:

```
funded runs = balance / fee_per_execution
```

At the 4,000 µALGO minimum fee, 1 ALGO buys 250 executions — about 10 days of
an hourly cadence, or 8 months of a daily one. Registering also costs box MBR
(`2,500 + 400 × (93 + len(call_data))` µALGO, so 41,300 for a 4-byte
selector), and **that comes back in full when you cancel**.

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
