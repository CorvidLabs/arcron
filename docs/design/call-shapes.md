# Design: call shapes (multi-argument and foreign arrays)

**Status:** implemented at a fan-out ceiling of 3. The foreign-array half needed nothing at all; see the correction below.
**Issue:** [#8](https://github.com/CorvidLabs/arcron/issues/8)
**Depends on:** [#24](https://github.com/CorvidLabs/arcron/issues/24) (measured), and shares a struct with [#7 and #14](scheduling-and-fees.md)
**Reproduce every number here:** `poetry run python -m scripts.spike_multiarg --network localnet`, and the batch table with `poetry run python -m scripts.spike_asa_fee --network localnet`

Arcron executes exactly one call shape: a NoOp app call carrying a single app
arg. An ARC-4 method with arguments of its own needs the selector *and* each
argument in an app arg of its own, so today only zero-argument hooks are
reachable. Every demo in this repo is zero-argument because of that, not by
preference.

Issue #8 asks for two things at once: multi-argument calls and foreign arrays.
They turn out to be different problems with different answers, so this splits
them.

## The foreign-array half is already solved, and not by the struct

[#24](https://github.com/CorvidLabs/arcron/issues/24) measured what an
Arcron-triggered inner call can reach. Resource availability supplied on the
*keeper's* transaction flows two levels down: to Arcron's inner call, and to
the target's own inner transactions. Five patterns (inner payment, inner asset
transfer, balance read, holding read, inner app call) all fail bare and all
succeed when the keeper attaches references.

So the capability #8 wants already exists, without a struct change, without
a call-shape change, and without touching the trust model: the creator still
fixes the call at registration, and the keeper supplies availability, never
data. (That line is what separates this issue from
[#22](https://github.com/CorvidLabs/arcron/issues/22), which is out of 1.0.)

What is missing is **discovery**. Nothing on chain tells a keeper which
resources an upkeep needs, so a keeper has to know out of band. That is the
whole remaining problem, and storing a resource list in the `Upkeep` struct is
a bad way to solve it (see §3).

## The obvious multi-argument implementation is silently wrong

The natural code builds the args array in a loop:

```python
txn = itxn.ApplicationCall(app_id=target, on_completion=OnCompleteAction.NoOp)
for index in urange(args.length):
    txn.set(app_args=(args[index].native,))
txn.submit()
```

This **compiles**. puyapy emits one warning (`Variable
txn%%param_ApplicationArgs_idx_0 potentially used before assignment`) and no
error. It is wrong: Puya models `app_args` as compile-time-numbered slots and
hoists the whole inner transaction out of the loop, so the generated TEAL
contains a single `itxn_field ApplicationArgs`, set after the loop has ended.
Only the last assignment survives.

Part B of the spike proves it on chain rather than by reading TEAL. Calling a
zero-argument probe through that contract:

| Arguments passed | Result |
|---|---|
| junk, then the real selector | **accepted** (the probe ran) |
| the real selector, then junk | **rejected** (`err opcode executed`) |

A construction that drops every argument but the last, on a contract whose
call shape is fixed for every deployment that has frozen, is worth knowing
about before writing it. The fan-out below
is not a stylistic preference; it is the only correct construction.

## What the fan-out costs

The correct form is one static branch per argument count, and each branch
builds a larger tuple than the last, so program size grows super-linearly.
Part A compiles the real keeper at each ceiling:

| Fan-out ceiling | Approval program | Pages | vs today |
|---|---|---|---|
| today (1 blob) | 966 B | 1 | n/a |
| 1 | 1,052 B | 1 | +86 |
| 2 | 1,149 B | 1 | +183 |
| **3** | **1,285 B** | **1** | **+319** |
| 4 | 1,458 B | 1 | +492 |
| 6 | 1,907 B | 1 | +941 |
| 8 | 2,500 B | **2** | +1,534 |
| 16 (`MaxAppArgs`) | 6,295 B | 4 | +5,329 |

A TEAL program page is 2,048 bytes, and each extra page costs the deployer
another 100,000 µALGO of app minimum balance, permanently. **The ceiling that
matters is 6-to-8, not the protocol's 16.** Program size binds long before
`MaxAppArgs` does. And with the rest of the batch stacked on, it binds sooner
still: §2 prices the whole thing.

Part C prices the runtime, on LocalNet, against the same probe:

| Case | Args | Box bytes | Box MBR | Opcode budget handed to the target |
|---|---|---|---|---|
| today's keeper, zero-arg hook | 1 | 121 | 50,900 | **1,210** |
| multi-arg keeper, zero-arg hook | 1 | 125 | 52,500 | **1,179** |
| multi-arg keeper, `absorb(uint64,string)` | 3 | 149 | 62,100 | **1,104** |

Decoding `byte[][]` costs the target 31 opcodes for the array machinery and
about 37 more per additional argument, which is under 9% of the pool for a
three-argument call. The encoding costs 4 bytes per argument (a 2-byte offset
and a 2-byte length), or 1,600 µALGO of box MBR each.

(The 1,210 baseline is itself down from the 1,250 measured in
[#26](https://github.com/CorvidLabs/arcron/issues/26): #7 and #14's escalation
arithmetic costs the target about 40 opcodes.)

And `absorb` received `number=7777`, `text='arcron'`. That is a hook with real
arguments, executed by a keeper, which today's contract cannot reach at all.

## Proposal

### 1. `call_args: byte[][]` replaces `call_data: byte[]`

One field, same slot, no net field-count change. Element 0 is whatever app
arg 0 should be (the ARC-4 selector for an ARC-4 target), and Arcron stays
agnostic about what the bytes mean, exactly as it is today.

### 2. A fan-out ceiling of 3, decided

Three covers the selector plus two ABI arguments. It is also more general than
it looks: a target you control can accept *any* argument list by declaring it
as a single ARC-4 struct or tuple argument, the same trick ARC-4 itself uses at
arg 15, so arity 2 is already universal for a cooperating target. The ceiling
only buys reach into targets you do *not* control.

Page headroom is the binding constraint, and now that #7 and #14 are
implemented the batch can be compiled rather than estimated
(`poetry run python -m scripts.spike_asa_fee`):

| Contract | Approval | Pages | Headroom |
|---|---|---|---|
| before the batch | 729 B | 1 | 1,319 |
| the contract today, with #7 + #14 | 966 B | 1 | 1,082 |
| + #9 (ASA bonus) | 1,483 B | 1 | 565 |
| + #8 at ceiling 3 | 1,285 B | 1 | 763 |
| **the whole 1.0 batch, ceiling 3** | **1,814 B** | **1** | **234** |
| the same batch **as built** | 1,932 B | 1 | 116 |
| **the keeper today**, plus `update` + `freeze` | **2,008 B** | **1** | **40** |

With everything else fixed, the ceiling is the only dial left, so it is the
one parameter the whole batch's fit depends on:

| Fan-out ceiling | Whole batch | Spare |
|---|---|---|
| 2 | 1,675 B | 373 |
| **3** | **1,814 B** | **234** |
| 4 | 1,990 B | 58 |
| 6 | 2,453 B | **second page** |

Four was the earlier recommendation, made against an estimate of #7 and #14
that turned out 79 bytes light. Compiled, it leaves 58 bytes. That is one added
assertion away from a second program page and another 100,000 µALGO of app
minimum balance, forever, on a contract nobody can patch once it is frozen.
Governance has since taken 76 bytes of its own, so ceiling 4 would not fit on
one page today at all.

**Three is the setting.** It covers a selector plus two ABI arguments, it
covers *any* arity for a target that packs its arguments into one struct, and
234 bytes is enough room to fix something during implementation without
re-opening the decision.

### 3. Foreign arrays stay out of the struct, and discovery needs nothing either

Do not store a resource list. #24 measured that availability supplied on the
keeper's transaction already reaches two levels down, so the capability exists
without a struct field.

This section originally went further and proposed a convention: a readonly
`resources()` view for a target to declare what it needs, which a keeper would
simulate and attach. **That was unnecessary, and it is withdrawn.**

Simulation already answers the question. A keeper simulates the call, the node
reports the resources it *would* have required, and the keeper attaches those
and sends. `algokit-utils` does it by default, in `populate_app_call_resources`.
Measured on LocalNet against a target reaching for an account no argument
names: a raw transaction with no references fails with `unavailable Account`,
and the identical call through a keeper that simulates first succeeds.

That is strictly better than a convention on three counts. It needs nothing
from the target, so it works for apps written before Arcron existed. It stays
correct when a target's needs change between runs, which a declared list
cannot. And it has no adoption problem. A convention with no users is a
convention whose details are wrong.

The lesson is worth keeping rather than quietly deleting: the design reasoned
its way to a plausible mechanism without first checking whether the platform
already provided one. It did.

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
the array encoding, so the fixed component drops by two, from the 117 of the
single-argument struct to 115 against the 106-byte head this spike measured:

```
tail  = 2 + 4k + Σ len(arg_i)        # count, then an offset and a length each
box   = 9 (name) + 106 (head) + tail
MBR   = 2_500 + 400 × box
      = 2_500 + 400 × (115 + len(encoded call_args))
```

Verified against the real boxes in Part C: 125 bytes and 52,500 µALGO for a
bare selector, 149 bytes and 62,100 µALGO for `absorb(uint64,string)`.

**As shipped the fixed component is 139, not 115.** The ASA-fee fields in the
same release added 24 bytes to the head, taking it from 106 to 130, so
`BOX_MBR_FIXED` is `2_500 + 400 × 139` and a bare selector's box costs 62,100
µALGO rather than the 52,500 measured here. The shape of the formula is
unchanged; only the head is bigger. `smart_contracts/keeper/contract.py` is
the authority.

## Cost

Against the contract as it stands, for a one-argument upkeep: +1,600 µALGO of
box MBR (+3%), 31 opcodes off the target's budget, +319 bytes of program. A
one-argument upkeep costs 52,500 µALGO to register, up from the 41,300 of the
pre-batch contract. That is **+27%** on the entry price of an upkeep, of which
#7 and #14 are most. It is a deposit, refunded on cancel, not a fee.

Nothing here needs a second program page or a second box.

## What has to move together

The five-file lockstep from [#31](https://github.com/CorvidLabs/arcron/issues/31),
plus what the shape change touches beyond it:

1. `smart_contracts/keeper/contract.py`: struct, `register` bounds, the `execute` fan-out, `BOX_MBR_FIXED` 93 → 91
2. `scripts/keeper_bot.py::_decode_upkeep`: decode `byte[][]`, not `byte[]`
3. `js/src/upkeep.ts`, its TypeScript twin
4. `tests/test_keeper_bot.py` and `js/test/upkeep.test.ts`: the pinned box vectors
5. `specs/keeper/`: Public API, requirements, testing, Change Log

Beyond the struct:

- **The console's register form** stops being a single hex field. It needs a
  method signature and typed argument values, ARC-4 encoded in the browser.
  That is the largest single piece of work in this issue, and the one most
  likely to be underestimated.
- **`scripts/keeper_e2e.py`** gains the case #8 asks for: a target method that
  takes real arguments, executed by a keeper, with the arguments checked on
  the target rather than inferred from success.
- **The "base MBR only" regression** must be re-run, because the fixed
  component changes. That test is the reason to trust the new formula.
- **`docs/integrating.md`** gains the simulation-discovery note and a revised
  budget figure. A target now sees 1,216 rather than 1,250.

## Open questions for review

1. **Should zero arguments be allowed?** With `byte[][]`, an empty array is a
   bare NoOp app call, which some targets do implement. It costs one more
   branch. Against: `register` currently asserts non-empty call data, and a
   bare call is easy to register by mistake. Recommendation: allow it, and
   have the console require an explicit choice rather than defaulting to it.
2. ~~**Is 3 the right ceiling?**~~ **Decided: 3.** #7 and #14 are written and
   measured, so this was no longer an estimate. The whole batch is 1,814 bytes
   at 3 and 1,990 at 4, against a 2,048-byte page. The failure mode is quiet,
   so the batch takes the setting with room in it.
3. ~~**Should `resources()` be part of 1.0 at all?**~~ **Withdrawn.**
   Simulation already reports what a call needs, for every target, with no
   cooperation. There is nothing to standardise.
4. **Does `MAX_CALL_DATA` stay 1,024?** It becomes a cap on the encoded
   payload. The AVM's own `MaxAppTotalArgLen` is 2,048, so 1,024 stays
   conservative, but it is now shared across every argument, and a 1,024-byte
   argument list is a large box. Keeping the number and changing what it
   measures is the least surprising option.

## Recommendation

Take the multi-argument half at a **fan-out ceiling of 3**. Take nothing for
the foreign-array half, because it needs nothing: simulation already discovers
what an inner call touches, and `algokit-utils` populates it by default. The
`resources()` convention this document originally recommended is withdrawn;
see the correction in "The foreign-array half is already solved".

That keeps 1.0's only struct change here to one field replacing one field, and
it removes the reason #8 looked like the expensive item in the batch. As
built, the whole batch compiled to 1,932 bytes, leaving 116 inside one 2,048
byte program page. Adding `update`, `freeze` and the `frozen` global took that
to **2,008 bytes with 40 spare**: governance consumed the difference, and 40
bytes is not room for another call shape. The live number is one command away,
so measure rather than trusting the figure above:

```bash
poetry run python -c "import json,base64,pathlib; s=json.loads(sorted(pathlib.Path('smart_contracts/artifacts/keeper').glob('*.arc56.json'))[0].read_text()); n=len(base64.b64decode(s['byteCode']['approval'])); print(n, 2048-n)"
```

#7 and #14 are already implemented, so the remaining order is: contract and
spec, then both decoders and both pinned vectors in the same commit, then the
console form, then the e2e case. Deploy once, with #9 if it is agreed, and
only after `fledge lanes run local` is green on all of it.
