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

**The contracts can be patched in place until their creator freezes them.**
The keeper contract ships an `update` method only its creator can call, and a
one-way `freeze` that removes it permanently. Until `freeze` is called, the
creator can replace the programs and reach every escrow in that deployment.
That is a real power over your money, and nothing but `freeze` ends it.

Check which you are dealing with before you escrow anything:

```sh
poetry run python -m scripts.govern status --network testnet --app-id 769891898
```

It prints `frozen 0`, meaning the creator can still replace the programs, or
`frozen 1`, meaning nobody can. **The live TestNet deployment `769891898` is
unfrozen.** That is deliberate: alpha is the stage where a bug is fixed in
place instead of by asking every creator to cancel and re-register by hand.

Once a deployment is frozen, a bug in it cannot be fixed. The remedy is then
the one it always was: `cancel` your upkeep, recover your escrow and box MBR,
and move to a corrected deployment.

That has already happened twice. The first TestNet keeper app
([`769772891`](https://testnet.explorer.perawallet.app/application/769772891))
undercharged box minimum balance by 800 microAlgos per upkeep, which could
leave an upkeep unable to pay its final execution. It was replaced rather than
fixed, and 243,000 microAlgos of box MBR is stranded in it permanently. Its
replacement ([`769802474`](https://testnet.explorer.perawallet.app/application/769802474))
was in turn superseded by the 1.0 contract, whose `Upkeep` struct is a
different shape; its registry is empty. `docs/arcron.md` records the migration.

The live deployment is
[`769891898`](https://testnet.explorer.perawallet.app/application/769891898).
Check that any app really is the code you think it is, comparing compiled
bytecode rather than source text:

```bash
poetry run python -m scripts.verify_build --network testnet --app-id 769891898
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
  `js/src/upkeep.ts` disagreeing with the contract

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

**The creator's key is the one that matters.** It creates the app, and while
that app is unfrozen it can replace the app's programs, which means it can
rewrite the rules and reach every escrow.

On TestNet that key is `DEPLOYER_MNEMONIC`, a throwaway. **On MainNet it is
`corvid.algo`**, `WGSHC4TYKYBS6EX5V5E377BQDLKWIIPBCFOLZQZIXCKHFIEKRPBFOMW25A`,
a single account held in a wallet. An app's creator cannot be changed
afterwards, so deploying from anything else is unfixable; `require_mainnet_creator`
refuses `--network mainnet` for any other signer.

**This was a 2-of-3 multisig until 2026-08-29, and giving that up is a real
loss.** One key can replace the programs governing every upkeep's escrow while
`frozen == 0`. The reason is that no wallet will sign for a multisig. Asked
directly through Pera's own SDK with ARC-1 `msig` metadata, on TestNet, with
an account that is genuinely a member, Pera answers `multisig signing is not
supported`. ARC-1 defines the field, `@txnlab/use-wallet` exports the type and
implements none of it, and Pera declares it and refuses it. A 2-of-3 therefore
means every governance action is a mnemonic pasted into a shell by three
people, including one whose key is on a hardware device precisely so that never
has to happen.

**What makes one key defensible is `freeze`.** It is one way, and after it the
creator can never replace the programs again, so the key stops mattering. A
single-key deployment frozen early is a smaller exposure than a multisig left
upgradeable because signing is too painful to actually do. The commitment that
comes with this decision is to freeze promptly. Until then
`poetry run python -m scripts.govern status` reports the deployment as
upgradeable and the console says so on every page.

`scripts/multisig.py` is kept and still works, and a test proves a multisig
would still satisfy the gate. If a wallet ships multisig signing this is one
constant away from going back. See [`docs/deploying.md`](docs/deploying.md).

Nothing else in this repository should ever hold a key it does not need:

- `scripts/keeper_bot.py` signs executions, and needs a funded account. Losing
  it costs that account's ALGO and nothing else.
- `scripts/notifier.py` cannot sign at all, and a test enforces that.
- The console signs through a wallet or through LocalNet's KMD; it never sees
  a mnemonic.
- Env files are gitignored anywhere in the tree, not just at the root, and
  must stay that way. `deploy/keeper.env` is the one the deployment guide
  tells you to create, and it was briefly not covered.
