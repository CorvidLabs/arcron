# Security

## Reporting a vulnerability

Report privately, not as a public issue: **[open a draft security advisory](https://github.com/CorvidLabs/arcron/security/advisories/new)**.

We will acknowledge within **72 hours** and give an assessment within **7
days**. If a report is valid we will agree a disclosure timeline with you and
credit you unless you prefer otherwise.

Please include what you need to make the issue reproducible: the contract or
script involved, the network, and a transaction id or test that shows it.

## What you are testing against

**This code is unaudited.** No third party has reviewed the contracts. Treat
TestNet as a demonstration and do not put value on it that you would mind
losing.

**The contracts cannot be patched in place.** They are deployed with no update
or delete path. That is deliberate: an upgradeable keeper contract is one where
somebody can change the rules after you have escrowed funds. The consequence is
that a bug cannot be fixed on a live deployment. The remedy is
always the same: `cancel` your upkeep, recover your escrow and box MBR, and
move to a corrected deployment.

That has already happened twice. The first TestNet keeper app
([`769772891`](https://testnet.explorer.perawallet.app/application/769772891))
undercharged box minimum balance by 800 microAlgos per upkeep, which could
leave an upkeep unable to pay its final execution. It was replaced rather than
fixed, and 243,000 microAlgos of box MBR is stranded in it permanently. Its
replacement ([`769802474`](https://testnet.explorer.perawallet.app/application/769802474))
was in turn superseded by the 1.0 contract, whose `Upkeep` struct is a
different shape; its registry is empty. `docs/arcron.md` records the migration.

The live deployment is
[`769823086`](https://testnet.explorer.perawallet.app/application/769823086).
Check that any app really is the code you think it is, comparing compiled
bytecode rather than source text:

```bash
poetry run python -m scripts.verify_build --network testnet --app-id 769823086
```

## What stage this is at

Arcron is at **alpha**, which means the deployment may be replaced at any time
for any reason and nothing about it is a promise yet.
[`docs/releases.md`](docs/releases.md) sets out what freezes at each stage and
what is at stake. It matters here because the gate that ends alpha is the
point at which a struct change stops being free and starts meaning every
creator cancels and re-registers by hand.

## Scope

In scope, and interesting to us:

- anything that lets a keeper be paid without performing the registered call,
  or perform it without being paid
- anything that lets an upkeep's creator, a keeper, or a third party take
  escrow that is not theirs
- anything that lets execution happen before its due round, or be prevented
  after it
- the box-encoding decoders in `scripts/keeper_bot.py` and
  `web/src/app/core/upkeep.ts` disagreeing with the contract

Out of scope:

- the demo contracts in `smart_contracts/` other than `keeper/` are
  illustrations, not products; report anything you find but expect a lower
  priority
- `smart_contracts/beacon_stub/` is deliberately not random and is LocalNet
  scaffolding
- keeper liveness. Nobody is obliged to execute your upkeep, and that is the
  design rather than a defect
- the throwaway TestNet deployer account being funded or drained

## Key handling

**`DEPLOYER_MNEMONIC` is the one that matters.** It creates the app, and
while that app is unfrozen it can replace the app's programs, which means it
can rewrite the rules and reach every escrow. `poetry run python -m
scripts.govern status` reports whether a deployment is still in that state.
Everything below is less valuable than that key. For anything holding real
money it should be a multisig rather than one mnemonic: see
[`docs/deploying.md`](docs/deploying.md).

Nothing else in this repository should ever hold a key it does not need:

- `scripts/keeper_bot.py` signs executions, and needs a funded account. Losing
  it costs that account's ALGO and nothing else.
- `scripts/notifier.py` cannot sign at all, and a test enforces that.
- The console signs through a wallet or through LocalNet's KMD; it never sees
  a mnemonic.
- Env files are gitignored anywhere in the tree, not just at the root, and
  must stay that way. `deploy/keeper.env` is the one the deployment guide
  tells you to create, and it was briefly not covered.
