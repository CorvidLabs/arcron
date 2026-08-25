# Release stages

Arcron's contract has no upgrade path. That makes a release different from a
version bump: what ships is permanent, and every stage below is really an
answer to one question — **what is frozen, and what is at stake if it is
wrong?**

| Stage | Chain | What is frozen | At stake |
|---|---|---|---|
| **alpha** | TestNet | nothing | nothing — we run every part |
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

## alpha — where the network is now

**Meaning:** it runs, we are the only ones affected, and the contract may be
redeployed at any time for any reason.

Nothing here is a promise. An alpha app id can disappear. Anyone registering
an upkeep against it should expect to cancel and re-register.

**Entry:** the e2e passes on a real chain.

## beta — other people may rely on it

**The step that matters**, because it is where a struct change stops being
free. From here on, a change means a new app id and every creator cancelling
and re-registering by hand.

**Gate — all of these, or it is still alpha:**

- [ ] The **`Upkeep` struct and the ABI surface are frozen**, and a change is treated as starting a new beta rather than amending this one
- [ ] `fledge lanes run local` green, and the e2e green **on TestNet**, not only LocalNet
- [ ] Both decoders pinned to the same recorded box, byte for byte
- [ ] `docs/security.md` current, with every accepted risk listed
- [ ] `verify_build` matches the deployment against a clean tree
- [ ] **30 days** of continuous TestNet uptime with a funded heartbeat, and the notifier running
- [ ] A keeper running somewhere that is not a laptop
- [ ] Documentation an integrator can follow without asking us anything

**What we promise at beta:** we will not redeploy without a stated reason, and
if we do, we will say so before the old app is abandoned.

**What we do not promise:** an SLA. There is none and there cannot be — that
is what permissionless means. Fees are escrowed, execution is atomic, and a
neglected upkeep's fee escalates. That is the whole mechanism.

## rc — the candidate

**Meaning:** this exact bytecode is what we intend to put on MainNet. Not a
rewrite of it; this.

**Gate:**

- [ ] The bytecode is unchanged from the beta that earned it
- [ ] **#12 complete** — threat model, escrow isolation proven on chain, arithmetic reviewed, immutability posture stated, incident playbook written
- [ ] At least one **independent adversarial review** beyond our own
- [ ] At least one upkeep registered by **somebody who is not us**
- [ ] **60 days** at rc with no contract change

**The rule that gives the stage its meaning:** *any* change to the contract
resets the 60 days. Not "significant" changes — any. A stage whose clock can
be argued down is not a gate.

## mainnet

**Gate:**

- [ ] The rc bytecode, unchanged, hash recorded and published
- [ ] A **fresh deployer**, never used on TestNet, its key handling documented
- [ ] The app account funded for its base minimum balance
- [ ] `verify_build` run against the MainNet app id, output published
- [ ] The unaudited-risk disclosure prominent wherever anyone can find it
- [ ] A keeper running before the first upkeep is registered — an empty registry with no watcher is worse than no deployment

**What MainNet does not change:** it is still unaudited, still unpatchable,
and the incident playbook is still "tell people to cancel". Shipping to
MainNet is a statement that we believe the contract is right, not that anyone
has certified it.

## Recording a release

Each stage is a git tag on the commit that produced the bytecode, plus a row
in the table below. Tags are `alpha-N`, `beta-N`, `rc-N`, `mainnet-N` — a
counter, not a semver, because the interesting number is which deployment it
is rather than how it compares to another.

| Stage | Date | Commit | Contract sha256 | App id | Notes |
|---|---|---|---|---|---|
| alpha-1 | 2026-08-24 | `0e4de44` | `bb466d63…` | TestNet [`769823086`](https://testnet.explorer.perawallet.app/application/769823086) | First 1.0 deployment; 20-stage e2e green on chain |

## Going back

There is no rollback. A deployment cannot be amended, only abandoned — so
"reverting" means deploying a new app and asking every creator to move, which
has happened once already and stranded 243,000 µALGO of box minimum balance in
the old app.

That asymmetry is the reason for the gates. It is much cheaper to stay in
alpha a week longer than to abandon a beta.
