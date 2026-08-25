# @corvidlabs/arcron

Reading the [Arcron](https://github.com/CorvidLabs/arcron) upkeep registry and
building the transactions that change it. No UI framework, no backend, no
indexer. Everything here comes from box state any algod will serve.

```ts
import { decodeUpkeep, effectiveFee, upkeepBoxName } from '@corvidlabs/arcron';

const raw = await algod.getApplicationBoxByName(appId, upkeepBoxName(0n)).do();
const upkeep = decodeUpkeep(0n, raw.value);
console.log(effectiveFee(upkeep, currentRound)); // what it pays right now
```

## Why this lives in the contract's repository

The decoder here is the twin of `scripts/keeper_bot.py::_decode_upkeep`, and
both are pinned to the **same recorded box, byte for byte**, by tests that run
in the same CI as the contract itself. `keeper-abi.test.ts` checks every method
signature against the compiled ARC-56 artifact in the same tree.

That is not ceremony. During development the TypeScript encoder had the ARC-4
offset base wrong. Offsets are measured from after the array count, not from
its start, and it produced a plausible-looking encoding that decoded to
garbage. The only thing that caught it was the byte-for-byte comparison with
the Python implementation. In a separate repository that ships.

So: consume this as a dependency rather than copying it, and if you port it,
port the vector too.

## What it does not do

No signing and no wallet handling. `keeper-txns.ts` builds an
`AtomicTransactionComposer` group and takes a `TransactionSigner`, so how a
user authorises anything is entirely yours.
