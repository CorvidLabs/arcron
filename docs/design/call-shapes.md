# Design: call shapes — multi-argument and foreign arrays

**Status:** proposed, for review
**Issue:** [#8](https://github.com/CorvidLabs/archon/issues/8)
**Depends on:** [#24](https://github.com/CorvidLabs/archon/issues/24) (measured), and shares a struct with [#7 and #14](scheduling-and-fees.md)
**Reproduce every number here:** `poetry run python -m scripts.spike_multiarg --network localnet`

Archon executes exactly one call shape: a NoOp app call carrying a single app
arg. An ARC-4 method with arguments of its own needs the selector *and* each
argument in an app arg of its own, so today **only zero-argument hooks are
reachable**. Every demo in this repo is zero-argument because of that, not by
preference.

Issue #8 asks for two things at once — multi-argument calls and foreign
arrays. They turn out to be different problems with different answers, so this
splits them.

## The foreign-array half is already solved, and not by the struct

[#24](https://github.com/CorvidLabs/archon/issues/24) measured what an
Archon-triggered inner call can reach. Resource availability supplied on the
*keeper's* transaction flows two levels down: to Archon's inner call, and to
the target's own inner transactions. Five patterns — inner payment, inner
asset transfer, balance read, holding read, inner app call — all fail bare and
all succeed when the keeper attaches references.

So the capability #8 wants **already exists**, without a struct change, without
a call-shape change, and without touching the trust model: the creator still
fixes the call at registration, and the keeper supplies availability, never
data. (That line is what separates this issue from
[#22](https://github.com/CorvidLabs/archon/issues/22), which is out of 1.0.)

What is missing is **discovery**. Nothing on chain tells a keeper which
resources an upkeep needs, so a keeper has to know out of band. That is the
whole remaining problem, and storing a resource list in the `Upkeep` struct is
a bad way to solve it — see §3.

## The obvious multi-argument implementation is silently wrong

The natural code builds the args array in a loop:

```python
txn = itxn.ApplicationCall(app_id=target, on_completion=OnCompleteAction.NoOp)
for index in urange(args.length):
    txn.set(app_args=(args[index].native,))
txn.submit()
```

This **compiles**. puyapy emits one warning — `Variable
txn%%param_ApplicationArgs_idx_0 potentially used before assignment` — and no
error. It is wrong: Puya models `app_args` as compile-time-numbered slots and
hoists the whole inner transaction out of the loop, so the generated TEAL
contains a single `itxn_field ApplicationArgs`, set after the loop has ended.
Only the **last** assignment survives.

Part B of the spike proves it on chain rather than by reading TEAL. Calling a
zero-argument probe through that contract:

| Arguments passed | Result |
|---|---|
| junk, then the real selector | **accepted** — the probe ran |
| the real selector, then junk | **rejected** — `err opcode executed` |

A construction that drops every argument but the last, on a contract that can
never be upgraded, is worth knowing about before writing it. The fan-out below
is not a stylistic preference; it is the only correct construction.

## What the fan-out costs

The correct form is one static branch per argument count, and each branch
builds a larger tuple than the last, so program size grows super-linearly.
Part A compiles the real keeper at each ceiling:

| Fan-out ceiling | Approval program | Pages | vs today |
|---|---|---|---|
| today (1 blob) | 729 B | 1 | — |
| 1 | 818 B | 1 | +89 |
| 2 | 921 B | 1 | +192 |
| 3 | 1,053 B | 1 | +324 |
| **4** | **1,221 B** | **1** | **+492** |
| 6 | 1,664 B | 1 | +935 |
| 8 | 2,246 B | **2** | +1,517 |
| 16 (`MaxAppArgs`) | 6,014 B | 3 | +5,285 |

A TEAL program page is 2,048 bytes, and each extra page costs the deployer
another 100,000 µALGO of app minimum balance, permanently. **The ceiling that
matters is 6-to-8, not the protocol's 16** — program size binds long before
`MaxAppArgs` does.

Part C prices the runtime, on LocalNet, against the same probe:

| Case | Args | Box bytes | Box MBR | Opcode budget handed to the target |
|---|---|---|---|---|
| today's keeper, zero-arg hook | 1 | 97 | 41,300 | **1,250** |
| multi-arg keeper, zero-arg hook | 1 | 101 | 42,900 | **1,216** |
| multi-arg keeper, `absorb(uint64,string)` | 3 | 125 | 52,500 | **1,139** |

Decoding `byte[][]` costs the target **34 opcodes** for the array machinery
and about **38 more per additional argument** — under 9% of the pool for a
three-argument call, against the 1,250 measured in
[#26](https://github.com/CorvidLabs/archon/issues/26). The encoding costs
**4 bytes per argument** (a 2-byte offset and a 2-byte length), which is
1,600 µALGO of box MBR each.

And `absorb` received `number=7777`, `text='archon'` — a hook with real
arguments, executed by a keeper, which today's contract cannot reach at all.

## Proposal

### 1. `call_args: byte[][]` replaces `call_data: byte[]`

One field, same slot, no net field-count change. Element 0 is whatever app
arg 0 should be — the ARC-4 selector for an ARC-4 target — and Archon stays
agnostic about what the bytes mean, exactly as it is today.

### 2. A fan-out ceiling of 4, fixed last

Four covers the selector plus three ABI arguments. It is also more general
than it looks: a target you control can accept **any** argument list by
declaring it as a single ARC-4 struct or tuple argument — the same trick ARC-4
itself uses at arg 15 — so arity 2 is already universal for a cooperating
target. The ceiling only buys reach into targets you do *not* control.

Four leaves 827 bytes of page headroom for #7, #14 and #9, which also add
code. Six fits today (384 bytes spare) and might not once they land. Since the
ceiling is a single integer in a repetitive `if`/`elif` chain, it is the
cheapest parameter to re-tune during implementation: **fix it last, from
measured size with the rest of the batch in**, and do not let it push the
program to a second page.

### 3. Foreign arrays stay out of the struct; resource discovery is a convention

Do not store a resource list. Instead, publish a convention: a target that
needs resources exposes a readonly

```
resources()(address[],uint64[],uint64[])
```

which the keeper simulates before executing and attaches — up to the 6 slots
#24 measured — to its own `execute` transaction. A target that does not
implement it fails the simulate and the keeper attaches nothing, which is
exactly today's behaviour, so nothing existing breaks.

Storing the list instead would cost 32 bytes per address of box MBR, burn
struct budget in a contract that cannot be upgraded, cap out at 6 anyway, and
**freeze the list at registration** — a treasury whose recipients change would
need cancel-and-re-register. It would buy nothing in exchange, because
availability is granted by the keeper's transaction either way; a stored list
would only ever be documentation the keeper reads. The view is that same
documentation, minus the MBR and minus the freeze, and it stays correct when
the target's needs change.

Three properties make the convention safe:

- **References grant availability, not authority.** The target already decides
  what it touches, and the call is still fixed by the creator.
- **A wrong answer is free.** [#13](https://github.com/CorvidLabs/archon/issues/13)
  measured that a rejected execution costs a keeper nothing — the transaction
  never commits. A keeper can simulate and skip.
- **The 6-reference ceiling still binds**, and `resources()` lets a keeper
  learn that *before* spending anything, rather than in a rejection.

The honest cost: nothing enforces it, it adds an algod round-trip per due
upkeep, and it is an Archon convention rather than a standard anyone else
honours. An ARC is the eventual answer; not in 1.0.

This is the "written decision that it is out of scope, with the reasoning
recorded" that #8's acceptance criteria allows for the foreign-array half.

### 4. `on_completion` stays pinned to NoOp

As #8 itself recommends. Arbitrary `OnCompletion` opens delete and update
paths on the target, reachable by anyone who can execute the upkeep. That
needs far more thought than 1.0 has room for, and unpinning it later is a
struct change we would be making anyway.

### 5. The MBR formula, re-derived

Undercharging MBR is the bug that stranded the previous TestNet deployment, so
this is stated explicitly and asserted from the box rather than from the
formula.

The 2-byte length prefix that `byte[]` carries in the box tail moves *inside*
the array encoding, so the fixed component drops from 93 to **91**:

```
tail  = 2 + 4k + Σ len(arg_i)        # count, then an offset and a length each
box   = 9 (name) + 82 (head) + tail
MBR   = 2_500 + 400 × box
      = 2_500 + 400 × (91 + len(encoded call_args))
```

Verified against the real boxes in Part C: 101 bytes and 42,900 µALGO for a
bare selector, 125 bytes and 52,500 µALGO for `absorb(uint64,string)`.

## Cost

Against today, for a one-argument upkeep: **+1,600 µALGO** of box MBR (+4%),
**34 opcodes** off the target's budget, **+492 bytes** of program. Against
today *with #7 and #14 also landed*, a one-argument upkeep costs 52,500 µALGO
— up from 41,300, or **+27%** on the entry price of an upkeep. It is a
deposit, refunded on cancel, not a fee.

Nothing here needs a second program page or a second box.

## What has to move together

The five-file lockstep from [#31](https://github.com/CorvidLabs/archon/issues/31),
plus what the shape change touches beyond it:

1. `smart_contracts/keeper/contract.py` — struct, `register` bounds, the `execute` fan-out, `BOX_MBR_FIXED` 93 → 91
2. `scripts/keeper_bot.py::_decode_upkeep` — decode `byte[][]`, not `byte[]`
3. `web/src/app/core/upkeep.ts` — its TypeScript twin
4. `tests/test_keeper_bot.py` and `web/src/app/core/upkeep.test.ts` — the pinned box vectors
5. `specs/keeper/` — Public API, requirements, testing, Change Log

Beyond the struct:

- **The console's register form** stops being a single hex field. It needs a
  method signature and typed argument values, ARC-4 encoded in the browser —
  the largest single piece of work in this issue, and the one most likely to
  be underestimated.
- **`scripts/keeper_e2e.py`** gains the case #8 asks for: a target method that
  takes real arguments, executed by a keeper, with the arguments checked on
  the target rather than inferred from success.
- **The "base MBR only" regression** must be re-run, because the fixed
  component changes. That test is the reason to trust the new formula.
- **`docs/integrating.md`** gains the `resources()` convention and a revised
  budget figure — a target now sees 1,216 rather than 1,250.

## Open questions for review

1. **Should zero arguments be allowed?** With `byte[][]`, an empty array is a
   bare NoOp app call, which some targets do implement. It costs one more
   branch. Against: `register` currently asserts non-empty call data, and a
   bare call is easy to register by mistake. Recommendation: allow it, and
   have the console require an explicit choice rather than defaulting to it.
2. **Is 4 the right ceiling?** Argued above, but it is genuinely a sizing
   decision that should be re-measured once #7, #14 and #9 are written. The
   failure mode is quiet: a second page costs 100,000 µALGO forever.
3. **Should `resources()` be part of 1.0 at all, or just documented?** The
   keeper bot change is small; the argument for deferring is that no demo
   needs it yet, and a convention with no user is a convention that gets the
   details wrong. Recommendation: document it in `docs/integrating.md` for
   1.0, implement the keeper-side simulate only when a real target wants it.
4. **Does `MAX_CALL_DATA` stay 1,024?** It becomes a cap on the encoded
   payload. The AVM's own `MaxAppTotalArgLen` is 2,048, so 1,024 stays
   conservative — but it is now shared across every argument, and a 1,024-byte
   argument list is a large box. Keeping the number and changing what it
   measures is the least surprising option.

## Recommendation

Take the multi-argument half; leave the foreign-array half as a documented
convention. That keeps 1.0's only struct change here to one field replacing
one field, and it removes the reason #8 looked like the expensive item in the
batch.

Implement with #7 and #14 in a single deployment, in this order: contract and
spec, then both decoders and both pinned vectors in the same commit, then the
console form, then the e2e case. Deploy last, and only after `fledge lanes run
local` is green on all of it.
