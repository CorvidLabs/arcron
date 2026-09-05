# Arcron governance

Freezing a deployment, from the creator's own machine.

```bash
fledge run govern-ui      # http://localhost:4302
```

## Local only, and that is the point

This is **never served from corvidlabs.xyz**, and it is the one app here that
reaches MainNet with a wallet signature; the create and `update` are shell
ceremonies (`docs/deploying.md`).

The console's address is a security property: Arcron's contract is
permissionless, so anyone can deploy a look-alike, and the canonical address is
the only thing separating our front end from a copy. That already matters for
the console, where a convincing clone costs somebody their own escrow.

A page whose purpose is authorizing **permanent changes to a live contract**
raises the stakes of that same clone from "one person loses their escrow" to
"the programs are replaced for everyone holding one". So it is not published,
and `tests/test_keeper_ui_stays_local.py` enforces that rather than trusting
anybody to remember.

## What it does

Reads a deployment's real state from a public node: creator, program sizes, the
combined sha256, and whether the freeze flag is set, absent, or already 1. Then
offers **freeze**, signed by Pera.

Nothing here needs a mnemonic. The creator is a single account held in a wallet
at rest (the create and `update` export its mnemonic into a shell for the
duration of a ceremony, never into a file),
decided 2026-08-29 after establishing that no wallet will sign for a multisig
sender: asked directly through Pera's own SDK with ARC-1 `msig` metadata, Pera
answers `multisig signing is not supported`. See
[`docs/security.md`](../docs/security.md#key-handling) for what that decision
costs and why `freeze` is what makes it defensible.

## What it deliberately does not do

**Update the programs.** A browser cannot compile Algorand Python, so this page
would have to be handed bytes it has no way to check, and a page that signs
what it cannot verify is worse than no page. Use
`poetry run python -m scripts.govern update`, where `verify_build` rebuilds
from source rather than trusting a download.

## The confirmation field is deliberate friction

`govern freeze` on the command line asks an operator to type the app id back
before it acts. A one-click button would be a downgrade wearing an
improvement's clothes. Freezing is the only irreversible action anywhere in
this project: afterwards the programs can never be replaced, by anyone, and a
bug found later means every creator cancelling and re-registering against a new
deployment.

Check the digest against `poetry run python -m scripts.verify_build` on the
commit you expect before doing anything.
