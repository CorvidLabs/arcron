# Release stages

Arcron's contract is upgradeable until its creator calls `freeze`, and rc is
where that decision gets recorded either way. It buys less than it sounds
like: an update replaces code, not the shape of boxes that already exist, so
the `Upkeep` struct is permanent from beta whichever way the decision goes.
That is what makes a release here different from a version bump, and every
stage below is really an answer to one question: **what is frozen, and what is
at stake if it is wrong?**

| Stage | Chain | What is frozen | At stake |
|---|---|---|---|
| **alpha** | TestNet | nothing | nothing, since we run every part |
| **beta** | TestNet | the ABI surface and the `Upkeep` struct | other people's test upkeeps |
| **rc** | TestNet | the exact bytecode intended for MainNet | the same, plus our credibility |
| **mainnet** | MainNet | everything, forever | real money |

The labels are ordinary. The gates are not, so they are written down.

## What a version *is* here

Not a semver. A deployment's identity is **the compiled bytecode and the app
id it lives at**, and the only claim worth making is that a given app is a
given commit:

```
poetry run python -m scripts.verify_build --network testnet --app-id <id>
```

That compares compiled bytecode, not source text. Every release below records
the combined `sha256` and the commit, so anyone can check the claim without
trusting us. A semver on the repository is a convenience for the tooling; the
hash is the thing.

## alpha: where the network is now

**Meaning:** it runs, we are the only ones affected, and the contract may be
redeployed at any time for any reason.

Nothing here is a promise. An alpha app id can disappear. Anyone registering
an upkeep against it should expect to cancel and re-register.

**Entry:** the e2e passes on a real chain.

**Exit, and the reason this stage has real work in it:** beta is where the
`Upkeep` struct stops being changeable for free, so feedback that could change
it has to arrive *before* then. Inviting people to use the network at beta
gets their reports one stage too late, when acting on one means every creator
cancelling and re-registering by hand.

Note that this is a different thing from the on-chain `freeze` call, which
happens at rc and gives up the ability to replace the programs. A struct
change is expensive from beta because it means a new app id whether or not the
old one could be updated: an update replaces code, not the shape of boxes that
already exist.

Getting outside upkeeps registered is therefore alpha work, not beta work.
While we are here a redeploy costs nothing but our own time, which is exactly
the condition under which you want to find out that a field is missing or a
policy is wrong. The console has to be reachable for that to happen at all, so
publishing it comes first.

## beta: other people may rely on it

**The step that matters**, because it is where a struct change stops being
free. From here on, a change means a new app id and every creator cancelling
and re-registering by hand.

**Gate (all of these, or it is still alpha):**

- [ ] The **`Upkeep` struct and the ABI surface are frozen**, and a change is treated as starting a new beta rather than amending this one
- [ ] `fledge lanes run local` green, and the e2e green **on TestNet**, not only LocalNet
- [ ] Both decoders pinned to the same recorded box, byte for byte
- [ ] `docs/security.md` current, with every accepted risk listed
- [ ] `verify_build` matches the deployment against a clean tree
- [ ] `govern status` published, so anyone can see the deployment is still
      updatable and by whom. beta does not require freezing: a struct change
      is still a new app, but a fixable bug should be fixed
- [ ] **30 days** of continuous TestNet uptime with a funded heartbeat, and the notifier running
- [ ] A keeper running somewhere that is not a laptop
- [ ] Documentation an integrator can follow without asking us anything
- [ ] **At least one upkeep registered by somebody who is not us, which survived a redeploy.**
      Not "an outside upkeep exists": one that was cancelled and re-registered when we
      replaced the app, because that is the thing beta promises not to make people do again,
      and it is worth knowing somebody has done it once before we promise it

**What we promise at beta:** we will not redeploy without a stated reason, and
if we do, we will say so before the old app is abandoned.

**What we do not promise:** an SLA. There is none and there cannot be. That
is what permissionless means. Fees are escrowed, execution is atomic, and a
neglected upkeep's fee escalates. That is the whole mechanism.

## rc: the candidate

**Meaning:** this exact bytecode is what we intend to put on MainNet. Not a
rewrite of it; this.

**Gate:**

- [ ] The bytecode is unchanged from the beta that earned it
- [ ] **A decision recorded about `freeze`, either way.** Freezing is
      optional and both answers are normal on Algorand: the Foundation's
      randomness beacon, Reti and Folks Finance are immutable, while Tinyman,
      Pact and AlgoFi keep an update path. What is not acceptable is drifting
      into one by accident, so rc requires the choice to be written down in
      the release row below, not that it go a particular way
- [ ] **#12 complete**: threat model, escrow isolation proven on chain, arithmetic reviewed, immutability posture stated, incident playbook written
- [ ] At least one **independent adversarial review** beyond our own
- [ ] Outside upkeeps still registered and being serviced, unchanged since beta
- [ ] **60 days** at rc with no contract change

**The rule that gives the stage its meaning:** *any* change to the contract
resets the 60 days. There is no "significant change" exemption. A stage whose
clock can be argued down is not a gate.

## mainnet

**Gate:**

- [ ] The rc bytecode, unchanged, hash recorded and published
- [ ] A **fresh deployer**, never used on TestNet, its key handling documented
- [ ] The app account funded for its base minimum balance
- [ ] `verify_build` run against the MainNet app id, output published
- [ ] The unaudited-risk disclosure prominent wherever anyone can find it
- [ ] A keeper running before the first upkeep is registered, because an empty registry with no watcher is worse than no deployment

**What MainNet does not change:** it is still unaudited, still unpatchable,
and the incident playbook is still "tell people to cancel". Shipping to
MainNet is a statement that we believe the contract is right, not that anyone
has certified it.

## Recording a release

Each stage is a git tag on the commit that produced the bytecode, plus a row
in the table below. Tags are `alpha-N`, `beta-N`, `rc-N`, `mainnet-N`. That is
a counter, not a semver, because the interesting number is which deployment it
is rather than how it compares to another.

| Stage | Date | Commit | Contract sha256 | App id | Notes |
|---|---|---|---|---|---|
| alpha-1 | 2026-08-24 | `0e4de44` | `bb466d63…` | TestNet [`769823086`](https://testnet.explorer.perawallet.app/application/769823086) | First 1.0 deployment; 20-stage e2e green on chain |
| alpha-2 | 2026-08-25 | `10ecd54` | `0afab368…` | TestNet [`769891898`](https://testnet.explorer.perawallet.app/application/769891898) | First deployment with governance: upgradeable until frozen, two program pages, and every fix from five review rounds. Pulse target [`769891902`](https://testnet.explorer.perawallet.app/application/769891902). |

## Going back

There is no rollback. A deployment cannot be amended, only abandoned, so
"reverting" means deploying a new app and asking every creator to move, which
has happened once already and stranded 243,000 µALGO of box minimum balance in
the old app.

That asymmetry is the reason for the gates. It is much cheaper to stay in
alpha a week longer than to abandon a beta.
