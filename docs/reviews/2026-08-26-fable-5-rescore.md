# Arcron: full-system re-score

| | |
|---|---|
| **Target** | `main` @ `8b9bb05`, whole system |
| **Date** | 2026-08-25 |
| **Prior passes** | `docs/reviews/2026-08-25-fable-5.md` (3/10), `docs/reviews/2026-08-26-fable-5-console.md` (console 5/10) |
| **Method** | Source read at `8b9bb05`; three fan-out agents (satellite contracts, governance, clients) with every load-bearing claim re-verified by me first-hand; **six adversarial experiments run against a real AVM on LocalNet**; `pytest` (298 pass) and `bun test` (124 pass) run |
| **Confidence, real money on MainNet today (unfrozen, id unpublished)** | **5 / 10** |
| **Confidence if it were frozen at deploy** | **3 / 10** |
| **Would I escrow my own money on MainNet today** | **No** |

**Disclosure:** I deployed three throwaway Keeper apps to LocalNet (8207, 8212, 8217) and registered a few upkeeps in them, because two of my findings could only be told apart from mock artefacts on a real AVM. `algokit localnet reset` clears them. No file in the repository was changed.

---

## 1. Overview

Arcron is a permissionless keeper network: a creator escrows ALGO in the `keeper`
contract and registers an upkeep, "call app X with these args every N rounds,
paying R microALGO". Any keeper calls `execute` when one is due and is paid from
that upkeep's own escrow, atomically with the call it triggers. No owner, no
rake, no token. Six sibling contracts are example targets demonstrating the
pull-not-push pattern the design forces.

The distance travelled since my last pass is large and mostly real. CI is green
at the deployed commit (I ran both suites). `SECURITY.md` no longer says the
opposite of the truth about immutability. The flagship example compiles against
the shipped API. Every accepted payment and asset transfer in all seven
contracts now refuses `rekey_to`, `close_remainder_to` and `asset_close_to`; I
enumerated all thirteen of them and found no gap. All seven items on my
console list were implemented, and implemented properly: `noticesFor` is a pure
exported function taking chain state as data, the identity comparison happens
before any network call, notices are ranked rather than exclusive, there is a way
back to the published app, and `frozen.test.ts:13` now imports the real `isFrozen`
so reverting the coercion fails the suite. That last one is the specific thing I
complained about and it was fixed at the root rather than papered over.

What I found instead is that the review process has a characteristic shape, and
it is still producing findings. Three times now a fix has been correct at the
site where the bug was found and absent at the two or three sibling sites with
the identical shape, with the *reasoning* written down at the fixed site and not
carried across. And twice a guard has been built on a confident premise about
what some other layer does, where the other layer does not do that. Both patterns
are live at `8b9bb05`, and one instance of each is the top of my list below.

The keeper contract's own arithmetic remains the strongest part of this system,
and I want to be clear that I did not break escrow accounting: every balance
mutation is a single guarded add or subtract, the overflow bounds are proven from
input limits rather than from chain age, the pull-don't-push discipline is applied
consistently, and I confirmed on chain that the AVM's ARC-4 argument decoder makes
the box registry structurally incorruptible (section 3). The finding at the top of
section 2 is not an arithmetic bug. It is a missing binding between who pays and
who owns, and it is the one path I found where a third party's money ends up
somewhere its owner did not choose.

---

## 2. Anything that can still lose money that was not its owner's to lose, ranked

### K1 (High, **confirmed on chain**): `register` never binds the payer to the upkeep's creator, so a victim's payments can fund an attacker's upkeep

`register` checks four things about each of the two payments it accepts: the
receiver, `rekey_to`, `close_remainder_to`, and the amount
(`smart_contracts/keeper/contract.py:193-223`). It never checks the **sender**.
The upkeep's creator is `Txn.sender` of the app call
(`smart_contracts/keeper/contract.py:228`), and `cancel` pays that creator
(`:282`, `:327`).

Those are two different accounts, and nothing requires them to be the same. I
proved it against a real AVM on LocalNet: a group of the victim's MBR payment,
the victim's funding payment, and an app call signed by a different account:

```
victim   IZKHUOYL3SOYEEWUGZDVIJNTNRGQBSI3DATGJ3GXZK35QN55YITWSWLGJM
attacker 7CXIBSF52LSXFHJ2OEPGEZN5CQTYHMOGR743LPQIIJTEX3PB67AYYLF7AQ
register ACCEPTED. box creator = 7CXIBSF52LSXFHJ2OEPGEZN5CQTYHMOGR743LPQIIJTEX3PB67AYYLF7AQ
  creator is the ATTACKER: True  (victim paid every microALGO)
victim   net -464,100 microALGO
attacker net +459,100 microALGO (paid 3,000 in fees)
```

The victim signed two payments to the genuine app account of the genuine app id.
The attacker signed the app call, then called `cancel` and took the escrow and the
released box MBR.

Three things make this worse than it first looks:

1. **It is the exact threat model this contract already adopted.** Invariant 20
   (`specs/keeper/keeper.spec.md:85`) and the test-file comment at
   `tests/test_keeper.py:887-891` both say the rekey and close asserts exist to
   protect "a user of a malicious front end that slips either into a group
   otherwise built to look like an ordinary register or top-up." This is that
   front end, one step further, and it is strictly the easier attack: wallets
   render a rekey with a loud dedicated warning, and they render a group member
   with a different sender as an ordinary line item.
2. **Every check the trust banner teaches a user to make passes.** The receiver
   is the real app account. The app id is the published one. The console's own
   identity notice stays silent, correctly, because the app really is `769891898`.
   The one signal that would catch it, the sender of the app call, is the one
   thing the register panel and the banner never mention.
3. **The project already knows the assert.**
   `smart_contracts/subscription/contract.py:162` is
   `assert deposit.sender == Txn.sender, "Deposit must come from the caller"`.
   The example contract binds its payer. The contract holding the money does not.

**Fix:** two asserts in `register`. No struct change, so it costs no app id, and
the approval program is 2,178 bytes of a 4,096-byte two-page budget. I checked
that nothing legitimate breaks: `js/src/keeper-txns.ts:112-121` builds both legs
from `signing.sender`, `examples/register_upkeep.py:76-84` and every
`_register_with_interval` call in `scripts/keeper_e2e.py` use the deployer for
both.

`top_up` and `top_up_asset` have the same missing binding
(`contract.py:253-260`, `:512-521`) but there it is a deliberate feature,
"anyone can top up an upkeep", asserted at `scripts/keeper_e2e.py:443-459` and
stated in the console's own hint text. The marginal harm over "trick someone
into a payment" is small there, so I would leave those and fix `register`.

### K2 (High, confirmed): a single-key MainNet creation path exists outside the tooling that forbids it

`scripts/network.py:74-101` is good work. It refuses MainNet unless a multisig is
configured *and* its recomputed address equals the hardcoded `MAINNET_CREATOR`,
and it is called from `load_network` so it covers every `scripts/` entry point at
once. `scripts/deploy.py` and `scripts/govern.py` are both dead on MainNet
without it.

But `.algokit.toml` declares:

```toml
[project.deploy]
command = "poetry run python -m smart_contracts deploy"

[project.deploy.mainnet]
environment_secrets = [ "DEPLOYER_MNEMONIC" ]
```

That command reaches `smart_contracts/__main__.py`, which does a bare
`load_dotenv()` at line 24 and never imports `scripts.network`, then
`smart_contracts/keeper/deploy_config.py:15-19`:

```python
algorand = algokit_utils.AlgorandClient.from_environment()
...
deployer_ = algorand.account.from_environment("DEPLOYER")
```

No `ARCRON_ALLOW_MAINNET`, no `require_mainnet_multisig`, no genesis assertion.
`algokit project deploy mainnet` with `ALGOD_SERVER` pointed at MainNet creates a
keeper app whose creator is one key. An app's creator is fixed at creation and
holds `update` and `freeze` for as long as the app exists, so this is not a
mistake you fix; it is a permanent admin key over every escrow in that
deployment. The config file names `DEPLOYER_MNEMONIC` as the MainNet secret
explicitly, which is the one thing that should never appear next to the word
mainnet in this repo.

Related: `poetry run python -m smart_contracts` with no argument falls through to
`main("all")`, which builds **and deploys** every contract against whatever
environment happens to be loaded.

### K3 (High, confirmed): the control that makes the unfrozen MainNet window acceptable is not implemented, and its premise is already false

`docs/status.md:126-131` is explicit that the protection during the pre-freeze
window "is not the calendar and not our intent: it is that the MainNet app id is
not published anywhere," and that an unexpected upkeep "is a real person who has
trusted us, and the answer is to freeze then."

Both halves fail.

**The id is derivable.** The MainNet multisig address is published in
`SECURITY.md:99`, `docs/security.md:312`, `docs/deploying.md:224` and `:266`,
`scripts/network.py:71` and `tests/test_multisig.py:52`. An app id is one indexer
query from a creator address, and every block explorer shows an account's created
applications on its front page. The moment that account creates the app, the app
id is public to anyone holding the address, and the address is in the file a
security researcher opens first. This does not matter while the repository is
private; it becomes true the instant #50 lands, and #50 is scheduled *before*
public release, which is before freeze.

**There is no detector.** `scripts/notifier.py:71-90` builds its `Snapshot` from
ten upkeep fields and `creator` is not one of them, so `diff` at `:128-198` has
no way to distinguish an upkeep we registered from a stranger's. It emits
"Upkeep N registered" naming the target app, interval and fee, and never says
whose. Worse, `:141` guards that event with `if previous.upkeeps:` so that a
first run is not a flood, which means on a freshly deployed app the entire
initial registry, including anything a stranger got in first, is announced to
nobody.

So the stated remedy, freeze the moment somebody else shows up, depends on
noticing, and nothing here notices. Adding `creator` to the snapshot and flagging
any address not on an allowlist is perhaps thirty lines.

### K4 (Medium-High, confirmed): `govern sign --no-rebuild` silently removes the program comparison, with no refusal

`scripts/govern.py:322-326`:

```python
expected_digest = None
if ms.carried_programs(args.file) is not None and not args.no_rebuild:
    rebuild()
    expected_digest = _digest(*_programs(_spec("keeper")))
```

and `scripts/multisig.py:375` gates the whole program check on
`if expected_digest is not None:`. When it is `None`, **no refusal is emitted at
all**. A file carrying arbitrary hostile programs against the correct app id
produces zero reasons not to sign.

The same function does the opposite, deliberately, eight lines earlier
(`scripts/multisig.py:366-374`): when no multisig is configured it adds a refusal
precisely because "saying nothing looks identical to having checked." That
reasoning was written down and then not applied to the field it was written for.

Aggravating: `--no-rebuild` is a global flag whose help text reads
`"update: trust the built artifacts"` (`scripts/govern.py:359`), so a holder has
no reason to think it touches `sign`; and `_refuse` runs a full puya compile on
every sign of a program-carrying file, which is exactly the pressure that makes
someone reach for the flag. The repo's own `fledge run verify` normalises it
(`fledge.toml:10`).

Related and smaller: `refusals` never inspects the method selector or the
on-completion action, so a `freeze()void` call, the one transaction that can
never be undone, describes as `app args 0e2c6c0f` and draws no refusal. The
ARC-56 spec with the method list is already loaded in that code path
(`scripts/govern.py:52`).

### K5 (Medium, confirmed): the keeper bot's race-attribution guard is a no-op, and its regression test pins a string the system cannot produce

`scripts/keeper_backoff.py:43-49` says:

```python
# algod names the application that failed, as
# "Runtime error when executing Keeper (appId: N) in transaction 0: Not due".
# ... The target controls its text; it does not control which app the node says failed.
KEEPER_ATTRIBUTION = "executing keeper"
```

algod does not produce that string. `algokit-utils` does, client-side, from the
caller's own app spec, at
`.venv/lib/python3.13/site-packages/algokit_utils/applications/app_client.py:1712`:

```python
f"Runtime error when executing {app_spec.name} "
```

`app_spec` is the `KeeperClient`'s spec, whose `name` is `"Keeper"`. So the
string is `"executing Keeper"` for every error the bot ever sees, whichever app
actually failed. `KEEPER_ATTRIBUTION in lowered` is therefore always true, and
`is_lost_race` collapses back to the plain substring match on `("not due",
"upkeep not found")` that the fix existed to remove. A target that fails in a way
whose rendered message contains either phrase is treated as a lost race,
`record_failure` returns `None` (`keeper_backoff.py:117-120`), and the upkeep is
never backed off: it stays due forever and consumes a build-and-simulate round
trip on every scan, without bound.

And `tests/test_keeper_backoff.py:180-183` asserts against:

```python
target = ("Runtime error when executing Pulse (appId: 1004) in transaction 0: "
          "cooldown not due")
```

A `KeeperClient` call can never render `"executing Pulse"`. **This is the same
shape as the finding I raised last time**, one level up: the test does not
declare a private copy of the code, it declares a private copy of the *input*,
and passes against a string the system does not generate.

No ALGO is burned (algokit simulates before submitting, and Algorand rejects
failing transactions at validation rather than including them), so this is
availability, not theft. But the trustworthy signal is already in hand:
`record_failure` receives the upkeep, and a genuine lost race is visible in state
as `times_executed` or `next_execution_round` having moved.

*Suspected, not reproduced:* that a hostile target can aim its failing program
counter at a PC which the Keeper's own ARC-56 source map renders as `"Not due"`.
The structural half above holds regardless of whether that works.

### K6 (Medium, confirmed): `rain` and `subscription` book the app account's own base minimum balance, which is the bug `deadman` just fixed

`APP_BASE_MBR` appears in exactly one contract:
`smart_contracts/deadman/contract.py:48`, used at `:97` and `:115`. The fix there
is correct, and `scripts/deadman_demo.py:56-62` removes the pre-fund that was
masking it and explains at length why the demo running clean *is* the regression
test.

Neither sibling with the same shape got the same treatment.
`smart_contracts/rain/contract.py:240` credits every microALGO of a deposit to
the pot with nothing held back, and `smart_contracts/subscription/contract.py:248-250`
refunds `record.balance + SUBSCRIBER_BOX_MBR`, that is the whole deposit plus the
box, leaving the app account nothing for its own 100,000. In both cases the last
party out cannot get out: the inner payment drops the account below its minimum
balance and reverts. And in both cases the mask is still in place, uncommented:
`scripts/rain_demo.py:117-123` sends a bare 200,000, `scripts/subscription_demo.py:79-85`
sends a bare 300,000, both to the app account for no stated reason, exactly as
`deadman_demo.py` used to.

The money at risk is its owner's own and any passer-by can unstick it with a bare
payment, so this ranks below K1 to K5. It matters because `docs/status.md:44`
recommends `subscription` as "the better of the two examples to copy", and an
integrator who copies it inherits the defect.

### K7 (Low-Medium, confirmed): the console's write path is gated at three of five call sites, and not at the choke point

`KeeperService.send` (`web/src/app/core/keeper.service.ts:95-127`) is the single
funnel every money-moving call passes through, and it still never reads
`arcron.status()`. The gate was instead added per call site:
`register-form.ts:359-369` requires `status() === 'ready'`, and
`registry-table.ts:273` defines `reads()` and applies it to execute, fund and
cancel at `:123`, `:131`, `:140`. Two sites were missed:
`upkeep-board.ts:88` (Execute, gated only on `busy()`), and
`registry-table.ts:163` (the Fund submit button inside an already-open panel).

That is the C2 finding from my console pass, closed at 60 percent, in the one
shape that guarantees the next new button will miss it too. Ten lines in
`send` covers all five permanently.

---

## 3. What is genuinely good

**The AVM validates the stored call arguments exhaustively, and I proved it.**
I expected the opposite. `register` bounds only the declared argument count and
the total encoded size (`contract.py:184-190`), so I built four `byte[][]` blobs
that a naive decoder would choke on and tried each against a real chain:

```
well-formed offset 2:             ACCEPTED. box 140 bytes
in-range misaligned offset 3:     REJECTED (assert // invalid tail pointer)
crafted offset 60000:             REJECTED (assert // invalid tail pointer)
valid encoding + 4 trailing bytes: REJECTED (total-length assert)
two args aliasing one body:       REJECTED (assert // invalid tail pointer)
```

Puya emits a full walk of the encoding at method dispatch
(`smart_contracts/artifacts/keeper/Keeper.approval.teal:143-190`), requiring every
tail pointer to be exactly canonical and the total length to match. That means
**no third party can plant an undecodable box in the canonical app**, which in
turn means the console's new "N boxes do not decode" notice is a true signal
rather than something an attacker can turn on for 0.06 ALGO. I went looking to
break the new trust control and the platform closed it for me. Worth writing into
the spec, though, because it is a property of the compiler rather than of an
assert anyone here wrote, and nothing in this tree would notice if it changed.

**The trust banner rebuild is the right shape, not just the right behaviour.**
`noticesFor` (`trust-banner.ts:14-87`) takes chain state as a plain argument and
returns a list, so the identity comparison genuinely cannot be gated on a network
read; the "no published app is recorded" branch at `:30-38` closes the C8
fail-open I flagged for the moment MainNet is added to `networks.ts`; ranking
replaced exclusion so a self-hoster now sees both notices; the "switch to the
published app" button at `:112-117` closes the no-way-back problem without
breaking persistence for self-hosters. `arcron.service.ts:203-204`'s generation
counter is a real fix to the last-writer-wins repaint, and `:267-285`'s per-box
try/catch with an `undecodableBoxes` count converts a total outage into one
dropped row plus a warning. And `frozen.test.ts:13` imports the real function,
with a comment naming the mistake. That is the thing I asked for and it was done
at the root.

**The rekey/close sweep is complete.** I enumerated every `gtxn` argument across
all seven contracts: thirteen of them, spanning `register` (two), `top_up`,
`opt_in_asset`, `top_up_asset`, `deadman.arm`, `embargo.schedule`,
`subscription.subscribe`, `rain.opt_in_prize_asset` / `enter` / `deposit` /
`deposit_asset`, `treasury.configure` / `deposit`. Every one asserts the
receiver is the app account, every one refuses `rekey_to` and
`close_remainder_to`, and both asset transfers refuse `asset_close_to`. No gaps.

**The keeper bot's fee ceiling is real and enforced end to end.**
`MAX_OUTER_FEE_MICROALGO = 10_000` (`keeper_bot.py:53`) is applied as
`max_fee = MAX_OUTER_FEE + extra_fee` (`:573-575`) and survives the whole
algokit stack: `_common_txn_build_step` raises before signing, `build()` runs
before `send_atomic_transaction_composer`, and the simulate-then-populate step
rewrites resources only and never recomputes the fee. There is exactly one
submission path and no retry-with-higher-fee anywhere. A hostile target cannot
inflate what the bot signs.

**The deployer-key refusal on an unfrozen app holds.** `keeper_bot.py:446-478`
sits between the `KEEPER` load failure and the `DEPLOYER` load, `--check` exits
before any account is loaded, an exception inside `is_frozen` propagates and
kills the process, and a bad mnemonic lands in the same guarded path. I looked
for an env var, a flag, an ordering trick and an exception path and found none.

**Multisig integrity is done properly.** Threshold, signers and address are all
read from the signed msgpack blob (`multisig.py:249`, `:287`, `:292`), the address
is recomputed and compared against the configured group (`:345-349`), and the
editable JSON fields written by `export_unsigned` are never read back. A signer
cannot downgrade the threshold or swap a key without changing the derived
address. There is no `--force` and no `--yes` that skips refusals; `sign` always
refuses before it prompts, and the prompt requires typing the sender address in
full.

**The deadman and treasury fixes are correct, and treasury's is correct for a
structural reason.** deadman's floor arithmetic works out exactly (minimum
passing deposit 100,001, escrow 1, claim leaves exactly 100,000) and the inner
payment compiles to `Fee = 0` so the app never pays fees either. treasury's
`configure` creates its box while its MBR payment covers only the box, so the
group cannot settle unless the base MBR is already there: it fails closed, and
because deposits can no longer precede `configure`, deposited ALGO can never be
the thing transiently satisfying that check.

**Smaller things that were done right:** the `register` note that stops the two
payment legs serialising identically (`keeper-txns.ts:106-121`); the escalation
arithmetic agreeing across three implementations including the
`balance < fee -> base` clamp and the replay guard; box head offsets identical
between `keeper_bot.py:224-238` and `js/src/upkeep.ts:118-134`; `SECURITY.md`
now correct and specific about the update path, with the check command inline;
`subscription.charge` being keeper-gated *and* rate-limited rather than either
alone.

---

## 4. What is weak or will bite

**The fix-one-site pattern is the dominant risk in this codebase.** K6 is the
clearest case (deadman fixed, rain and subscription not, with the explanation
written down at the fixed site and the mask left in place at the other two), but
K7 is the same thing in the console and the ASA surcharge is a third: the bot
checks three of the six conditions the contract actually requires before paying a
bonus (`keeper_bot.py:560-566` versus `contract.py:410-417`, missing the app's
real holding, and both frozen checks), and the JS client checks only one, because
its parameter type `Pick<Upkeep, 'id'|'targetApp'|'feeAsset'>`
(`keeper-txns.ts:212`) cannot see `assetBalance` at all. All three of those are
over-declare, so the cost is a wasted 1,000 microALGO on exactly the executions
that pay most, and a creator with a clawback ASA can make it permanent. This is
the finding I raised as L4 last time, fixed at one site.

**Guards built on assumptions about another layer.** K5 is the sharp case. A
second: `js/src/keeper-txns.ts:305-318` simulates before submitting and then
never checks `failureMessage`. I read algosdk 3.7.0's
`AtomicTransactionComposer.simulate` and it does not inspect it either, so a
failed simulation resolves normally, `unnamedResourcesAccessed` comes back
undefined, `foldUnnamedResources` returns the known set unchanged, and the
console asks the user's wallet to sign a transaction already known to fail. The
Python path raises. I had assumed this failed closed when I first read it, which
is the point: the code reads as if it does.

**`js/src/upkeep.ts:109` reads the tail-offset fingerprint and does not check
it,** while `scripts/keeper_bot.py:218-223` rejects any value other than 130 and
its docstring explains that the check exists so a box from a different struct
shape is refused rather than decoded into plausible garbage. The TypeScript twin
has the length check but not the fingerprint, so a wrong-shape box is decoded at
1.0 offsets and shown to a user as real balances and schedules. It is also not
counted as undecodable, so the banner stays quiet. That docstring still points at
`web/src/app/core/upkeep.ts`, which moved to `js/src/`.

**`verify_build` authenticates a reviewer, not a user.** It gates on the program
comparison (`raise SystemExit(1)`) but checks only approval and clear state: not
the creator address, not the state schema, not extra pages, not `frozen`. The
creator is exactly who holds `update` and `freeze`, so verifying bytecode says
nothing about who can replace it. A dirty tree is printed and not gated. And
nothing connects any of it to the console, which shows no hash, no verification
date and no "this is the app the release recorded". I grepped `web/src` and
`js/src` for `sha256`, `hash` and `verify` and found nothing.

**Release hygiene has regressed in a different direction than last time.** CI is
green now, which was my M3, but:

- `docs/status.md`, the page I was told to read first and which calls itself the
  one place to look, still lists `treasury` and `deadman` as **"not yet"** at
  lines 48 and 49 with the exact issue numbers this branch closed, and is stamped
  "Last updated 2026-08-25" at a commit that is later than that.
- `README.md:289` and `docs/arcron.md:14` say **alpha-1** while
  `docs/releases.md:143` and the HEAD commit message say alpha-2; and
  `README.md:373` attributes alpha-1 to app `769891898`, which
  `docs/releases.md:142` says was `769823086`. That is a stage-to-app-id mix-up on
  the number a reader would copy.
- **There are no git tags at all** (`git tag` returns empty), while
  `scripts/multisig.py:382-384` tells a signer whose file disagrees with the tree
  to "check out the tag this was built from". `sign` has no dirty-tree check
  either (`create` does, at `govern.py:192-202`), so the digest a holder is told
  matches can correspond to no commit anyone can check out.
- `fix-verification-2026-08-25.md` is tracked at the repository root rather than
  in `docs/reviews/`.

**Unchanged and still open from my console pass:** `DEFAULT_NETWORK = 'localnet'`
(`js/src/networks.ts:64`) with no hosted override, so the hosted console's front
door is an empty LocalNet page and every real user must arrive by link, and links
are the attack medium. `web/scripts/dev.ts:8-9` still imports
`../src/app/core/keeper-abi` and `keeper-txns`, which moved to `js/src/`.
`stat-tiles.ts:74-75` still computes `escrowed` and `escrowedExact` as identical
expressions. No lane runs AXE, so "MUST pass all AXE checks" remains unverified in
CI.

**`specsync check --strict` passes 10/10 and would pass through every finding
above.** Invariant 20 enumerates the rekey and close asserts and is silent on the
sender binding; the spec is complete against what the code does and incomplete
against what it should do, which is the limit of that tool and worth stating so
nobody reads a green spec check as a security result.

---

## 5. Next steps, ranked

1. **Add `assert mbr_payment.sender == Txn.sender` and the same for
   `funding_payment` in `register`** (K1). Two lines, no struct change, plenty of
   program budget. Add a spec invariant and a test that a stranger-signed
   register is refused. This is the only confirmed theft path I found and it must
   be closed before any freeze, because after freeze it is a migration.
2. **Delete `[project.deploy.mainnet]` from `.algokit.toml`**, and make
   `smart_contracts/__main__.py` refuse any network it did not reach through
   `scripts.network` (K2). Also remove the bare `main("all")` deploy fallback.
   Consequence is permanent, cost is one commit.
3. **Make `--no-rebuild` emit a refusal instead of removing one** (K4), matching
   the treatment `expected_address` already gets fourteen lines away, and scope
   the flag to `update` as its help text claims. Add a selector check so `freeze`
   describes as `freeze`.
4. **Build the detector the pre-freeze window depends on** (K3): put `creator`
   into the notifier's `Snapshot`, alert loudly on any address not on an
   allowlist, and drop the `if previous.upkeeps:` suppression for a first run
   against an app that is supposed to be empty. Then reconcile
   `docs/status.md:126-131` with the fact that the multisig address is public, and
   decide deliberately whether the pre-freeze MainNet window survives that.
5. **Fix K5 by keying off state rather than error text**, and rewrite
   `tests/test_keeper_backoff.py:180-183` against a string algokit can actually
   produce. Grep the rest of the suite for fixtures that were written from the
   docstring rather than from an observed output.
6. **Carry the `APP_BASE_MBR` reserve into `rain` and `subscription`** (K6), and
   delete the unexplained pre-funds from their demos so the demos prove it, the
   way `deadman_demo.py` now does.
7. **Move the console's readiness gate into `KeeperService.send`** (K7) and
   delete the per-component copies.
8. **Align the ASA surcharge with all six of the contract's conditions** in the
   bot, and widen the JS client's parameter type so it can ask the same question.
9. **Doc and release sweep:** `docs/status.md`'s ships table and date, the
   alpha-1/alpha-2 disagreement and the app-id mix-up in `README.md:373`, tag the
   releases that `docs/releases.md` records, add a dirty-tree check to
   `govern sign`, move `fix-verification-2026-08-25.md` into `docs/reviews/`.
10. **Add the tail-offset fingerprint to `js/src/upkeep.ts`**, a
    `failureMessage` check to `discoverResources`, and a hosted default network
    that is not LocalNet.

---

## 6. The two numbers

### Confidence that this is safe for real money on MainNet today, given that the deployment is upgradeable and its app id is unpublished until frozen: **5 / 10**

Up from 3. Everything that put it at 3 last time is genuinely fixed: CI is green
at the deployed commit, the security policy tells the truth, the flagship example
works, and the console has a canonical-app guard that is well built rather than
merely present.

It is 5 and not 7 for three reasons, none of which is the upgradeability.

The first is K1. It is a confirmed path, proved on chain, where a third party's
money ends up in an attacker's escrow, and it is in the contract that holds the
money rather than in an example. Five review rounds and four audits did not find
it, which tells me the review process has a blind spot around *who pays* as
distinct from *what is paid*, and I do not know what else is in that blind spot.

The second is that the trade's compensating control does not exist. The reasoning
in `docs/status.md` under "Why 90 to 95 and not 100" is sound, and I want to say
so plainly: an unfrozen contract's bugs are patchable, so buying the last few
percent of certainty really is disproportionate, and the honest counterpart is
written down more clearly than most projects manage. But the whole argument turns
on "that allowance is ours and does not transfer", and the mechanism that keeps it
from transferring is "the app id is not published anywhere". The address that
will create the app is published in six files including `SECURITY.md`, and the
tool that is supposed to notice a stranger cannot tell a stranger from us. So the
window is currently protected by the repository being private, which is a state
the roadmap ends before freeze.

The third is K2. If MainNet is ever created through `algokit project deploy
mainnet`, the multisig gate is not merely bypassed, it is bypassed permanently,
because the creator cannot be changed. That is a config file away from turning
the whole governance design into decoration.

**What moves it to 7:** K1's two asserts; deleting the `.algokit.toml` MainNet
profile; the `--no-rebuild` refusal; and a notifier that can say "that upkeep is
not ours". Those are days of work, not weeks. **What moves it past 8** is
sustained unattended TestNet time with the detector actually running, plus at
least one outside person registering an upkeep from the docs alone, which remains
the largest genuinely unknown thing about this system.

### Confidence if it were frozen at deploy: **3 / 10**

Lower, and I think that is the most useful thing I can tell you, because all
three of us previously said freeze would raise the number.

It would have raised it a week ago, when the honest state of knowledge was "we
found nothing in the contract." It does not now. Freezing today makes K1
permanent: the only remedy would be to tell every creator to cancel and
re-register against a new app id, which is exactly the outcome the whole 1.0
scope decision was built to avoid. It also makes the ASA surcharge asymmetry,
the argument-count fan-out and every other thing nobody has found yet permanent
on the same day.

And K6 is the evidence that the last-instance-of-a-class problem is live in this
codebase right now: the deadman fix was correct, was reasoned about carefully,
had its reasoning written into the demo, and was not carried to the two siblings
with the identical shape. That is a fine failure mode for a contract you can
patch. It is the wrong failure mode to freeze around.

**What moves the frozen number:** K1 closed, then a period long enough that the
fix-one-site pattern has been swept systematically (grep every constant and every
guard that appears in exactly one contract and ask why), then a paid audit. The
gap between 5 and 3 is what the upgradeability is buying today, and on my
numbers it is buying something real.

### Is the trade wrong?

**No.** The reasoning is right and better argued than most projects manage. Two
corrections, both to the justification rather than the decision:

- "The app id stays unpublished" is not a control you currently have. Either stop
  relying on it, or hold `MAINNET_CREATOR` and the multisig address out of the
  public tree until freeze, and build the detector.
- "We can fix it in place" is only true while the deployment's creator is the
  3-of-5. K2 is a live path to a MainNet app whose creator is one key, and in
  that world the trade has no remedy leg at all.

---

## 7. Would I escrow my own money here on MainNet today, given the upgradeable-and-unpublished shape?

**No.**

Not because of the unfrozen key. I would take that bet: the disclosure is honest,
it is on-chain checkable, it is surfaced in the console at the point where money
is committed, and the reasoning for it is sound.

I would decline because of K1, which I proved rather than inferred: a signature I
give in a group I did not read closely enough hands my escrow to whoever composed
the group, with the receiver being the genuine app account of the genuine app id,
which is the exact check the project has taught me to perform. And because K2
means the deployment I would be trusting might not have the creator the
governance design says it has.

Both are days of work. Close them and ask me again, because the thing underneath
is worth trusting: I spent a real effort trying to break the escrow accounting,
including on a live chain, and I could not.

---

## 8. The single thing most likely wrong that I did not check

**`web/src/app/core/wallets.ts` and the `use-wallet` signing surface.** I read
`wallet.service.ts:117-122` far enough to see it hands algosdk
`manager.transactionSigner` and never composes a group itself, which is why the
canonical console is not the delivery vehicle for K1. I did not read
`wallets.ts`, did not check which indexes each adapter actually signs, and did not
drive a wallet. That surface is precisely what K1 depends on: if any adapter
signs by position rather than by matching sender, or if `transactionSigner` can
be handed a group it did not build, K1 stops needing a hostile front end and
becomes reachable from the console itself. It is the one place where the finding
I am most confident about could turn out to be worse than I have written it.

Second most likely: the ASA clawback and freeze edges in `execute` and `cancel`
on a real chain. `scripts/clawback_e2e.py` exists and is in the `local` lane, but
I did not run it, and the unit tests mock inner transactions entirely.

**Also not checked:** the `local` and `endurance` lanes (I ran `pytest` and
`bun test` only), the soak and scenario scripts, `verify_build` against the live
app, the npm and Python supply chain, and `rain`'s beacon-randomness fairness
beyond what the contract subagent reported.
