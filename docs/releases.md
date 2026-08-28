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
publishing it comes first. Its address is
**https://corvidlabs.xyz/arcron/console/**, and that is also the canonical URL
to check a link against: anything claiming to be Arcron at another address is
somebody else's front end, whatever it looks like.

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
| alpha-3 | 2026-08-26 | `13d38bb` | `c94c6e0c…` | TestNet [`769891898`](https://testnet.explorer.perawallet.app/application/769891898) | **An update in place, not a new app id.** The `Upkeep` struct and the ABI are unchanged, so the boxes and all five upkeeps survived and no creator had to cancel and re-register. Carries the payer binding at all four payment sites, which alpha-2 was deployed without: `f980321` and `1499963` landed after alpha-2 went up and were never deployed, so `verify_build` had been red against the live app. It is green now. First exercise of the governance update path on a real chain. |

## The rain dogfood deployment

Not a stage of the keeper contract; a separate app, deployed once, that
makes the dogfood in `docs/design/1.0.md` real rather than aspirational. It
has no alpha/beta/rc of its own: `rain` is frozen the moment it is created,
in the sense that it has no `configure` do-over and no update path, so a
release row here is simply "when it went up and what it is."

| Date | Commit | Contract sha256 | App id | Notes |
|---|---|---|---|---|
| 2026-08-26 | `d986921` | `7a4890f5…` | TestNet [`769988156`](https://testnet.explorer.perawallet.app/application/769988156) | Open entry, ALGO prize, `configure`d against the real Foundation randomness beacon `600011887` (`scripts.network.FOUNDATION_BEACON["testnet"]`), verified byte-for-byte with `scripts.verify_build --contract rain`. Pot seeded to 1,000,000 µALGO. |

**The upkeep.** Registered on the live keeper (app `769891898`) as upkeep
**76**: calls `draw()uint64`, interval **2,571 rounds** (≈2 hours at 2.8s),
policy **SKIP_AHEAD**, fee 4,000 µALGO/execution, funded 2,000,000 µALGO
(500 executions, ≈41.6 days, comfortably past the 30-day floor). SKIP_AHEAD
rather than CATCH_UP for the reason stated in `examples/register_upkeep.py`
and proven the hard way by upkeep 18 above it in this same registry: a
missed window replayed under CATCH_UP costs one fee per interval backed up,
which starved 18 to 41 serviced rounds against a 23,478-round backlog on 17
funded runs. A missed `rain` draw should be dropped, not replayed in a burst.

**The loop.** `scripts.rain_bot` holds a dedicated TestNet account (its own
`RAIN_MNEMONIC`, separate from `DEPLOYER` and `KEEPER`), which holds the
draw's one ticket. It resolves an open draw against the real beacon, claims
whatever it wins, and redeposits the exact amount straight back into the
pot; see the module's own docstring for what circulates (the prize) and
what does not (transaction fees). `.github/workflows/rain-bot.yml` runs it
every 30 minutes, the same cron-stopgap shape as `keeper-bot.yml`.

**Proven end to end, live, on 2026-08-26.** `scripts.rain_testnet_deploy
--bootstrap-draw` opened draw 1 manually (any account may call `draw`; this
did not touch the registered upkeep's own schedule, which will start firing
automatically at round 66,705,133), waited the 8 rounds for the beacon's
committed round to pass, and ran the bot's own resolve/claim/deposit logic
against it:

```
INFO: Draw 1 open: 981100 µALGO locked for 1 ticket(s), decided at round 66702585
INFO: Round 66702612: resolved draw 1; winner QIC6TGUFH3EUFGVQVCXLV5XPP7EHD7SFH4ONPLXYDCGABGMJQPH7P3DDKY
INFO: Round 66702663: claimed 981100 µALGO
INFO: Round 66702860: deposited 981100 µALGO back into the pot (pot now 2000000)
```

The winner is the bot's own account because it is, for now, the draw's only
entrant, and `draw`, `resolve` and `claim` do not know or care that it is the
same account each time, which is exactly what a genuinely open, permissionless
draw requires.

**What the real beacon confirmed, that the LocalNet stub could not.**
`smart_contracts/rain/contract.py`'s comments reason about the beacon's
behaviour; probing app `600011887` directly (a raw `must_get` simulate, no
real transaction) checked the reasoning against the thing itself before any
ALGO was committed:

- `must_get` answers for **any** past round, not only ones aligned to the
  committee's ~8-round submission cadence, confirmed against rounds offset
  by 16, 40 and 100 from a submitted one, all non-multiples of 8.
- The real retention window is between 1,500 and 1,600 rounds (a round 1,500
  back answered; 1,600 back panicked with `assert failed`), consistent with
  the contract's comment ("roughly 1,512") and comfortably wider than
  `BEACON_WINDOW`'s 1,000-round margin.

No change to `smart_contracts/rain/contract.py` was needed or made; both findings are the
LocalNet demo's assumptions holding up, not a gap it papered over.

**Two script bugs, found by a flaky endpoint, not by design.** The public
TestNet algod/indexer this deployment used (`https://testnet-api.algonode.cloud`)
returned intermittent `403 Forbidden` throughout (a shared, heavily used
endpoint, not this deployment's own account), so every step here was run
under retry. That surfaced two real gaps in the deployment tooling, both
fixed and covered by `tests/test_rain_bot.py`, neither in the contract:

1. `scripts/rain_testnet_deploy.py` used to gate its one-time pot seeding on
   `pot == 0`, which is also true of an ordinary in-progress cycle right
   after `draw` empties it. An interrupted first run re-entered that check
   after `draw` had already run and deposited a second 1,000,000 µALGO,
   harmless (a bigger prize, not a lost one) but wrong. Fixed by also
   requiring `draw_id == 0`, which only ever increments.
2. `scripts/rain_bot.py` used to redeposit only the amount `claim` had just
   returned in the same call. `claim` and `deposit` are two transactions,
   not one atomic group, and the same rate limiting interrupted a run
   between them: the prize sat in the bot's own wallet, and the next run's
   `allocation_of` correctly read zero, so nothing would ever have told it
   to redeposit money it no longer saw as owed. Fixed with a small local
   pending-deposit record (`default_pending_path`), written before the
   deposit is attempted and cleared only once it confirms, the same shape
   as `scripts/keeper_backoff.py`'s state, and with the same caveat: it
   assumes a persistent filesystem between runs, which a scheduled CI job
   is not (see `deploy/rain-bot.service` for the alternative).

**Measured monthly burn, not the ~2.5 ALGO estimated when this was
planned.** Two components, both a real, recurring cost that has to be
topped up from outside for the dogfood to keep running, neither of them the
prize (which nets to zero every cycle):

- The upkeep's own escrow pays 4,000 µALGO to whichever keeper executes
  `draw`, every ~2 hours: 4,000 × 12/day × 30 ≈ **1.44 ALGO/month**.
- The rain bot's own three transactions per cycle: `resolve` (2,000 µALGO:
  1,000 base plus 1,000 pooled for the beacon inner call), `claim` (2,000
  µALGO, same shape for the payment inner call) and `deposit` (2,000 µALGO:
  two ordinary 1,000 µALGO transactions), totalling 6,000 µALGO × 12/day ×
  30 ≈ **2.16 ALGO/month**.

**≈3.6 ALGO/month combined**, about 44% above the earlier estimate. The gap
is the participant side: three signed transactions per cycle rather than
one, because `resolve`, `claim` and `deposit` cannot be collapsed into a
single call without giving up the pull pattern the whole design rests on.

## Going back

There is no rollback. A deployment cannot be amended, only abandoned, so
"reverting" means deploying a new app and asking every creator to move, which
has happened once already and stranded 243,000 µALGO of box minimum balance in
the old app.

That asymmetry is the reason for the gates. It is much cheaper to stay in
alpha a week longer than to abandon a beta.
