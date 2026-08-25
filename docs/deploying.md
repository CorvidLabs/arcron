# Deploying, updating, freezing

Everything you can do to a keeper deployment, on any of the three networks,
and what each one costs you if you get it wrong.

## The short version

```bash
algokit localnet start
fledge lanes run local          # everything, against a chain

fledge run deploy-localnet      # a keeper of your own
fledge run deploy-testnet       # needs .env.testnet
fledge run govern -- status --network testnet --app-id <id>
```

## Contributing without deploying anything

You do not need a chain to work on most of this.

```bash
poetry install                  # Python 3.12 or 3.13, never 3.14
bun install                     # the console and the js package
fledge lanes run ci             # build, tests, spec drift, console
```

`fledge lanes run ci` is exactly what CI runs, task for task, so a green lane
locally means a green pull request. `CONTRIBUTING.md` covers the conventions
that will otherwise bite you, of which spec-sync is the one nobody expects.

For anything touching a chain, LocalNet is enough and costs nothing:

```bash
algokit localnet start
fledge lanes run local          # the above plus the e2e and every demo
```

## Deploying

| Task | Network | Needs |
|------|---------|-------|
| `fledge run deploy-localnet` | LocalNet | `algokit localnet start`, nothing else |
| `fledge run deploy-testnet` | TestNet | `.env.testnet` with `DEPLOYER_MNEMONIC` |
| `fledge run deploy-mainnet` | MainNet | `.env.mainnet`, and `ARCRON_ALLOW_MAINNET=1` |

Each one rebuilds from source, deploys, funds the app account's base minimum
balance, and then verifies the deployed bytecode against a clean build before
telling you it worked. It prints the app id and the combined `sha256` in the
shape [`releases.md`](releases.md) wants recorded.

**MainNet needs a second, deliberate act.** `ARCRON_ALLOW_MAINNET=1` is set
nowhere in this repository, so a mistyped `--network` cannot reach real money.
MainNet is also gated behind the rc clock; deploying there before that is a
decision, not a command.

**The contract now needs two program pages.** It compiles to just over 2,048
bytes, so a deployment allocates an extra page, and each page costs the
*creator* 100,000 microAlgos of minimum balance permanently. Budget 0.2 ALGO
locked rather than 0.1, for as long as the app exists. This is create-only:
extra pages cannot be added by an update, so a contract that outgrows its
pages needs a new deployment.

Funding the base minimum balance matters more than it sounds. An app account
below 100,000 µALGO cannot hold a box or escrow anything, and the failure
reads as a minimum balance error somewhere unrelated. It is the most common
way a fresh deployment looks broken.

## Updating, and giving it up

A new deployment starts **unfrozen**: its creator can replace the programs.
That is deliberate, and temporary.

```bash
fledge run govern -- status --network testnet --app-id 769891898
fledge run govern -- update --network testnet --app-id <id>
fledge run govern -- freeze --network testnet --app-id <id>
```

`status` reads `frozen` from global state, which anybody can do without
trusting anyone:

```
app 769891898
  approval   1932 bytes
  combined  sha256 bb466d637cc9441f408e8af29cc68398ab2d4320a02629e22c95cf057ce6d0fb
  frozen    absent: this app predates the freeze flag and has no update path
```

`update` compiles this tree, refuses if the deployment is frozen, replaces the
programs, and then re-reads them to confirm what landed is what was sent.

`freeze` gives the update path up permanently. It prints the digest the app
will be stuck with and makes you type the app id back. There is no undo, and
nothing can add an update path afterwards, because the only call that could is
an update.

**Whether to freeze at all is a choice**, and both answers are ordinary on
Algorand. Checked on MainNet: the Foundation's randomness beacon, the Reti
staking validator and Folks Finance pools accept `NoOp` only and can never be
updated. Tinyman AMM v2, Pact and AlgoFi all handle `UpdateApplication` and
can be. There is no single convention to follow, so the useful thing is to
pick deliberately and say which you picked.

A deployment that never calls `freeze` behaves exactly like Tinyman or Pact:
its admin can update it whenever it needs to. One that calls `freeze` behaves
exactly like the beacon. `govern status` tells anyone which they are dealing
with, which is the part that actually matters.

## Why unfrozen at all

An upgradeable keeper contract is one where somebody can change the rules
after you have escrowed funds, and no statement of intent removes the fact
that they could. That is a real cost, and the reason the flag exists rather
than a permanent update path.

The other side is that being unable to fix a bug is expensive while nobody
depends on the deployment yet. Two earlier deployments were abandoned rather
than repaired, which stranded box minimum balance and made every creator
cancel and re-register by hand.

So the update path is temporary by construction, readable on-chain, and given
up before the network asks anyone to rely on it. `docs/security.md` has the
full reasoning.

## Multisig control

A single mnemonic on a single machine is the wrong home for a key that can
rewrite a live contract. The creator can be an Algorand multisig address
instead. Nothing in the contract changes: it compares `Txn.sender` against
`Global.creator_address`, and a multisig address is an address like any other.
What changes is that producing a signature takes several people.

Configure it in `.env.<network>`:

```
ARCRON_MULTISIG_THRESHOLD=2
ARCRON_MULTISIG_ADDRESSES=ADDR1,ADDR2,ADDR3
```

With that set, `govern update` and `govern freeze` stop signing and start
writing an unsigned transaction instead:

```bash
fledge run govern -- update --network testnet --app-id <id> --out update.json

# each holder: read it first, then sign, wherever their key lives
fledge run govern -- show --file update.json --app-id <id>
SIGNER_MNEMONIC="..." fledge run govern -- sign --file update.json --app-id <id>

# anyone, once the threshold is met
fledge run govern -- submit --file update.json --app-id <id>
```

**`show` before `sign`, always.** The file is base64 msgpack, so signing it
blind is signing whatever somebody put in it, and a multisig whose holders do
not read what they sign is one person who clicked several times. `show`
decodes it:

```
on complete   UpdateApplication
REPLACES THE PROGRAMS with 2008 + 4 bytes
  approval sha256 481ac7e4c927f4ac2a9e3f36cfa8483dfe093ec0f5fd0d214e606b3001266bbd
  Compare that against `fledge run verify` on the commit you expect.
```

and shouts about the two things that would otherwise pass as routine:

```
!! REKEYS the sender to FKWV...ARHZU. Do not sign unless you meant this.
!! CLOSES the sender to ... Do not sign unless you meant this.
```

A zero-amount payment that quietly rekeys the multisig is exactly the
transaction somebody would wave through.

A signature is not a secret, so the file can be emailed, committed to a private
gist, or carried on a stick. Only the mnemonics stay put. Submitting below the
threshold is refused locally, and would be refused by the network anyway.

`fledge run smoke-multisig` proves the whole flow on LocalNet: a 2 of 3 creates
the app, one signature is rejected by the network, two are accepted, one holder
alone cannot update, and a *different* pair can.

`scripts/deploy.py` refuses to run when a multisig is configured, rather than
quietly deploying from the single-key `DEPLOYER` and leaving a contract whose
creator is not the multisig anyone was told to expect.

**Not every Algorand account can be a member.** A multisig subsignature is an
ed25519 public key and an ed25519 signature, so a member has to be an account
an ed25519 key can sign for. A post-quantum Falcon account's address is a hash
rather than a point on the curve, so no such key exists and it can never
produce a subsignature.

Nothing complains on its own. The address derives normally and the result
reads as a threshold of N while behaving as a threshold of N out of one fewer
signer, which silently changes who can act alone. `scripts/multisig.py`
refuses such a member rather than letting it through, and the check is a real
curve-membership test: about half of all 32-byte values happen to be valid
points, so a weaker check would pass some Falcon addresses and reject others,
which is worse than not checking.

**Which holders, and how many.** Three keys with a threshold of two is the
usual shape: any one can be lost without losing control, and any one can be
compromised without losing the contract. Keep them on different devices held by
different people; three keys in one drawer is one key.

## Checking a deployment you did not make

```bash
poetry run python -m scripts.verify_build --network testnet --app-id <id>
poetry run python -m scripts.govern status --network testnet --app-id <id>
```

The first compares compiled bytecode against a clean build of a given commit,
so it answers "is this app really that source". The second answers "can its
creator still change it". Together those are the whole trust question, and
neither requires believing anything written here.

## Continuous integration

`.github/workflows/ci.yml` runs the `ci` lane on every push and pull request,
and the LocalNet end-to-end on pushes. Pull requests from forks run the same
tasks on GitHub's own infrastructure with no secrets, because a self-hosted
runner executes whatever a workflow says.

Every CI step shells out to `fledge.toml`, so CI and a local run cannot drift.

Deployment is **not** automated, on any network. A deploy is a decision with a
permanent consequence, and `verify_build` exists so that decision can be
audited afterwards rather than trusted in advance.
