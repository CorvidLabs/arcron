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

## Installing

Published to **GitHub Packages**, not npmjs.org, so `npm install
@corvidlabs/arcron` on its own will 404. Two lines of `.npmrc` in the consuming
project:

```
@corvidlabs:registry=https://npm.pkg.github.com
//npm.pkg.github.com/:_authToken=${NODE_AUTH_TOKEN}
```

```sh
npm install @corvidlabs/arcron@alpha   # or: bun add @corvidlabs/arcron@alpha
```

The token is not optional and not a mistake in these instructions: GitHub
Packages requires authentication to *read* an npm package even when the package
and its repository are public. In GitHub Actions the built-in `GITHUB_TOKEN`
with `permissions: { packages: read }` is enough and needs no secret. Outside
Actions it is a classic personal access token with `read:packages`; fine-grained
tokens are not supported by this registry.

**There is no tokenless install path, and depending on the repository directly
is not one.** The obvious workaround does not work, because this package lives
in `js/` and the repository root is a different, private package:

```console
$ bun add github:CorvidLabs/arcron
installed arcron-workspace@github:CorvidLabs/arcron#38703be
```

That is the Bun workspace root — wrong name, no `exports` map, and
`@corvidlabs/arcron` still unresolvable. Neither npm nor Bun supports pointing
a git dependency at a subdirectory. So the options are a token, or vendoring
the handful of files you need, which is what
[`../docs/design/split.md`](../docs/design/split.md) recommends for the first
cut of `arcron-rain`.

This package ships **raw TypeScript**. That is deliberate — it is the same
source the tests in this repository pin byte-for-byte, with no build step in
between — but it means the consumer must be a bundler: Bun, Vite, esbuild,
Angular, Next. Plain `node` cannot import it at any version, because Node
refuses to strip types from files under `node_modules`
(`ERR_UNSUPPORTED_NODE_MODULES_TYPE_STRIPPING`). Type resolution needs no
`types` field and works under `bundler`, `node16` and `nodenext`; the `.ts`
files are their own declarations. `algosdk` v3 is a peer dependency you install
yourself.

The version is a prerelease on the `alpha` dist-tag, and it tracks the
deployment ladder in [`../docs/releases.md`](../docs/releases.md) rather than
running ahead of it: the TestNet contract is at `alpha-3` and its creator can
still replace the programs, so the box layout this decoder is pinned to is not
yet frozen. `1.0.0` is reserved for the point at which it is.

`./rain`, `./rain-abi` and `./rain-txns` are slated to move to
`CorvidLabs/arcron-rain` — see
[`../docs/design/split.md`](../docs/design/split.md). Depend on them knowing
they will leave this package.

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
