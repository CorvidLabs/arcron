# Documentation review: is Arcron ready to be public?

| | |
|---|---|
| **Scope** | Documentation only. The contract was read to check what documents claim about it, not to review it. |
| **Reviewing** | `7354961`. Line numbers are as of that commit; the tree was being edited during the review, so each finding quotes its text as well. |
| **Date** | 2026-08-27 |
| **Method** | Read as a stranger who might escrow money. Every numeric and behavioural claim checked against `scripts.testnet_snapshot --network testnet --app-id 769891898` at round 66705686, against `smart_contracts/keeper/contract.py`, against the generated ARC-56 spec, and against the published mirror at corvidlabs.xyz. Full unit suite run (280 passed). |
| **Third of three** | Written without seeing the other two reviewers' work. |

**Verdict: not yet.** Five things must change first, and one of them is not in
this repository at all. None is a rewrite; the largest is deleting a section.

The documentation is unusually good at the thing most projects are bad at. It
says unaudited, it says unfrozen, it says the creator can reach your escrow, it
says there is no SLA and cannot be one, and it says so in the places a careful
person actually looks. `docs/security.md` names how each invariant was checked
rather than asserting it. `docs/status.md:181` volunteers that the project's own
90-percent-confidence allowance "is ours and does not transfer". That is the
paragraph that earns the rest of the prose, and I would not want a single
finding below read as a reason to soften it.

What is wrong is narrower and more specific: **several documents describe a
state the project has already left, and nothing in the repository can tell.**
The suite has 280 tests and not one of them reads a sentence.

## A note on timing

While this review was in progress, an edit pass landed in the working tree that
fixed six of my findings: the release stage in `README.md:335` and `:419`, the
MainNet multisig address and key count in `SECURITY.md:98`, `docs/deploying.md:224`
and `:266`, `docs/security.md:335`, the `alpha-2` comment in
`examples/register_upkeep.py:40`, the stale "five keys" phrasing in
`scripts/network.py:110`, and the stage plus superseded list in
`docs/arcron.md:14` and `:15`.

Everything below is verified against the tree **as it stands after those
edits**. Two findings are sharper because of them, not weaker: item 1 is a
sentence that survived the very edit that corrected the numbers on either side
of it, and item 4 is the reason six documents could disagree about an app id
and a key for a day without anything noticing.

---

## Must change before this is public

### 1. `docs/security.md:336` states the MainNet key's security margin backwards

The MainNet creator is now correctly documented everywhere as a 2 of 3 at
`LUH77ATP…`, matching `scripts/network.py:83`. But the sentence that says what
that buys you was not updated with the numbers around it:

> The MainNet deployment uses **three keys with a threshold of two**
> (`LUH77ATP…`). Two can be lost and two can be compromised without either
> losing control or losing the contract.

For a 2 of 3, both halves are false and both fail in the dangerous direction.
Two keys lost leaves one, which is below the threshold: control is gone
permanently, and an app's creator cannot be changed. Two keys compromised **is**
the threshold: the attacker can replace the programs and reach every escrow.
The correct statement for this configuration is one and one, which is exactly
what `docs/security.md:332` says two lines above it and `docs/deploying.md:263`
says as well. The claim as written was true for the 3 of 5 it replaced.

`docs/security.md:338` is left stranded by the same edit: "The LocalNet smoke
test uses three keys with a threshold of two because it only has to prove the
mechanism, not carry anything." That is now the same configuration as MainNet,
so the contrast says nothing.

This is first because it is the one document sentence that, believed, changes
how somebody protects the key that can drain every escrow in the deployment.
`docs/security.md` is published as `/arcron/docs/security/`.

**Fix:** one sentence. And add a test: `tests/test_multisig.py:262` already
asserts `ms.address() == net.MAINNET_CREATOR`, so the machine-readable half is
pinned and the prose half is not.

### 2. `docs/arcron.md:733-760` documents a self-hosted CI runner that was removed for this exact release

The page carries a CI section built entirely on infrastructure that no longer
exists. `.github/workflows/ci.yml:10` says "There is deliberately no self-hosted
runner", and every job in every workflow is `runs-on: ubuntu-latest`. The
document says the opposite at `:735`, and then, at `:747-752`:

> **Fork pull requests do not run.** A self-hosted runner executes whatever the
> workflow says on hardware we own [...] Once the repository is public this
> matters a great deal: without that guard, opening a pull request would be
> remote code execution on the runner. Revisit it as part of the open-source
> readiness work rather than leaving it implicit.

The open-source readiness work was done (`36d90c2`), and the answer was to
delete the runner rather than guard it. `docs/arcron.md:754-760` then gives
instructions for registering one. `docs/deploying.md:280-285` describes the real
arrangement correctly.

A reader who wants to contribute reads this and concludes that opening a pull
request runs their code on someone's Mac. A reader who wants to attack reads it
and goes looking for a runner. Both are wrong, and the page is published as
`/arcron/docs/how-it-works/`.

**Fix:** delete `### Registering the runner` and rewrite the four-line CI table
intro to match `docs/deploying.md:280-287`.

### 3. The published mirror is materially older than the repository, and currently says two contracts with known money-losing bugs are ready to hold funds

This is the finding that is not in the repository, and it is live right now,
before anything is made public.

`corvidlabs.xyz/arcron/docs/status/` lists `watchdog` and `embargo` among the
contracts that "ship", which `docs/status.md:81` defines as "ready to hold value
belonging to someone other than us". Both were cut from the repository on
2026-08-26. `docs/status.md:75-79` records why: `embargo` "let a stranger hijack
a fresh instance", and the same paragraph records `deadman`'s "total-loss trap
on its default deploy path". The published page presents `treasury` and
`deadman` as merely needing fixes.

The same mirror is stale in the other direction on the question a stranger
actually cares about. `/arcron/docs/how-it-works/` still says "no always-on
keeper operates the TestNet deployment" and "An upkeep registered against this
app today would sit due until somebody started a keeper", when three keepers are
running and the chain shows 59 executions. It also still reads `Stage | alpha-1`.
`/arcron/docs/status/` says "five upkeeps active" against 11 on chain.

Nothing gates this. `scripts/sync_site_docs.py` has a `--check` mode and its own
docstring says `--check` "is what CI would run if the two repositories ever
share one". No workflow runs it; `release-drift.yml` watches the deployment, not
the docs.

Making the repository public makes this divergence checkable by anybody, which
is good, and it means the mirror stops being a stale copy and starts being a
contradiction of a public source.

**Fix:** re-run the sync and push the site before flipping visibility. Then wire
`sync_site_docs --check` into a scheduled workflow the way `verify_release` is,
so it fails when the two drift again.

### 4. Nothing in the repository can tell when a document is wrong, and the enforcement is one line away

`tests/test_app_id_consistency.py` exists precisely because "most references
followed but not all" once pointed a keeper at a dead app for hours. It pins app
ids in eight files against the last row of `docs/releases.md`. It does not pin
anything else, and its own `HISTORICAL` set exempts `README.md`, `docs/arcron.md`,
`docs/status.md` and `SECURITY.md` from the superseded-id check entirely, which
is the whole set of documents a stranger reads.

The consequence, still true after the edit pass: `README.md:34-38` warns about
superseded apps `769802474` and `769772891` and omits `769823086`, the alpha-1
app, which `docs/arcron.md:15` and `docs/status.md:34` both name and which
`tests/test_app_id_consistency.py:28-32` describes as the one that cost real
service time. The README is exempt, so nothing sees it.

The stage claims that were wrong in four files for a day were invisible for the
same reason. `latest_release_app_id()` at `:75-85` already matches
`r"\|\s*(alpha|beta|rc|mainnet)-\d+\s*\|"` against the release table and throws
`group(1)` away. Capturing the full stage and asserting it against `README.md`,
`docs/arcron.md`, `docs/status.md` and `examples/register_upkeep.py` is a few
lines in a test file that already loads all four.

I would not normally rank a missing test as a release blocker. I do here because
this project's documented failure mode, in its own words in three separate
files, is prose that was true once. Publishing raises the cost of that failure
and nothing has raised the chance of catching it.

**Fix:** extend the existing test to the stage. Remove `README.md` from
`HISTORICAL` and give its warning block the third app id.

### 5. "Unaudited" and "four independent contract audits" are both published, on the two pages a stranger reads first

`README.md:22` and `SECURITY.md:16`: "No third party has reviewed this contract."
`docs/security.md:3`: "**Arcron is unaudited.**"

`docs/status.md:11`: "five rounds of adversarial review plus four independent
contract audits". `:74`: "Four independent audits plus three re-scores covered
seven contracts in August 2026". `:86`: "five review rounds plus an audit; no
unresolved findings". `:87`: "audited clean".

`docs/status.md` is what `scripts/sync_site_docs.py:32` publishes as the site's
"Start here" page. The reviews it counts are LLM passes, named as such in
`docs/reviews/README.md:3-5`. `docs/status.md:174` uses "a paid audit" as the
thing that has not happened yet, so the document knows the distinction and
spends the same word on both sides of it.

In a smart-contract context "audit" has one meaning, and it is not this. This is
the only place in the documentation where I think the prose is doing work the
facts do not support, and it matters more than its size because the project's
entire credibility argument is that it says uncomfortable things plainly.

**Fix:** call them adversarial review passes, say by what, and keep the count.
"Seven contracts, four independent LLM review passes and three re-scores, no
third-party audit" is both more impressive and true.

---

## Should change

### 6. Two documents disagree about whether a keeper will service your hook

`docs/integrating.md:251-256` tells an integrator:

> `algokit-utils` does this by default, through its `populate_app_call_resources`
> send parameter, so a keeper built on it services your hook with no
> configuration and no cooperation from you.

`docs/arcron.md:602-615` says that is false past four references: the
algokit-utils default populator "caps at four direct account references per
transaction and refuses a fifth with 'No more transactions below reference
limit'", which is why `scripts/keeper_bot.py::_resolve_execute_references` stopped
using it and simulates itself.

`docs/integrating.md:277` then tells the reader "Six is a real ceiling". For a
keeper built the way integrating.md just recommended, it is four. Someone who
sizes a hook at five or six resources from the integration guide gets a target
that the reference bot serves and that a stock algokit-utils keeper cannot, and
finds out after escrowing.

### 7. Catch-up is documented as the only scheduling behaviour in the two places that shape a design decision

Per-upkeep policy shipped. On chain, 7 of the 11 live upkeeps are `SKIP_AHEAD`,
and `docs/first-upkeep.md:99` tells a first-time user to choose it. But:

- `docs/arcron.md:263-276` ("What an outage looks like afterwards") describes
  catch-up unconditionally and closes by pointing at issue #7 as the live
  argument. `docs/arcron.md:128-133` documents `SKIP_AHEAD` 130 lines earlier.
- `docs/integrating.md:127-136` does the same and goes further, asking the
  reader to comment on #7, "which is deciding that policy and needs concrete
  cases". `docs/integrating.md:195-199` already knows the policy exists.
- `README.md:104-106` and `:373-375` both assert catch-up as the behaviour, with
  no mention that it is a choice.
- `docs/design/scheduling-and-fees.md:3` still reads "**Status: proposed, not
  implemented.**", and its first section heading at `:11` is "The keeper contract
  cannot be upgraded". Its two sibling design docs were updated to "implemented"
  (`call-shapes.md:3`, `asa-fees.md:3`); this one was not.
  `docs/arcron.md:723-725` sends readers there for the reasoning behind the
  policy they just picked.

### 8. `docs/arcron.md:138` contradicts the contract, and contradicts `docs/arcron.md:219`

In the Public API section, where somebody sizing an escrow reads:

> `balance ≥ effective fee` is what makes an upkeep executable, so a ceiling
> raises the dormancy threshold.

Eighty lines later, at `:219-222`: "A ceiling does not raise that threshold."
`smart_contracts/keeper/contract.py:403-405` settles it in favour of the second:
`if upkeep.balance.as_uint64() < fee: fee = base`, and the assert at `:407` is
against that reduced fee. `docs/integrating.md:389-399` and `docs/arcron.md:726-730`
also get it right. It is one wrong sentence, in the worst place for one.

### 9. The tables that exist to be current are stale, and the mechanism that would fix them is wired to nothing

Chain at round 66705686: 11 upkeeps, 59 executions, solvent.

- `docs/arcron.md:237` "Upkeeps registered | ten"; `:239` "Executions | 44 and
  counting".
- `docs/status.md:28` "Registry | ten upkeeps, the fastest firing about every ten
  minutes". The fastest live cadence is 10 rounds, roughly 30 seconds.

`docs/arcron.md:241-246` is a paragraph about how this exact table went stale
once before, and it ends "a `snapshot` task exists for exactly this table". It
does, at `fledge.toml:37`, and no lane runs it: `ci`, `local` and `endurance` all
omit it. The paragraph explaining the past failure is itself the current failure.

### 10. `docs/hosting.md` contradicts the registry, and itself

- `:12` "Latency only starts to matter when keepers are competing for the same
  upkeep, which is not yet true." Section B2 of the same file, at `:98-126`, is
  about two keepers built to race, and `docs/status.md:114-121` records the race
  happening on chain.
- `:66-68` "The shortest live cadence is about six hours, so a check every thirty
  minutes is late by at most eight percent of one interval." The shortest live
  cadence is 10 rounds. `docs/first-upkeep.md:93` tells a newcomer to register at
  215 rounds and `:152` expects the half-hourly keeper to catch it inside ten
  minutes. The half-hourly cron's whole justification rests on a number the
  registry has not matched for some time.
- `:147` `gh secret set KEEPER_2_MNEMONIC --repo CorvidLabs/nest`. The only
  surviving reference to the pre-rename repository, in a page published as
  `/arcron/docs/running-a-keeper/`.
- Related: `docs/integrating.md:381` "Arcron's TestNet deployment currently has
  one keeper." Two GitHub keepers race on a shared barrier and a third runs
  locally.

### 11. Three documents print a keeper command the tooling refuses

`scripts/keeper_bot.py:547-562` errors with "--app-id (or KEEPER_APP_ID) is
required [...] there is no canonical Arcron deployment to default to."

`docs/arcron.md:302-303`, `examples/README.md:52-53` and `README.md:360-361` all
print bare `keeper_bot` and `keeper_bot --once`. `README.md:365` then explains
that `--app-id` is required, five lines after printing two commands that need it.
A stranger has no `.env.testnet` (gitignored) and `.env.testnet.template` sets no
`KEEPER_APP_ID`, so every one of these fails on first run. `docs/hosting.md:200`
and `docs/status.md:55` get it right.

Two documents also contradict the error message's reasoning directly:
`examples/README.md:23` "attach to the **canonical** TestNet keeper app", and the
`# canonical TestNet keeper app` comment at `examples/register_upkeep.py:40`.

### 12. The docs still teach the habit the console was changed to defeat

`web/src/app/core/dev-mode.ts:9-18` names the link carrying `?app=` as "the only
attack this project has", and `entry.ts:66-76` now ignores `?network=` and `?app=`
entirely outside dev mode, "which means a poisoned link is inert for everyone who
is not already editing this code".

`docs/first-upkeep.md:42` tells the reader to open
`http://localhost:4200/register?network=testnet&app=769891898`. Outside dev mode
those parameters do nothing. The page works anyway, because `DEFAULT_NETWORK` is
`testnet` and `defaultAppId` is `769891898` (`js/src/networks.ts:72`, `:81`), so
the walkthrough reads as though the URL is doing the work. It never mentions
`?dev=1`. The single hands-on document in the repository teaches a URL shape that
is now decoration, and teaches it as the normal way to reach a deployment.

`docs/first-upkeep.md:189-193` then documents the quarantine panel as something
the reader may hit because "the app id in the URL is not `769891898`", and
explains how to continue past it. Outside dev mode that trigger cannot fire. What
remains is a paragraph telling a newcomer what to do when a link names another
app, which is the opposite of the lesson.

The rule the code now follows is not stated in any document: **on the published
console the app id is not something a reader supplies, checks, or can be sent.**
`README.md:175-179` and `web/README.md:10-14` both say "check the address", which
is good advice and currently the only defence any document names. Say the
stronger thing next to it.

### 13. What a stranger cannot find

- `README.md` links `docs/arcron.md`, `deploying.md`, `hosting.md`,
  `integrating.md`, `releases.md` and `security.md`, and never `docs/status.md`.
  That is the one page that is dated, current, and candid about what is unknown,
  and the one the site publishes as "Start here". From the repository's front
  door it is unreachable.
- `README.md` never states the up-front box deposit. It is 0.0621 ALGO, about
  fifteen times the per-run fee, and refundable on cancel. The figure is in
  `docs/arcron.md:199` and `docs/first-upkeep.md:58`. "What does this cost me" is
  not answerable from the first screen, which is the first of the five things
  this review was asked to test.
- `README.md` has two `## Running a keeper` headings, at `:318` and `:387`, with
  `## Running a keeper bot` at `:352` between them. GitHub's rendered outline has
  a duplicate entry and the deploy options are split across two sections that
  each look complete.
- `docs/first-upkeep.md` is linked from nothing. It is the only walkthrough in
  the repository and the only document that holds a newcomer's hand through a
  signature.

### 14. Documents that will be public and describe the product as broken, in the present tense

- `docs/journeys.md:70-80`, under "**True today**": "`DEFAULT_NETWORK` is
  `localnet` (`js/src/networks.ts:64`) [...] So a stranger currently cannot arrive
  at all". Fixed on 2026-08-26; `js/src/networks.ts:76-81` records the fix and
  names this exact drift as a past failure.
- `web/README.md:26-28` "It opens on **LocalNet**". Same drift, in the file a
  contributor reads first.
- `docs/console-plan.md` and `docs/ac/*.md` are internal working notes with no
  marking that says so.

None of these is dangerous. Together they are the first impression of a
repository whose strongest asset is that its documents are trustworthy.

### 15. A broken path in a public-facing file

`SECURITY.md:79` puts "the box-encoding decoders in `scripts/keeper_bot.py` and
`web/src/app/core/upkeep.ts`" in scope for a security report. That file does not
exist; it is `js/src/upkeep.ts`. Same stale path in `AGENTS.md:64` and
`CLAUDE.md:74`. `docs/arcron.md:192-193` and `CONTRIBUTING.md:86` have it right.

---

## Leave

Briefly, because the value is in what is wrong, but these should not be touched
in the course of fixing the above:

- `docs/security.md`'s threat model, invariants table and accepted-risks
  section. Every row names how it was checked and several name what was
  measured. `:383-386`, recording that the page once told reporters to stay
  quiet, is the right instinct and should stay.
- `docs/releases.md`'s gates, and especially "any change to the contract resets
  the 60 days. There is no 'significant change' exemption."
- `docs/status.md`'s "Genuinely unknown" section and `:181-202`. Volunteering
  that the confidence allowance does not transfer, and that on chain there is no
  such thing as not inviting people, is the best writing in the repository.
- `docs/arcron.md`'s box-encoding section, including the "+2" sentence and the
  paragraph explaining why omitting it returns a plausible wrong value.
- `docs/hosting.md`'s race log at `:217-242` and its field-by-field "check this
  yourself" list.
- `docs/integrating.md`'s "Four things that will cost you an hour".
- `NOTICE`.

---

## Would I put my own money in a contract documented like this?

**Not today, and the reason is narrow enough to fix in an afternoon.** The
contract's own documentation is the most honest I have read on this kind of
project: it tells me it is unaudited, that the creator can reach my escrow until
`freeze`, that there is no SLA, and it hands me two commands to check both
claims myself without trusting a word of it. That is the whole test, and it
passes.

What stops me is that the documents describing the system are demonstrably not
kept true, and I cannot tell from the outside which sentence is the stale one.
On the day I read them, the CI section described infrastructure deleted for this
release, the coverage table was 15 executions behind the chain, the integration
guide sent me to argue on a decided issue, the security page had the key
arithmetic backwards, and the published site was telling strangers that a
contract with a stranger-hijack bug was ready to hold their funds. Every one of
those was true once. That is the point: a project whose safety case rests on
being told uncomfortable things plainly cannot also be a project where I have to
guess which page was last checked.

Fix the five blockers, put the stage and the registry counts under the test that
already parses the release table, and I would escrow on TestNet the same day and
on a frozen MainNet deployment without hesitating.
