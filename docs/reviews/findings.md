# What the reviews found, and what is left

Twenty-two review files, about 182,000 words, and until now no way to ask them
the only question that matters: what did they find, and what happened to it.
This page is that index. The reviews themselves stay unedited, including the
parts that are wrong, for the reason `README.md` gives.

Built on 2026-09-02 by reading all twenty-two files, extracting every distinct
finding, and checking each one against the tree as it stood at `7de0cde`.

## The numbers

**881 distinct findings** were raised across five review rounds. **761 of them
have been checked against the tree**; the other 120 are named at the bottom of
this page as unchecked, because saying so is cheaper than implying otherwise.

The first 608 were checked one file at a time and are the table below. The
2026-09-01 round's 153 were checked separately, by hand, and are summarised in
their own section rather than merged into the table, because their residue has
a different shape.

Of the 608:

| | count | what it means |
|---|---|---|
| fixed | 285 | closed in the tree, with a citation that was opened and read |
| superseded | 61 | the component left the repository, and no surviving sibling has the same defect |
| accepted | 34 | a deliberate risk, written down in `docs/security.md` or a design doc |
| refuted | 8 | wrong when written, and the tree records why |
| **open** | **197** | nothing in the tree closes it |
| decision pending | 9 | deferred on purpose, to a named moment |
| unverifiable | 14 | needs a chain read or a live run this pass could not do |

So **roughly seven in ten checked findings are closed**, and the residue is
220 rows of open, pending and unverifiable, listed in full below.

Read the open list for what it is. Most of it is small: 101 rows are `low` and
24 are `nit`, and 38 are recommendations somebody made rather than defects
anybody found. The ten that carry real weight are named next.

## What actually matters in what is left

**The open findings concentrate in governance, and governance is the one path
that has never been run for real.** Forty-five of the 220 are in the signing
and multisig code, and they sit beside a row saying the create ceremony has
never been executed across two machines. Every other area's residue is docs
drift and console polish; this one is the MainNet path.

Three of them, in the order I would fix them:

1. **The create ceremony does not check what it displays.** `multisig.refusals`
   compares programs, and its `extra_pages` and schema checks live behind
   `if is_app_call and in_file != 0` (`scripts/multisig.py:392`), which is the
   *update* branch. On a create, `in_file` is 0, so a coordinator can inflate
   extra pages (permanent creator minimum balance) or set a schema that bricks
   `__init__`, and `sign` raises nothing. `show` prints both
   (`multisig.py:189-191`), which is the shape the repository has already
   named: printing a value asks somebody to compare it, and the comparison is
   the check.

2. **The MainNet pre-freeze window rests on a control that does not exist.**
   `docs/status.md:206` and `:228` both say the app id stays unpublished until
   freeze. The creator is named in the same sentence, and an app id is one
   indexer query from a creator address. This got worse rather than better when
   #202 moved the creator from a multisig to a single named account: there is
   now exactly one address to query.

3. **`docs/status.md:121` says the keeper contract has "no unresolved
   findings".** This page is the counter-example. That row was true of the
   rounds it was written about and is not true of the record as a whole.

The rest of the top ten: `opt_in_asset` takes an MBR payment with no upper
bound and strands the surplus with nobody able to recover it
(`contract.py:518`, a fix that needs no struct change); no test exercises the
console's per-box decode catch, so deleting it breaks nothing; `attacks.py`
still lacks the `top_up_asset` and `opt_in_asset` siblings of the attack it
does carry; `sync_site_docs --check` runs in no workflow, so the published
mirror can drift ungated; and `docs/console-plan.md:544` still says nothing has
been pushed, of a console that has been live for days.

## What this pass corrected in itself

Nine rows were re-checked by hand after the automated pass and changed. Worth
recording because they are the same failure the lessons below describe.

- **`govern sign --no-rebuild` was marked a high open finding and is closed.**
  `scripts/govern.py:359` refuses outright when the flag meets a file carrying
  programs. The comment there already cites the reasoning it was applying.
- **The escalation headline was marked open and is fixed** (`escalation.md:44`
  reads 258 and 18.4%, corrected in #241).
- **A digest alarm I raised myself was wrong.** `docs/deploying.md` prints
  combined `c94c6e0c…`, and hashing the local build without the separator byte
  made it look like a mismatch. Hashed the way `verify_build` does
  (`approval + b"\x00" + clear`) it matches. What is genuinely stale is the
  *approval* line beside it, `433a0418…`, against today's `1b60506e…`, in an
  illustrative block whose next paragraph explains why only the combined digest
  should be compared. Low, not the blocker it was filed as.
- Five spec findings against `embargo`, `treasury`, `watchdog` and `rain` were
  open against specs that no longer exist.

And one the reviews never raised, found while checking them:
**`specs/keeper/keeper.spec.md:38` says the contract stays "inside one
2,048-byte program page".** The approval program is 2,219 bytes across two,
which is what `docs/status.md:28` says. `specsync check --strict` passes over
it, because it reads structure rather than arithmetic, which is the drift four
separate reviews warned about in almost the same words.

## The lessons

Six patterns, each with at least three instances in the table below.

**A fix lands where the review pointed, not where the bug class lives.** The
canonical instance is #105: `deadman`'s minimum-balance trap was fixed in
`deadman` while `rain` and `subscription` carried the identical shape. The same
pattern produced the payer binding landing at one of four payment sites, the
adoption premise surviving in six documents after being retired in one, and one
figure being corrected in three places and left in a fourth. *Rule: when a fix
is written, grep for the shape, not the symbol, and fix every site in the same
commit or record why not.*

**A check's coverage gets asserted without being checked.** A spike that
exited green without measuring anything; a test whose last assertion could not
fail; a request-count test that hand-added a constant and ran a fifth low; a
damage scan that reported finding all three duplicated passages when seven
existed. *Rule: a check that has never failed has not been tested; break it on
purpose once and watch it go red.*

**Numbers from two snapshots get mixed under one dateline.** README and
START-HERE carried counts from two different rounds inside one dated claim , 
in the commit whose subject was fixing datelines. *Rule: every number in a
dated block comes from one round, and the round is named in the block.*

**Documents teach rules the contract does not have.** Dormancy, call shape,
multisig shape and foreign references (#100); catch-up described as the only
outage behaviour when SKIP_AHEAD is the console default; "no admin key" over an
unfrozen app. *Rule: a claim about behaviour cites the line that implements it,
or it is a wish.*

**A structural check passes over a semantic falsehood.** `specsync --strict`
is green on a tree whose prose contradicts the contract, four reviews said so
independently, and the one-page claim above is a live instance. *Rule: green
spec-sync means the sections exist, and nothing about whether they are true.*

**Every claim of an outsider has turned out to be us.** Upkeep 81, upkeep 110,
`RABTKCI5`, `CEPY52VZ`: each looked like a stranger and each was an agent we
dispatched. The count of upkeeps registered by somebody who is not us is zero,
and this page's own review panel is the newest instance of it; `README.md`
records that.
*Rule: an address is not a person; before writing "somebody else", say who.*

## The open list

Weight is the reviewer's own. "Who found it" is the earliest reviewer to raise
it; where several found the same thing, the table keeps the first.

### The contracts (23)

| what | who found it | weight | what closing it takes |
|---|---|---|---|
| opt_in_asset MBR payment has no upper bound; surplus is stranded | F console-1 | high | Closing it takes `==` at contract.py:518 (a code fix, not a struct change) or one sentence under "Overpaid MBR is not returned" naming opt_in_asset. |
| security.md "only multiply in the contract" claim is false | F 8-25 | low | Closing it takes rewording docs/security.md "Arithmetic" to list the four multiplies with their bounds (MBR by argument size, refund by stored byte... |
| ASA clawback/freeze edges in cancel/execute only mocked | F 8-25 | recommendation | overturned: The reviewer named 'the ASA clawback/freeze edges in cancel/execute on a real chain'. The e2e closes clawback on both paths and freeze ... |
| Pulse spec says no asserted error paths; tick_with asserts a cap | G 8-25 | low | Closing it would take one Error Cases row for `tick_with` over the cap. |
| Keeper spec says register MBR is exactly box cost; code is >= | G 8-25 | low | Closing it would take changing invariant 6 to "at least" and noting the surplus stays in the app account. |
| Pulse spec types omit last_note | G 8-25 | low | Closing it would take adding `last_note: string` to the Exported Types row. |
| Subscription set_keeper accepts a nonexistent app id, bricking billing | K 8-25 | low | Closing takes either an on-chain existence read of the app before storing it, or recording it as accepted in the spec. |
| Subscription min_rounds_per_period has no floor beyond > 0 | K 8-25 | low | Closing takes a floor (the keeper's MIN_INTERVAL_ROUNDS would do) or a spec sentence saying the floor is the deployer's promise, not the contract's. |
| Pulse tick_with panics on notes over 119 bytes | K 8-25 | low | Closing takes a note-length assert (or truncation) and a spec row; self-inflicted and refundable via cancel, so Low. |
| Record the AVM's canonical ARC-4 decoding guarantee in the spec | F rescore | recommendation | The property the whole struct-in-a-box design leans on is asserted in a review and nowhere the spec-sync checks. Closing takes an invariant in keep... |
| ASA clawback and freeze edges in execute/cancel not exercised on chain | F rescore | recommendation | The script exists and is wired, but nothing in the tree or in CI shows it passing against the current build. Confirming takes `fledge lanes run loc... |
| local/endurance lanes, verify_build vs live app, supply chain unchecked | F rescore | recommendation | Three of the four surfaces need a live run or chain read; the fourth (supply chain) is unaddressed in the tree and would be 'open' on its own: clos... |
| set_keeper(0) leaves the one-shot lock unengaged | G rescore | low | Since 9011b7b subscribe refuses while keeper_app == 0 (:193) and set_keeper can be re-called, so nobody is trapped; closing it takes `assert keeper... |
| Bot pays ASA surcharge against book value, not real holding | K rescore | low | Bounded 1,000 uALGO per execution when an issuer has clawed back or frozen the app's holding. Closing it: read the app's asset holding and frozen f... |
| Fixes land where the review pointed, not where the bug class lives | K rescore | recommendation | The specific instances are closed, the pattern is not: there is no mechanical check that every escrowing contract or deploy path reserves the base ... |
| MainNet: creator must be the multisig, freeze decided on a date | audit 9-01 | recommendation | One of the three conditions is closed (funding proof is a release gate), one is explicitly rejected with reasons (single-key creator, security.md),... |
| Escalation self-heals in one cycle, not two | K branch | low | Closing takes one sentence at each of the two sites saying the next on-time execution pays base (one cycle), so the residual claim is exact rather ... |
| cancel's best-effort ASA refund can pay out other upkeeps' balances | K branch | low | Closing takes one paragraph in docs/security.md's ASA best-effort item saying the ASA holding is pooled and a clawback is first-cancel-takes-all, o... |
| raise_fee is the right remedy over the spread ramp | K branch | recommendation | The recommendation Kimi made is now the named option 2 with a named decision point; it is deferred to a moment, not to nowhere. |
| Replaying the ramp makes the escalated/base split exact; clamp never fired | K branch | recommendation | Closing takes replaying the ramp per execution from `last_serviced_round` and the confirmed round and stating 'clamp never fired' or the count of t... |
| Keeper can top up in-group to collect cap instead of fallback | audit 9-01 | nit | The bypass only exists while escalation exists (needs fee_cap > base), so it rides on the escalation decision scheduled before freeze. Doc gap: sec... |
| top_up_asset accepts a zero asset amount | audit 9-01 | nit | Closing it takes one assert (`asset_funding.asset_amount > 0`) mirroring top_up, a unit test in tests/test_keeper.py, and a failure-table row plus ... |
| top_up_asset binds sender, not asset_sender; clawback transfers pass | audit 9-01 | nit | Closing it takes either an assert that `asset_funding.asset_sender == Global.zero_address` (with a test and a spec row) or a sentence in docs/secur... |

### The keeper bot (10)

| what | who found it | weight | what closing it takes |
|---|---|---|---|
| Bot scan_upkeeps aborts on one undecodable box | G rescore | medium | On the canonical keeper only register writes those boxes, so this only bites on a look-alike; closing it takes a per-box try that counts undecodabl... |
| Three docs print keeper_bot commands the tooling refuses | F docs | medium | Closing it is adding `--app-id 769891898` (or `KEEPER_APP_ID=769891898` to .env.testnet.template) at the five sites, the way docs/hosting.md:313-35... |
| Within-scan opt-in staleness is bounded and self-inflicted | fixver 8-25 | low | overturned: The reviewer accepted a bound of one scan with at worst one backed-off execution or one 1,000 uALGO overpay, self-healing next scan. Si... |
| Bot surcharge decision branch has no unit test | fixver 8-25 | low | Closing takes factoring the decision into a function and a unit test for each of the four cases (no asset, asset but short, asset but not opted in,... |
| Backoff.save() OSError mislabels failures and double-backs-off | K rescore | low | The unit fix makes the trigger unlikely, but a genuinely unwritable state path still mislabels a successful execution as a failure. Closing it: cat... |
| Two docs call the TestNet app 'canonical', contradicting keeper_bot | F docs | low | Closing it is replacing 'canonical' with 'the live TestNet keeper app (769891898)' in the three example sites. |
| wait_for_work's dev-mode LocalNet comment describes behavior that does not exist | K branch | low | Closing takes one LocalNet run showing what `status_after_block` actually returns in dev mode and adjusting the docstring or the early-return to ma... |
| keeper_bot docstring overclaims missing frozen key means frozen | fixver 8-25 | nit | A nit; closing takes one scoping sentence in the docstring. |
| Bot main() DEPLOYER fallback path is not unit-tested | G rescore | nit | Closing it takes a test calling main([...]) with KEEPER unset and a stub algod reporting frozen=0, asserting the refusal. |
| --app-id help text claims a TestNet default resolve_app_id lacks | K branch | nit | Closing takes deleting ', else the TestNet app' from the help string; the two sites still disagree. |

### Scripts and spikes (13)

| what | who found it | weight | what closing it takes |
|---|---|---|---|
| scripts/attacks.py covers register only; no standing top_up attack | F console-1 | high | The register-only state is gone; closing the finding as written takes the two remaining sibling attacks beside the top_up one. |
| Quoted spike numbers are not stable across runs | K branch | medium | Closing takes either rounding those figures in the creator- and security-facing docs or appending 'one LocalNet run' at each site; the caveat was f... |
| Reentrancy spike TypeErrors, asserts nothing, runs in no lane | K 8-25 | low | Two of the three parts are closed; closing the last takes one step in lanes.local. |
| Someone other than the author should run e2e and soak clean | K 8-25 | recommendation | Closing takes one reviewer running fledge lanes run endurance and writing the result down. |
| verify_build prints commit and dirty flag but never asserts | F console | low | Closing it means a --require-clean (or tag) flag that exits non-zero when the tree is dirty or not at the recorded release commit, and using it in ... |
| rain and subscription demos still pre-fund the app account, uncommented | F rescore | low |  |
| No outsider has registered an upkeep from the docs alone | F rescore | recommendation | Still true and honestly recorded. Closing takes an outsider registering from the docs alone and surviving a redeploy; whether the notifier is runni... |
| reclaim.py does not paginate boxes | K rescore | low | An app with more boxes than one page returns would be reclaimed partially. Closing it: replace the call with keeper_bot._box_names. |
| notifier.py treats any app call as an execution | K rescore | low | Narrower than stated: the count is right, the keeper name under it can be wrong. Closing it: match apaa[0] against the execute selector (and ideall... |
| notifier.py sleeps for an unbounded Retry-After | K rescore | low | A hostile or misconfigured Retry-After stalls the notifier indefinitely. Closing it: `min(retry_after, cap)`. |
| Reentrancy spike repaired; spike_asa_fee.py still patches call_data | G mainnet | low | Still a measurement rather than the create path, but the script now fails closed rather than measuring anything. Closing it: re-anchor the patch on... |
| read_escrowed placeholder arguments are dead today but fragile | K branch | nit | Closing takes either a `read_upkeeps` signature that makes those parameters optional for a sum-only read, or a docstring that names every field the... |
| reclaim.py --commit loop iterates found, not ours | K branch | nit | Closing takes changing the loop's iterable to `ours` and a test that a foreign upkeep in `found` is reported and not cancelled; unchanged since the... |

### Governance and signing (45)

| what | who found it | weight | what closing it takes |
|---|---|---|---|
| Pre-freeze 'app id unpublished' control is false: creator address public | F rescore | high | The premise the reviewer attacked was moved from a multisig to a single named account (#202) and is still published, so the finding got stronger, n... |
| govern sign --no-rebuild silently removes the program comparison | F rescore | high |  |
| Create extra_pages and schema are display-only at sign | G rescore | high | Closing it takes the same comparison refusals() already does for updates applied to a create's extra_pages and global/local schema, with one test. |
| A freeze payload reaches signers as unlabeled hex | K 8-25 | medium | Closing takes decoding the selector in describe_transaction and printing an unmistakable irreversible-freeze line for the multisig file path. |
| Freeze/NoOp files show hex; selector never checked or labelled | G rescore | medium | Closing it takes decoding the first app arg against the ARC-56 method list and printing the method name, with a refusal (or explicit type-back) whe... |
| govern._refuse rebuild wiring and CLI sign are untested | G rescore | medium | Closing it takes one test of govern.main sign with the rebuild monkeypatched to produce different bytes, asserting the refusal. |
| docs/deploying.md still sells deploy-mainnet as working | G rescore | medium | Closing it takes one path that actually executes for the #202 single-account creator, documented once, with the dead row removed. |
| govern sign checks one read of the file and signs another | K rescore | medium | Still a code flaw in tooling that is kept and exercised by `fledge run smoke-multisig`, though no longer on the MainNet path since the creator is o... |
| App args reach holders as unlabeled hex; decode the selector | K rescore | medium | A freeze file still arrives as unlabeled hex. Closing it: decode app_args[0] against the ARC-56 method selectors and print the method name (freeze/... |
| --yes documented as freeze-only but skips create and sign pauses | G mainnet | medium | What remains is a help-text/wiring mismatch on the coordinator's own step, not the second-signer problem the reviewer cared about. Closing it: eith... |
| A freeze file still shows app_args as unlabeled hex | K fixver | medium | Closing it: decode `app_args[0]` against the known selectors (`freeze()void`, `update()void`) in `describe_transaction`, print a 'PERMANENTLY FREEZ... |
| deploy.py refuses multisig; MainNet create path is a hand-rolled e2e | G 8-25 | low | A first-class MainNet create path still does not exist. Closing it would take teaching `govern create` and `require_mainnet_creator` to accept the ... |
| Runbooks say 2-of-3 multisig; review prompt said 3-of-5; pick one | G 8-25 | recommendation | The decision was made and written down; the tree still names a 3-of-5 in two operator-facing places. Closing it would take deleting those two strin... |
| Multisig should create, verify bytecode, and freeze before outside money | G 8-25 | recommendation | Create-verify-freeze-then-invite is the recorded plan, deferred to the MainNet create; note that the tooling to perform that create from the record... |
| set_keeper(0) should be rejected | G 8-25 | recommendation | Because 0 is the unset sentinel, a zero call leaves the contract still configurable and only strands the extra 0.1 ALGO base-MBR payment, so the im... |
| govern_e2e only updates empty apps; escrow never crosses an update | K 8-25 | low | Closing takes a govern_e2e stage that registers, updates, executes against the new programs, then cancels and checks solvency. |
| multisig_e2e never exercises freeze | K 8-25 | low | Closing takes a freeze stage through the multisig file path (which is also where govern-freeze-unlabeled-hex lives) or a docstring that stops promi... |
| specs/keeper/testing.md lacks ASA-bonus and governance coverage rows | fixver 8-25 | low | Closing takes adding rows for the ASA-bonus, governance and rekey/close sweeps to the coverage table. |
| govern refusals never inspect method selector or on-completion | F rescore | low | Exposure narrowed: the multisig CLI is no longer the MainNet governance path (#202, scripts/network.py:100-131), and web-govern builds freeze itsel... |
| No git tags exist while multisig tells signers to check one out | F rescore | low |  |
| govern sign has no dirty-tree check | F rescore | low |  |
| Upgradeable-until-frozen trade is right; two justification corrections | F rescore | recommendation | One of the two corrections is in the tree. Closing is the same edit as finding 6: rewrite the status.md paragraph so the trade rests on the detecto... |
| --i-mean-to-rekey also permits payment and asset closes | G rescore | low | Closing it takes separate flags (or an unconditional refusal of the two close fields, since no ceremony ever needs them). |
| Sign rereads the file after the confirmation pause | G rescore | low | Closing it takes reading the file once, describing and signing that same buffer (and printing its digest for the signer to compare). |
| Dirty-tree check has no cwd and ignores git exit code | G rescore | low | Closing it takes check=True (or an explicit returncode test) with a unit test that stubs a failing git. |
| Whether TestNet/MainNet enabled AppSizeUpdates is unchecked | G rescore | low | Deciding it needs a live read of the consensus version on both networks; for the tooling it is moot because multisig.py:392-407 refuses a resize ei... |
| _can_sign returns True when nacl is missing | G rescore | low | Closing it takes returning False (or raising) when nacl cannot be imported; algosdk pulls nacl in, so the branch is theoretical but still fail-open. |
| Create ceremony never executed across two machines | G rescore | recommendation | Closing it takes implementing the single-account create path #202 chose and rehearsing it end to end on TestNet from a clean checkout, recorded in ... |
| --yes still skips the create type-back despite freeze-only help | G rescore | low | Closing it takes not passing --yes to create (or fixing the help to admit it does), with a test. |
| Combined program digest has no domain separation | K rescore | low | Not exploitable, but the window the review named ('cheap to fix before it is recorded in a release') has passed: the format is now in the release t... |
| Create checklist does not print the network/genesis it targets | G mainnet | low | Closing it is one line: print args.network and the genesis id from the suggested params on the checklist itself, next to the creator. The sign side... |
| extra_pages formula correct but no test pins the page boundaries | G mainnet | low | A test with the right name exists and passes, but a 'simplify the formula' edit to govern.py:247 would leave CI green, which is exactly the failure... |
| JSON sidecar vs blob disagreement is still not refused | G mainnet | low | Not a theft path, as the reviewer said. Closing it: either refuse when the sidecar fields disagree with the blob, or stop writing them in export_un... |
| Use govern create only as the writer, not as the check | G mainnet | recommendation | Nothing in the tree writes a create that corvid.algo can sign from a wallet, and web-govern offers freeze only (web-govern/README.md). Closing it: ... |
| txn.sender never compared against the multisig blob address | K fixver | low | Closing it is one refusal clause `txn.sender != blob_address(path)` plus a test; note the mismatch is a valid on-chain signature for any account re... |
| Create sign displays but does not refuse schema/extra-pages mismatch | K fixver | low | Closing it: in `_refuse`, when `carried_programs` is not None and app id is 0, compute the expected extra pages and schema from the rebuilt spec an... |
| Run the create signing ceremony cross-machine before MainNet depends on it | K fixver | recommendation | Closing it: run `govern create` -> `govern show`/`sign` from a second checkout (a `git worktree` at the tag would do) and record the outcome, or re... |
| docs/deploying.md prints the alpha-2 digest as the live app's | release audit | low | corrected by hand on 2026-09-02, in session |
| Add a test pinning the multisig prose, not only the address | F docs | recommendation | The specific 2-of-3 sentence no longer exists, but the gap the reviewer named stands: closing it is a test asserting MAINNET_CREATOR appears in tho... |
| Extra-pages test comments the old 2,108-byte size | G rescore | nit | Closing it takes deriving the pinned size from the artifact or updating the comment and assert to 2219 + 4; the formula itself still holds (one ext... |
| show requires --app-id and then ignores it | G rescore | nit | Closing it takes either dropping show from that list or comparing the file's app index against --app-id and warning on mismatch (the more useful ch... |
| Refusal message names a nonexistent --i-mean-to-resize flag | K rescore | nit | Still fail-closed and cosmetic: the refusal cannot be overridden and the message names a flag that does not exist. Closing it: add the flag or rewo... |
| Threshold/member order only implied by address match, not hardcoded in create | G mainnet | nit | On MainNet the guarantee no longer rests on the CLI argument because the network.py pin runs first, which is the mitigation the reviewer already cr... |
| Genesis allowlist entry devnet-v1 matches no known real chain | K fixver | nit | Closing it is one line: either drop `devnet-v1` or add a comment naming the node template that reports it (go-algorand's devnet genesis), so the fa... |
| govern status SHORT BY warning split across two logger calls | K branch | nit | Closing takes joining the two calls into one formatted warning; a one-line change that has not been made. |

### The console (38)

| what | who found it | weight | what closing it takes |
|---|---|---|---|
| No component test exercises the trust banner or per-box catch | F console-1 | high | Closing it takes an ArcronService test (or an e2e scenario) that plants one malformed box and asserts one row is dropped and `undecodableBoxes` rea... |
| Console shows no verify_build hash or verification date | F console-1 | medium | Closing it takes rendering the approval/clear hash the console read from chain (or the pinned build hash) with a date beside the identity line. |
| Activity log is in-memory, capped at 8, lost on reload | F console-1 | medium | The explorer link is new; persistence and export are not there. Closing it takes storing entries per viewer or an export, or a written decision tha... |
| ASA fees registrable from console but not operable or visible | F console-1 | medium | The bonus balance is visible now; funding it is still impossible from the console. Closing it takes opt-in and top-up controls on /u/:id or removin... |
| wallet-kmd-e2e.ts fails on stale imports; no lane runs it | F console-1 | medium | Closing it takes repointing the imports at `@corvidlabs/arcron`, running it against LocalNet KMD, and adding it to `[lanes.local]`, or deleting it. |
| Missing frozen key still reads as frozen; defaultAppId optional | F console-1 | medium | Closing it takes failing closed when the key is absent (report unknown or unfrozen) or making `defaultAppId` required. |
| Console computes no build hash and discards approvalProgram/creator | K plan | medium | Closing it is reading params.creator and approvalProgram/clearStateProgram in refreshApp, hashing them with the existing combinedDigest, and render... |
| verify_build checks bytecode only, not creator, schema, pages or frozen | F rescore | medium |  |
| Console can register an ASA bonus it cannot finish or fund | G rescore | medium | Closing it takes an opt-in button and an asset top-up form on the upkeep page, or refusing feeAsset > 0 in the register form until they exist. |
| first-upkeep.md teaches ?network=&app= URL that is inert outside dev mode | F docs | medium | Closing it is dropping the query string from both URLs and, if the LocalNet path needs it, saying that `?dev=1` is what turns the parameters on. |
| JS networks.ts has no MainNet entry | G 8-25 | low | Deferred to after the MainNet create and freeze, by name. |
| Console register box reference races concurrent registration | K 8-25 | low | Closing takes re-reading next_upkeep_id and retrying once when the failure names a box reference, or naming the race in the error. |
| web/scripts/ live-chain harness imports paths deleted in js/ move | K 8-25 | low | Closing takes repointing the imports at @corvidlabs/arcron or deleting the two scripts and their README lines. |
| Console can register ASA-bonus upkeeps but not fund them | K 8-25 | low | Recorded as unbuilt, not as accepted; closing takes the two actions on /u/:id or hiding the two fields until they exist. |
| Activity log states hostile contract return values as fact (C9) | F console | low | Closing it means rewording topUpAsset and execute the same way as topUp and cancel, or reading the box back after confirmation and logging that. |
| web/scripts/dev.ts imports modules that moved to js/src (C12) | F console | low | Closing it means pointing the three scripts at @corvidlabs/arcron (or deleting them and the README entries) and putting at least a bun build of the... |
| Console shows no verification hash or date from verify_build | F console | recommendation | Closing it means publishing the verified hash and date with the console build and rendering them beside the app id. |
| transactionSigner is handed out; console cannot refuse a foreign group | F console-1 | low | Correct for the canonical console as the reviewer said; closing it takes a signer wrapper that refuses groups keeper-txns did not build, or a writt... |
| WalletConnect is dead code; web/README describes it as configurable | F console-1 | low | The README is no longer misleading; the code path is still unreachable in the hosted build. Closing it takes injecting the id in the hosted index.h... |
| web/scripts/dev.ts still imports moved paths | F console-1 | low | Closing it takes the same import repoint as wallet-kmd-e2e.ts, or deletion. |
| aria-live on banner likely inert for hostile-link arrival | F console-1 | low | Needs a real screen-reader session (VoiceOver or NVDA) arriving via a quarantined link; nothing in the tree can settle it. |
| No lane runs AXE; accessibility claim unverified | F console-1 | low | Closing it takes injecting axe-core in web/e2e/console.pw.ts and failing on violations, or a written statement that the direct assertions replace it. |
| No Content-Security-Policy or SRI on the transaction page | F console-1 | low | Closing it takes a CSP (meta tag or site header) or a sentence in docs/security.md making the absence a deliberate choice. |
| Notices in one aria-live div may re-announce on every status flap | F console-1 | low | Closing it takes a screen-reader pass or a Playwright assertion that unchanged banner nodes keep identity across a stubbed status flap. |
| register and cancel through a real WalletManager never verified | F console-1 | low | Closing it takes fixing wallet-kmd-e2e.ts against LocalNet KMD and running it in `[lanes.local]`. |
| Console readiness gate missing at two of five call sites | F rescore | low |  |
| web/scripts/dev.ts imports modules that moved to js/src | F rescore | low | Dead script that fails at build time. Closing takes importing from @corvidlabs/arcron/keeper-abi and keeper-txns as entry.ts does, or deleting dev.... |
| No lane runs AXE, so the accessibility requirement is unverified | F rescore | low | The house rule says the console must pass all AXE checks and nothing in ci enforces it. Closing takes injecting axe-core in web/e2e/console.pw.ts a... |
| Sweep every constant or guard that appears in exactly one contract | F rescore | recommendation |  |
| JS execute overpays the ASA inner fee without an assetBalance check | G rescore | low | Closing it takes passing assetBalance/assetFee into execute and mirroring the bot's predicate so an exhausted bonus pot stops buying the extra 1_000. |
| Insolvent app is a red tile; Register not blocked | G rescore | low | Operator-side `fledge run health` now watches solvency (CLAUDE.md); closing the console side takes adding `solvent !== false` to canCommitMoney or ... |
| No JS/console MainNet entry, as intended | G rescore | low | Deferred to after the MainNet create and freeze; the reviewer's reading (intentional) is the documented one. |
| web/scripts/ still imports deleted paths | K rescore | low | Three scripts still import deleted paths. Closing it: delete them or repoint the imports at @corvidlabs/arcron (js/src). |
| Documented publishing command assumes a private sibling checkout | release audit | low | Closing it is a sentence marking the step as maintainer-only against a private checkout, or replacing '../../site' with '<site checkout>' in the th... |
| web/README.md:86-88 presents the 404.html copy as settling deep links | release audit | low | Practically moot now that the server rule has landed (deep links return 200), but the sentence still implies the copy alone settles it; closing it ... |
| State that the published console never takes an app id from the reader | F docs | recommendation | Closing it is one sentence beside each 'check the address' paragraph: on the published console the app id is fixed and no link can change it, and a... |
| keeper_bot docstring points at moved web upkeep.ts | F rescore | nit |  |
| stat-tiles computes escrowed and escrowedExact identically | F rescore | nit |  |

### The console plan (a plan, not a defect list) (22)

| what | who found it | weight | what closing it takes |
|---|---|---|---|
| 'Verification stays narrow' also cuts the narrow identity J5 requires | K plan | high | Closing it means either a low-tone canonical notice in trust-banner.ts noticesFor or an identity strip on the front door, and recording which was c... |
| docs/console-plan.md:544 says nothing has been pushed | release audit | high | Closing it takes a dated strike-through amendment like the one at console-plan.md:484-488 ('Superseded 2026-08-29'), saying the console was publish... |
| Three decided items appear in none of the five build steps | K plan | medium | Closing it means either adding build-order items for the three decisions or striking the decisions with a reason. |
| 'Mine means the connected wallet' contradicts J3 and NFD principle | K plan | medium | Closing it is either an address search under item 7 or a line in console-plan.md saying the J3 without-wallet scenario is dropped and why. |
| Plan's 'Today' baseline inherits stale, mutually contradicting AC claims | K plan | medium | The fix was a precedence rule, not a re-verification; closing it means either re-running the AC "Today" lines against the current tree or stamping ... |
| J1 front door beyond the network flip has no step | K plan | medium | Closing it is a build-order item that moves explanation above the connect prompt and a first-screen execution count. |
| Console lets creators configure ASA bonus it cannot operate | K plan | medium | Closing it is either a build item for opt-in and ASA top-up on /u/:id or removing the ASA controls from the form until it exists. |
| No-indexer stance wrongly drops a localStorage keeper session ledger | K plan | medium | Closing it is a line in console-plan.md saying the earnings question is answered by keeper-preview and web-keeper rather than the console, or a sma... |
| Plan never names who holds the upgrade key | K plan | medium | Closing it in the console is rendering params.creator next to the freeze state; the doc side is half done. |
| J1 and J5 have no build step at all | K plan | medium | Closing it is a J1 first-screen item and a J5 identity item in the build order, or a written decision that both wait for MainNet. |
| Expected-outcome wording exists in throws but renders raw red | K plan | low | Closing it is a classifier in send() that maps simulate refusals, wallet dismissals and race losses to an outcome state, rendered separately from f... |
| AC Decision 2 (TestNet money is not real) never addressed | K plan | low | Closing it is a one-paragraph decision under console-plan.md Decisions choosing between rewriting the J1 criterion and deferring J1 to MainNet. |
| Failed reads render zeros and dashes instead of 'unknown' | K plan | low | Closing it is gating the tiles on arcron.status() the way registry-table.ts already does. |
| Register-time race and wallet-on-wrong-network scenarios unplanned | K plan | low | Closing it is simulating register before the wallet opens (AC j2.md:787 question 4) and a wallet-network comparison in send(). |
| Clone to MainNet is speculative generality; cut it | K plan | recommendation | Closing it is one line striking or deferring the decision until a MainNet deployment is named. |
| Revised five-step order with hosting, shell and identity added | K plan | recommendation | Closing it is a sentence in the build order saying which of the reviewer's proposals were taken and which were declined with a reason. |
| Each plan decision should name the AC decision it closes | K plan | recommendation | Closing it is a short table mapping each AC decision to the plan decision that took it or a note that it is still open. |
| Console never reads the connected account's balance | K plan | low | overturned: The reviewer's claim named two sites, "affordability of register or top-up is never shown before signing". #117 (4188203) fixed registr... |
| Private CorvidLabs/site is referenced and its nginx internals quoted | release audit | low | Closing it is one clause in README.md:396 saying the site repository is private and the staging step is for maintainers, and optionally trimming th... |
| console-plan.md and docs/ac/*.md are unmarked internal notes | F docs | low | Closing it is the same two-line banner journeys.md got, on console-plan.md and the three docs/ac files, saying what they are and which document sup... |
| Register form has ten controls, not nine | K plan | nit | A two-line doc edit closes it; the count drifted further, not closer, since the review. |
| Four-type global search over-scoped at five upkeeps | K plan | nit | Resolves itself when item 7 is scoped; until then it is unanswered. |

### The JS client (13)

| what | who found it | weight | what closing it takes |
|---|---|---|---|
| Clients check opt-in and book balance, not contract's full bonus predicate | fixver 8-25 | medium | Closing takes checking the app account's holding and both freeze flags in both clients, with a unit test per client; the cost of the gap is a waste... |
| App account shown only truncated in footer, no copy control | F console-1 | medium | Closing it takes a copy control (or the full address in the footer), or a written decision that select-and-copy from the register form is enough. |
| Read path issues one box read per upkeep every 2.5s, unbounded | F console-1 | medium | Closing it takes backoff on a failed read, pausing while the tab is hidden, and a cap on concurrent box reads. |
| ASA surcharge checked at three of six conditions in bot, one in JS | F rescore | medium | Unchanged since the review and a live instance of the sweep pattern (finding 35). Closing takes aligning both clients with the six conditions (app ... |
| js/src/upkeep.ts reads tail-offset fingerprint but never checks it | F rescore | medium |  |
| Bot and JS hardcode a two-inner fee budget | G rescore | medium | A target whose own inner calls need more now fails closed at simulate (keeper-txns.ts:322-329) rather than on chain, but still cannot be serviced f... |
| JS register/topUp/topUpAsset take suggested fee with no ceiling | G rescore | medium | Closing it takes flatFee at the minimum (or a ceiling like the bot's MAX_OUTER_FEE_MICROALGO, scripts/keeper_bot.py:97) on those three builders. |
| JS register does zero client-side validation | K 8-25 | low | Closing takes throwing before the group is built when an argument violates the contract's exported constants, so a non-console consumer fails local... |
| discoverResources reads only group-level resources, ignores sim failure | G rescore | low | Closing it takes also folding txnResults[i].unnamedResourcesAccessed (harmless when empty) and running the spike against a target that touches an a... |
| Board netReward never credits ASA bonus, always subtracts extra fee | G rescore | low | Closing it takes crediting the bonus (or showing it separately) when the viewer is opted in and assetBalance >= assetFee, and dropping the surcharg... |
| unnamedResourcesAccessed round trip for nested inners unverified | G rescore | low | Closing it takes running it on LocalNet and either adding it to `fledge lanes run local` or recording the run and its result. |
| Do not add a MainNet entry to js/src/networks.ts before freeze | G mainnet | recommendation | The recommendation is being followed and the moment it is deferred to (freeze) is named in docs/status.md. |
| A declined transaction surfaces as bare 'Missing signatures' | F console-1 | nit | Closing it takes mapping algosdk's "Missing signatures" to a sentence saying the wallet did not sign, on the send path. |

### Specs (6)

| what | who found it | weight | what closing it takes |
|---|---|---|---|
| specs/keeper/testing.md:42 still says dormant once late | fixver 8-25 | medium | The review's target line was fixed; the same wrong rule survives one row up. Closing takes rewriting that phrase. |
| Keeper spec claims one 2,048-byte page; approval is 2104 bytes | G 8-25 | low | Closing it would take rewording the constant's rationale (it bounds `execute` branches, not the page count) and stating two pages. |
| Spec change log missing update/freeze and ASA-bonus entries | K 8-25 | low | Closing takes adding the missing rows; specsync and test_specs_match_contracts check presence, not history. |
| Doc/spec drift is systemic; specsync checks structure, not semantics | K 8-25 | recommendation | Semantic drift is still caught by review rather than tooling; there is no single fix, only the residuals listed here. |
| specsync passes while Invariant 20 is silent on sender binding | F rescore | recommendation | The reviewer's point that a green spec proves nothing about security is a reading rule, not a defect; the concrete gap is that the spec was not upd... |
| verify_build _spec() silently takes only the first arc56.json | F console | nit | Closing it means naming the spec file explicitly (or failing when the glob matches more than one). |

### Documents (27)

| what | who found it | weight | what closing it takes |
|---|---|---|---|
| docs/security.md overstates 'no admin key'; trust table omits creator | G 8-25 | high | The overstatement half is fixed; the table half is not, and the table's "Creator" label now collides with "a deployment's creator" two paragraphs a... |
| Beta/rc gates in docs/releases.md unticked while MainNet is soon | G 8-25 | medium | "Soon" came from the review prompt, not the repo; the repo's position is that the gates are not being skipped and MainNet waits on them. Nothing re... |
| Doc claims extra pages cannot be added by update | G rescore | medium | Closing it takes one consistent sentence (the tool refuses resizes; whether the network would accept one is a consensus question) written after che... |
| docs/integrating.md:35 offers example methods that were culled | release audit | medium | Closing it means replacing the three culled names with methods that exist (e.g. Subscription's `charge()uint64`) or dropping them. |
| docs/hosting.md prices Actions minutes as private-repo billing | release audit | medium | The table became wrong on publication exactly as predicted and has since propagated to the working guide; closing it means rewriting the cost parag... |
| docs/arcron.md outage section describes catch-up as the only behaviour | F docs | medium | Closing it is one paragraph: say the section describes CATCH_UP, that SKIP_AHEAD drops the backlog, and replace the pointer to issue #7 with docs/d... |
| scheduling-and-fees.md still says proposed, not implemented | F docs | medium | The 'proposed' half is fixed, the heading half the reviewer also named is not; closing it is a note under the :11 heading that governance shipped i... |
| docs/hosting.md:12 says keepers are not yet competing | F docs | medium | The sentence is still contradicted by the same file's B2 and by status.md; closing it is rewriting :8-12 to say competition has happened once on de... |
| first-upkeep.md:189-193 explains a quarantine trigger that cannot fire | F docs | medium | Closing it is deleting the bullet or reducing it to 'if you see this panel you have dev mode on; turn it off'. |
| "Nothing is skipped" contradicts SKIP_AHEAD dropping the backlog | F 8-25 | low | Closing it takes qualifying docs/arcron.md "What an outage looks like afterwards" with "under `CATCH_UP`" and stating that a `SKIP_AHEAD` upkeep fi... |
| docs/design/1.0.md still lists resource declaration as in 1.0 | G 8-25 | low | The row is stale twice over: it keeps the "declaration" label call-shapes withdrew and says discovery is missing when it exists. Closing it would t... |
| docs/arcron.md still describes 1.0 in the future tense | G 8-25 | low | Closing it would take rewriting that section in the present tense as what 1.0 is, or pointing it at docs/design/1.0.md. |
| Docs carry stale alpha stage labels and superseded-app lists | K 8-25 | low | Closing takes one sentence in SECURITY.md; the stage half is done. |
| 'Ownerless' and 'no admin key' claims are premature before freeze | K 8-25 | recommendation | overturned: The finding pointed at 'SECURITY.md; README.md; docs/ (immutability language)'. The two headline files are fixed, but a docs/design pag... |
| Run the unattended dogfood period after refreshing status.md | K rescore | recommendation | The instrument exists and is running; the time has not accrued. Closing it: 30 days with no contract change and the notifier watching, then record ... |
| docs/security.md three-party table still means upkeep creator, not app creator | G mainnet | low | Closing it: add a row (Deployer / app creator: can update and freeze while frozen is 0; cannot after) or rename the existing row 'Upkeep creator' s... |
| docs/reviews/ names model versions that may be pre-release | release audit | recommendation | De facto decided by publishing; closing it is the owner's confirmation that none of the names is under an agreement, which the tree cannot show. |
| AGENTS.md:63 points at a script inside the private design-system repo | release audit | low | Closing it is a clause saying the script lives in the private design-system repo and re-vendoring is a maintainer step. |
| Extend the release-table test to pin the stage in four docs | F docs | recommendation | Three of the four files the reviewer named are pinned; closing it is adding examples/register_upkeep.py to STRANGER_FACING (or dropping the stage f... |
| docs/integrating.md:381 says the deployment has one keeper | F docs | low | The reviewer's own count (two GitHub keepers plus a local one) was also wrong, since 5854220 found the second workflow never ran; but the replaceme... |
| README links six docs and never docs/status.md | F docs | low | Reachable in two hops via a footnote-style citation, which is not what the reviewer meant by reachable; closing it is a line in README's docs list ... |
| README never states the 0.0621 ALGO box deposit | F docs | low | Closing it is one line in README's keeper-network section: 62,100 µALGO box deposit up front, refunded in full on cancel, plus the escrow you choose. |
| Put the registry counts under the release-table test too | F docs | recommendation | Not done as asked and the alternative is recorded only in a commit message; closing it either way means a test that fails when a dated count is old... |
| Better queries show escalation never changed any keeper's behavior | K branch | recommendation | Closing takes running the three queries against the same indexer scan and adding their results under 'What that does and does not establish'; the c... |
| State the sequencing recommendation: re-ask before freeze, else remove via update | K branch | recommendation | Kimi's recommendation is the document's recommendation; the decision itself is scheduled to a named moment rather than made. |
| Referenced review file 2026-08-25-independent-security-readiness.md not in tree | G mainnet | nit | The create-time checklist and five signing attacks this follow-up verifies against exist only inside this file's reconstruction. Closing it: commit... |
| Python prerequisite stated inconsistently across docs and pyproject | release audit | nit | The reader-facing three now agree; only the agent-facing files still narrow it to 3.13 (which CI pins). Closing it is a few words in AGENTS.md:45 a... |

### CI and lanes (7)

| what | who found it | weight | what closing it takes |
|---|---|---|---|
| sync_site_docs --check runs in no workflow; mirror drift is ungated | F docs | high | Closing it takes a scheduled workflow (or a step in release-drift.yml) that checks out CorvidLabs/site and runs `sync_site_docs --check`, which fir... |
| Docs name a timed-release demo that does not exist | release audit | medium | Closing it is one clause in docs/integrating.md:69, e.g. naming Pulse alone or Pulse and Subscription's `charge`. |
| No CI lane runs AXE; accessibility requirement unverified (C12) | F console | low | Closing it means adding @axe-core/playwright to console.pw.ts against the existing matrix and letting web-render fail on violations. |
| Rotate KEEPER_MNEMONIC when the repository goes public | release audit | recommendation | Closing it is an operator action outside the tree: set a fresh KEEPER_MNEMONIC (and fund the new account), optionally noting the date in docs/hosti... |
| bug_report.yml:9 relative SECURITY.md link resolves to the wrong URL | release audit | low | I did not confirm how GitHub resolves the relative link from the new-issue page (that needs a logged-in browser), but the link is still relative an... |
| The snapshot task that refreshes the table runs in no lane | F docs | recommendation | Closing it is either deriving the coverage table from a committed snapshot that release-drift.yml refreshes daily, or rewording docs/arcron.md:264-... |
| Hostile-target spike in the local lane cannot catch race regressions | K branch | low | Closing takes either a spike variant that runs with `keeper_race` alongside and asserts the attacker still nets a gain under contention, or a note ... |

### Everything else (16)

| what | who found it | weight | what closing it takes |
|---|---|---|---|
| Connected account's balance never read, shown or checked | F console-1 | medium | Closing it takes running the top-up amount through the same affordability check the register form uses. |
| Register form offers no example, demo target or explanation of use | F console-1 | medium | Closing it takes a "try it against the demo target" affordance naming pulse 769891902, or an example block above the form. |
| Enforce deploying only from a green CI lane | F 8-25 | recommendation | Closing it would take deploy.py / govern create+update refusing (or recording in the release row) unless `fledge lanes run ci` passed at the exact ... |
| A test asserts >= 2 inner txns where == 2 is the invariant | K 8-25 | low | Closing takes == 2. |
| Subscription, treasury and embargo residuals still open | fixver 8-25 | low | Closing takes either an assert plus test for each or a sentence in the subscription spec accepting them. |
| Sender match is against every wallet account, not the shown one | F console-1 | low | Closing it takes a wrapping signer that asserts every transaction's sender equals the displayed active address, or a sentence accepting the adapter... |
| Header app-id input parses with Number(), unlike entry.ts | F console-1 | low | Closing it takes routing `setAppId` through `parseAppId`. |
| Uncontrolled top-up input may be overwritten by the poll | F console-1 | low | Still reasoned safe rather than observed; closing it takes a Playwright step that types an amount, advances the stub round, and asserts the field k... |
| Rain configure test proves once, not creator-only | G rescore | low | Closing it takes one subscription test calling set_keeper from a stranger and asserting the refusal. |
| TestNet beacon id has no authoritative Foundation source found | K fixver | low | Informational now, since no contract in the tree reads it. Closing it is a citation: add the Foundation's published TestNet beacon id source beside... |
| Confirm the public maintainer identity in pyproject and copier answers | release audit | recommendation | De facto decided by publishing with it in place, but nothing records it as deliberate; closing it is the owner's one-line confirmation (or a line i... |
| 149 commit trailers carry private Claude session URLs | release audit | low | The reviewer's own status was 'decided', but nothing in docs/security.md or a design doc accepts it and the trailer is still added to every commit;... |
| pyproject.toml lacks license, repository and homepage fields | release audit | low | Closing it is three lines in [tool.poetry]; with package-mode = false they are informational only, which may be why nobody added them. |
| copier answers still name hello_world and corvid_vault | release audit | low | Closing it means editing the recorded answers despite the header (they are just recorded answers), or deleting the file if `copier update` will nev... |
| Experiments assume MainNet consensus parameters match LocalNet | audit 9-01 | nit | A standing caveat rather than a defect; closing it would take recording the consensus version the spikes ran under and having verify_build or clock... |
| Fallback test docstring overclaims "strictly better" at balance == base | K branch | nit | Closing takes rewording the docstring to say what the assertion proves (the bypass takes more than the clamped fee, up to the escrow), one sentence. |
## The 2026-09-01 round, checked by hand

The keeper audit, its verification and the three-model panel on it: **153
findings**, checked on 2026-09-02 without the automated pass.

| | count |
|---|---|
| closed by #237, #238 and #241 | 56 |
| accepted, and written down as accepted | 20 |
| deferred to the moment before `freeze` | 2 |
| open | 75 |

The residue has a shape worth naming. **Fifteen of the 75 are corrections to
the review documents themselves**, and those stay as they are: an arithmetic
slip inside a review is evidence of what the reviewer thought on the day, and
the no-edit policy in `README.md` is the reason the whole directory is worth
anything. Most of the remaining sixty are nits, refutations of the audit's own
sentences, and recommendations, all of them already argued in
[`../design/escalation.md`](../design/escalation.md).

Four named live sites were genuinely stale, and this commit fixes them:

- **`CLAUDE.md` and `AGENTS.md` both still called upkeep 91 a live loose end.**
  It was cancelled on 2026-09-01 and `docs/testnet.md` was updated to say so,
  in the same session. Two of three sites, which is the first lesson above,
  found by the panel and not acted on until now.
- **`docs/design/1.0.md` still argued that escalation "is what makes a small
  user's upkeep reliable at all"**, which is the premise `escalation.md`
  spends a page refuting. That row now says the claim is why the feature
  shipped and that it has not held up.
- **`docs/integrating.md` quoted one run of the hostile-target spike as
  measured fact.** The spike asserts a shape, not the figures; the paragraph
  now says which it is.

Three the panel raised against live code were checked and are closed: the
spike escapes on every path where the attack does not reproduce
(`spike_hostile_target.py:224,254,281,298,315`), the request-load figure is
measured at eight per execution rather than a hand-added constant
(`tests/test_keeper_bot.py:663`), and `keeper_backoff`'s docstring already says
the sibling variant costs the attacker less and pays them for it.

## The 120 findings nobody has checked

| file | findings extracted | why they were left |
|---|---|---|
| `2026-08-26-fable-5-console-plan.md` | 62 | a plan, so most rows are unbuilt steps rather than defects |
| `2026-08-26-grok-4.6-console-plan.md` | 58 | same |

Both are reviews of a plan rather than of code, so a row that is "open" mostly
means a step nobody built, and `docs/console-plan.md` is the document that
should absorb them. That is the obvious next pass.

## How to redo this

The extraction and verification ran as a workflow: one reader per file, one
verifier per file against the tree, an adversary re-checking every row marked
closed, then a merge. The adversary overturned five of about ninety closed rows
it examined, which is the rate this repository's history predicts.

Two things to keep if it is run again. Verify against the tree rather than
against commit titles: a commit saying it fixed something is not evidence that
it is fixed, and the adversary pass exists to enforce that. And check the
closed rows harder than the open ones, because an open row that is really
closed costs a reader a minute, while a closed row that is really open is how
a finding disappears.
