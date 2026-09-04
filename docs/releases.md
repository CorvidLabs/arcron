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
| **mainnet** | MainNet | the app id, which cannot be moved | our own real money |
| **public** | MainNet | everything, forever | other people's real money |

The labels are ordinary. The gates are not, so they are written down.

**mainnet and public are two stages because they are two decisions**, and until
2026-09-04 this page ran them together. Creating the application risks our own
money and nobody else's: an app id nobody has published is an app id nobody can
escrow into. Publishing it, and calling `freeze`, is what risks somebody else's.
Those have different gates and do not have to happen in the same month.
Conflating them made the whole of MainNet wait on evidence that only the second
half needs.

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
publishing it comes first. Its address is
**https://corvidlabs.xyz/arcron/console/**, and that is also the canonical URL
to check a link against: anything claiming to be Arcron at another address is
somebody else's front end, whatever it looks like.

**And if nobody comes, this stage still ends.** Wanting outside upkeeps before
the struct freezes is right. Making the stage *depend* on one was not, because
nothing we do can cause it, and a gate nothing we do can satisfy is a deadlock
rather than a bar. What the beta gate asks for instead is in the next section,
under the amendment that replaced it.

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

  `fledge run clock` measures this. It runs from the application's creation
  round rather than from any commit date, and it refuses to count at all once
  the local build stops matching what is deployed: time served by code that is
  about to be replaced is not evidence about the code replacing it. Add
  `--gate` to make it exit non-zero, which is what to hang a check on.
- [ ] A keeper running somewhere that is not a laptop
- [ ] Documentation an integrator can follow without asking us anything
- [ ] **A cold start succeeded, twice, from two independent starts.** Somebody
      working only from the published docs, with no access to this repository and
      no question answered by us, registered an upkeep, watched a keeper execute
      it, and cancelled it. Twice, because the first run has already happened and
      once is an anecdote: on 2026-08-26 an agent given only `README.md`,
      [`docs/arcron.md`](arcron.md) and [`docs/integrating.md`](integrating.md)
      did get there, but needed twelve guesses and had to disassemble the
      approval program to recover the ARC-4 selectors. The docs were then
      repaired *from that agent's report*, which is exactly the condition under
      which a rerun proves something and a memory of the first run does not

  **Amended 2026-09-04.** This item used to read **"at least one upkeep
  registered by somebody who is not us, which survived a redeploy"**, and the
  reasoning under it was sound: beta promises not to make creators cancel and
  re-register by hand, so it is worth knowing somebody has survived that once
  before promising it. It was still the wrong gate, for a reason nothing on this
  page had noticed. Every other item here is something we can go and do. That one
  required a stranger to volunteer, and no document in this repository said what
  happens if none ever does. It is a deadlock rather than a bar: alpha cannot
  end, so the struct never freezes, so there is never a deployment stable enough
  to be worth a stranger's attention, so alpha cannot end.

  What the outsider was standing in for is three things, and they separate
  cleanly:

  1. **Feedback that could still change the struct.** If nobody comes, nobody
     holds an opinion about the struct, and the only people a wrong shape costs
     are us. A self-borne risk is one we are allowed to accept, and that is the
     whole difference between this stage and the money gates below.
  2. **Evidence the docs work end to end.** The instrument for that is a cold
     start, not a volunteer, and it can be run on any afternoon.
  3. **Evidence that somebody who is not us can operate it.** The same
     instrument: a clean checkout, no repository access, nobody to ask.

  Two of those are purchasable on demand, and are what the item now asks for. The
  third is self-borne, and is accepted here rather than gated. An outside upkeep
  remains the better evidence and is still wanted ([#92](../../issues/92),
  [#93](../../issues/93) and [#94](../../issues/94) are open and unchanged), but
  it is now something that makes the case stronger rather than something whose
  absence stops the release.

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
- [ ] **The cold start reproduces against the rc docs**, which are not the beta
      docs: every stage edits them, and the 2026-08-26 run is the standing proof
      that docs repaired from a report are not thereby known to work. If outside
      upkeeps exist by then, they are still registered and being serviced,
      unchanged since beta
- [ ] **60 days** at rc with no contract change

**The rule that gives the stage its meaning:** *any* change to the contract
resets the 60 days. There is no "significant change" exemption. A stage whose
clock can be argued down is not a gate.

## mainnet: the deployment

**Meaning:** the rc bytecode exists on MainNet, at an app id we have told
nobody. Our own upkeeps, our own money, our own keeper.

**This is the strongest soak available to us**, which is worth saying plainly
because it reads like the risky step and is not. TestNet time is evidence about
code paths. MainNet time with our own money is evidence about code paths *and*
about everything that only becomes real when the ALGO is: a fee market we do not
set, a keeper that has to actually stay up, a mistake that costs something. The
contract cannot tell which chain it is on. We can, and only one of the two
chains bills us for being wrong.

**Gate:**

- [ ] The rc bytecode, unchanged, hash recorded
- [ ] A **fresh deployer**, never used on TestNet, its key handling documented
- [ ] The app account funded for its base minimum balance, **confirmed by
      `govern status` showing spendable at or above escrow** rather than by
      remembering to do it
- [ ] `verify_build` run against the MainNet app id
- [ ] A keeper running before the first upkeep is registered, because an empty registry with no watcher is worse than no deployment
- [ ] The create ceremony **rehearsed end to end from a clean checkout on a
      second machine**, before the one that counts. It is the only path in this
      repository that has never executed, and the fields a create fixes are
      permanent: extra pages, both schemas, the creator. `scripts/multisig.py`
      compares all of them at sign since #245; nothing has yet watched it do so
      on a create that was not a test
- [ ] Every finding a create would make permanent is closed. Not every open
      finding: "the console has no MainNet entry" is an open finding whose
      closure publishes the app id, and that belongs to the stage below

**What holds this stage together is one control:** the app id is published
nowhere. Not in the console, not in the JS client, not in a README, not in a bot
log, not in a status post. Read the warning below before treating that as easy.

## public: the invitation

**Meaning:** the app id is published, outside escrow is welcome, and `freeze`
has been called or explicitly declined in writing.

**Gate:**

- [ ] Everything above, still true
- [ ] **A decision executed about `freeze`.** rc required the choice to be
      recorded; this is the stage where it is acted on
- [ ] The MainNet hash and the `verify_build` output **published**
- [ ] The unaudited-risk disclosure prominent wherever anyone can find it
- [ ] The console, the JS client and the docs carry the MainNet entry, which
      *is* the act of publishing and so happens here and nowhere earlier
- [ ] An incident playbook for the day somebody else's money is stuck, written
      before it is needed rather than during it

### The warning that makes the split honest

**There is no such thing as not inviting people, on chain.** `register` is
permissionless, so anybody who learns the app id can escrow into it during the
unpublished window, and an explorer listing, a bot log, a README or a status
post is the invitation whatever we intended by it. The protection during that
window is not our intent and not a calendar. It is that the id is nowhere.

That gives the window one rule, and it is a trigger rather than a schedule.
**If an upkeep we did not create appears before freeze, that is a real person
who has trusted us, and the answer is to freeze then**, not to finish the
remaining time first. An unfrozen app holding somebody else's money means they
are trusting a keyholder rather than bytecode, and the 90-to-95 percent
confidence this project accepts is justified by a remedy that is ours and does
not transfer. [`status.md`](status.md) argues that at length; this paragraph is
the operational half of it.

**What MainNet does not change:** it is still unaudited, and once frozen still
unpatchable, and the incident playbook is still "tell people to cancel".
Shipping to MainNet is a statement that we believe the contract is right, not
that anyone has certified it.

## Recording a release

Each stage is a git tag on the commit that produced the bytecode, plus a row
in the table below. Tags are `alpha-N`, `beta-N`, `rc-N`, `mainnet-N`,
`public-N`. That is a counter, not a semver, because the interesting number is
which deployment it is rather than how it compares to another.

| Stage | Date | Commit | Contract sha256 | App id | Notes |
|---|---|---|---|---|---|
| alpha-1 | 2026-08-24 | `0e4de44` | `bb466d63…` | TestNet [`769823086`](https://testnet.explorer.perawallet.app/application/769823086) | First 1.0 deployment; 20-stage e2e green on chain |
| alpha-2 | 2026-08-25 | `10ecd54` | `0afab368…` | TestNet [`769891898`](https://testnet.explorer.perawallet.app/application/769891898) | First deployment with governance: upgradeable until frozen, two program pages, and every fix from five review rounds. Pulse target [`769891902`](https://testnet.explorer.perawallet.app/application/769891902). |
| alpha-3 | 2026-08-26 | `13d38bb` | `c94c6e0c…` | TestNet [`769891898`](https://testnet.explorer.perawallet.app/application/769891898) | **An update in place, not a new app id.** The `Upkeep` struct and the ABI are unchanged, so the boxes and all five upkeeps survived and no creator had to cancel and re-register. Carries the payer binding at all four payment sites, which alpha-2 was deployed without: `f980321` and `1499963` landed after alpha-2 went up and were never deployed, so `verify_build` had been red against the live app. It is green now. First exercise of the governance update path on a real chain. |

## The rain deployments are recorded elsewhere now

This page used to carry a section of release rows for the rain hub, which was
never a stage of the keeper contract: separate apps, no alpha/beta/rc of their
own, listed here only because they lived in this repository. On 2026-08-31 the
rain contract, its scripts, its bot and its console moved to
<https://github.com/CorvidLabs/arcron-rain>. The code moved; the writing did
not. Its release rows, its measured monthly burn and the two deployment-script
bugs a flaky endpoint found were cut here and have not been rehomed, so the
only copy is this repository's history; `arcron-rain` has no release page to
link to. What did travel is the live end-to-end proof, as a script rather than
a write-up.

What that section argued is worth keeping in one line, because it is about the
keeper and not about rain: **a scheduled call whose absence would be noticed is
the only dogfood worth the name.** The registry itself is that now. See
[status.md](status.md) for what the live registry looks like, and
[design/1.0.md](design/1.0.md) for why the mainnet gate stopped depending on a
single upkeep we run ourselves.

## Going back

There is no rollback. A deployment cannot be amended, only abandoned, so
"reverting" means deploying a new app and asking every creator to move, which
has happened once already and stranded 243,000 µALGO of box minimum balance in
the old app.

That asymmetry is the reason for the gates. It is much cheaper to stay in
alpha a week longer than to abandon a beta.
