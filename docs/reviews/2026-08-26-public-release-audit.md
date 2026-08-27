# Public release audit, 2026-08-26

Audit of everything that becomes public if `CorvidLabs/arcron` is made visible.
Read-only. Nothing was changed, nothing was rewritten, and repository
visibility was not touched: that is the owner's decision and this is the
evidence for it. Issue #23 tracks the decision.

## 1. VERDICT

**Safe to publish with remediations.** No credential, key, mnemonic, token,
webhook, private endpoint or internal hostname exists anywhere in the working
tree or in any reachable git object; every blocker below is an inaccurate
document, not a secret.

Said plainly, because it is the question that matters most: **the mnemonic
search came back empty.** Every one of the 2,993 objects in this repository's
object database was decompressed and scanned for a 24-word-or-longer run of
lowercase words. There is exactly one hit, it is unreachable from any ref (so
git never pushes it), it is a 4.7 MB binary lockfile, and the words are the
BIP39 wordlist bundled inside `algosdk`, not anybody's account. Both halves of
the `CLAUDE.md` claim hold, and both were verified rather than assumed:

- `.env.*` files are gitignored, and the rules are stronger than the claim.
  `git check-ignore` confirms `.env`, `.env.testnet`, `.env.mainnet`,
  `deploy/keeper.env`, `deploy/notifier.env`, `web/.env`, `scripts/.env` and
  any nested `*.env` are all ignored, while `.env.testnet.template` and
  `deploy/keeper.env.example` are not. No real env file has ever been added in
  any commit: the only `.env`-shaped paths in the whole history are the AlgoKit
  generator templates and the two `.example` files.
- The TestNet deployer has never been reused on MainNet. Asked MainNet algod
  directly: `E5M2OH5XNDMNABJ6VOFOUVR2IKRPCGQH43PVC5P3DWQQ2LV2VJV2FJZQ3E` has
  `amount: 0`, no created apps, no created assets, no opt-ins.

The self-hosted macOS runner, which is the one thing that turns "public" into
a code-execution question, is already guarded correctly. See B4 for the one
setting to confirm before flipping the switch.

## 2. BLOCKERS

Four. Three are documents that are wrong about custody risk or about how to
report a bug; one is a setting to confirm.

### B1. `docs/security.md:339-344` tells a security reporter not to report

```
## Reporting

Open an issue on [CorvidLabs/arcron](https://github.com/CorvidLabs/arcron/issues)
for anything that is already public. For anything that is not, and while there
is no published contact, do not open an issue. The repository is private and
the deployment holds test funds only.
```

This was written for a private repository and directly contradicts
`SECURITY.md:5`, which gives a working draft-advisory link. A reader who
follows `README.md:27` ("Read `docs/security.md` before escrowing anything")
and then finds a vulnerability is told there is nowhere to send it.

**Fix:** replace the section with a pointer to `SECURITY.md`. One sentence.

### B2. "no update path" is still asserted in two public-facing places, and it is false

The live TestNet app `769891898` reports `frozen: 0` on chain right now, and
`smart_contracts/keeper/contract.py:118-119` carries
`@abimethod(allow_actions=["UpdateApplication"])` on `update`. The creator can
replace the programs and reach every escrow. Two files say otherwise:

- `.github/ISSUE_TEMPLATE/feature_request.yml:22-24`:
  `"Changes to the keeper contract cannot be deployed to an existing app — it
  has no update path — so they mean a new deployment and a migration."`
  Unqualified and flatly false.
- `docs/security.md:325`: `"There is no upgrade path, so the playbook is
  short:"`, opening the `## If a bug is found` section. The surrounding
  material is about the frozen case (`docs/security.md:269` says so
  explicitly), but line 325 carries no qualifier, and the deployment a reader
  is being pointed at is not frozen. A reader lands on "if a bug is found" and
  is told the only remedy is cancel and migrate, when for the live app the
  first remedy is `update`.

This matters more than an ordinary doc bug because it is the exact finding all
three independent reviews led with. `docs/reviews/README.md:14-17` records it:
"All three led with the same finding: `SECURITY.md` said the contracts have no
update path, which was false." `SECURITY.md` was corrected. These two were not.
Publishing ships the same error under a different filename.

**Fix:** delete the clause in the issue template; qualify line 325 with "once
frozen" or restructure the section to cover both states.

### B3. `docs/deploying.md:86` prints a superseded digest as the live app's

```
app 769891898
  creator   E5M2OH5XNDMNABJ6VOFOUVR2IKRPCGQH43PVC5P3DWQQ2LV2VJV2FJZQ3E
  approval   2104 bytes
  combined  sha256 0afab3686aedeb990a46ad519a4bf0bf6a04394672ec3dd24990761be660bf49
```

`0afab368…` is the alpha-2 digest. The app has been alpha-3 since 2026-08-26
and its on-chain combined digest is `c94c6e0c…`. I confirmed this against the
chain: `scripts.verify_release --network testnet` reports "The live app is the
bytecode the release record claims", `c94c6e0cc561c028eeb3ccdd8c462c509ee106a28ba2e1d61469adbb62ffe124`.

The block is illustrative sample output, but it is labelled with the live app
id, and the whole point of the surrounding section is teaching a stranger to
compare digests before trusting a deployment. Someone doing exactly what the
document asks will get a mismatch and conclude the live app is compromised.
`docs/deploying.md:168` repeats the same stale digest in the `govern show`
example that multisig holders are told to check before signing; that one at
least tells the reader to compare against a rebuild rather than against the
printed sample, so it is the lesser of the two.

`docs/status.md:26` carries the same stale hash (`sha256 0afab368…`).

**Fix:** update all three to `c94c6e0c…`, or replace the literal digests with
`<digest>` so they cannot go stale again.

### B4. Confirm the fork-PR approval setting before flipping visibility

This one is a check, not a defect. `.github/workflows/ci.yml` runs
`build-and-test` and `localnet` on `[self-hosted, macOS]`, which is a machine
the owner owns. The workflow is already guarded correctly, and I verified it:

- `build-and-test` is gated on
  `github.event.pull_request.head.repo.full_name == github.repository`
  (`.github/workflows/ci.yml:96-99`), so a fork PR never reaches it.
- fork PRs are routed instead to `fork-checks` on `ubuntu-latest` with no
  secrets (`.github/workflows/ci.yml:38-43`).
- `localnet` runs only on `push` to `main` or `workflow_dispatch`, neither of
  which an outside contributor can trigger.
- repository `default_workflow_permissions` is `read`.

So the code side is right. Before flipping, confirm in Settings that Actions
requires approval for workflows from outside contributors, which is belt to the
workflow's braces on a runner where the failure mode is code execution on a
personal Mac.

Note also that `docs/arcron.md:718` says **"Fork pull requests do not run"**
and then says to "revisit it as part of the open-source readiness work". That
is this audit, and the work is already done: fork PRs do run, on GitHub's
hardware, safely. The document understates its own state. Fixing it is S5.

## 3. SHOULD FIX

Not dangerous. Every one of these would mislead or embarrass a stranger.

### The project describes itself as smaller and deader than it is

This is the largest cluster, and the most costly. A visitor arriving from a
link reads several documents saying nothing is deployed, nothing is running and
nothing is published. All three are false.

| Claim | Where | Reality |
|---|---|---|
| `Upkeeps registered \| none; the e2e cancels everything it creates` | `docs/arcron.md:237` | 11 boxes on app `769891898`, `next_upkeep_id` 75 |
| `Always-on keeper \| **none running**` | `docs/arcron.md:238` | the half-hourly Actions cron is live and `KEEPER_MNEMONIC` is set |
| `deploy/` makes that a `docker compose up -d`, but nobody has` | `docs/arcron.md:243` | as above |
| "It is deliberately manual-dispatch-only until someone sets the `KEEPER_MNEMONIC` secret" | `docs/arcron.md:365-366` | `.github/workflows/keeper-bot.yml:38-40` carries a live `cron: "*/30 * * * *"`, and the secret exists |
| `Console \| corvidlabs.xyz/arcron/console/, first publish pending` | `README.md:15`, `docs/status.md:27` | the console is live and returns HTTP 200 |
| "Nothing has been pushed" | `docs/console-plan.md:544` | as above |
| "There is no hosted URL in any document" / "a stranger currently cannot arrive at all" | `docs/journeys.md:75,78` | as above |

Verified against the chain and the network, not inferred: `GET
/v2/applications/769891898/boxes` returns 11 boxes, global state is
`frozen: 0`, `next_upkeep_id: 75`, and `GET https://corvidlabs.xyz/arcron/console/`
returns 200 with the Arcron console's own `<title>`.

### The release stage is wrong in three places

`README.md:331` and `README.md:415` say **alpha-1**; `docs/arcron.md:14` says
**alpha-1**; `examples/register_upkeep.py:40` says **alpha-2**. The live
release is **alpha-3** (`docs/releases.md:147`, tag `alpha-3`, confirmed on
chain). Worse, `docs/releases.md:145` maps alpha-1 to app `769823086`, which is
in the superseded set, so `docs/arcron.md:14` resolves a reader to a dead
deployment.

### Test counts in the README are off by an order of magnitude

`README.md:152`: `fledge lanes run ci         # contracts + console: build, 15 + 45 tests, spec check`

Measured today: **254** python tests (`poetry run pytest tests/ -q`, all
passing), **123** js tests (`cd js && bun test`), **91** web tests
(`cd web && bun test`). The `js` suite is not mentioned at all. `specsync check
--strict` also passes, 7 specs, 0 failed.

### A superseded app id sits where the guard cannot see it

`.github/ISSUE_TEMPLATE/bug_report.yml:15`:
`placeholder: scripts/keeper_bot.py on TestNet, app 769823086`

`769823086` is in `SUPERSEDED` in `tests/test_app_id_consistency.py:33`. The
test passes anyway (9 passed), because
`tests/test_app_id_consistency.py:111` skips every path beginning with a dot,
which excludes all of `.github/`. So the one guard written to stop exactly this
is blind to the file where a newcomer sees an app id first.

**Fix:** correct the placeholder to `769891898`, and narrow the exclusion from
`relative.startswith(".")` to the specific directories meant to be skipped so
`.github/` is covered. The task brief asked whether this test covers what is
public-facing: it covers the eight live pointers well, and this is the hole.

### Two other stale contradictions worth correcting

- `docs/arcron.md:713` says the LocalNet CI job runs "the keeper e2e and the
  timed-release demo". There is no timed-release demo: no such contract, no
  such script, and `.github/workflows/ci.yml` runs `smoke-keeper` and
  `smoke-rain`. `docs/integrating.md:69` names it too.
- `docs/integrating.md:35` offers `tick()uint64`, `publish()uint64`,
  `distribute()uint64` and `sweep()uint64` as examples "in this repo". Only
  `tick` survives; the other three went with the example-contract cull on
  2026-08-26. `git grep -E 'def (publish|distribute|sweep)\b'` returns nothing.

### `docs/journeys.md` is an internal working document

Beyond the stale facts above, it is written in a first-person voice for the
team ("the maintainer named as the model", `docs/journeys.md:18`), cites line
numbers that have moved (`js/src/networks.ts:64` for `DEFAULT_NETWORK`, which
is line 81 and now reads `'testnet'`, not `'localnet'`), and describes a console
with "two tabs ... and no router at all" (`docs/journeys.md:50`) when
`web/src/app/routes.ts` and `web/src/app/routes.test.ts` define and assert
three routes. Either bring it current or move it out of `docs/`.

### `docs/hosting.md` costs invert on the day you publish

`docs/hosting.md:19` and `docs/hosting.md:70-78` price the Actions cron at up
to "~$115/month", explicitly "because it is billed per minute on a private
repository". Actions minutes are free on public repositories. The table becomes
wrong at the moment of publication.

### A personal path in a committed review

`docs/reviews/2026-08-26-grok-4.6-rescore.md:145` and `:159` contain
`...` inside code-fence headers.
Cosmetic, and it is also in history so removing it at HEAD does not remove it
from the published clone. Not worth rewriting history for; worth fixing at HEAD
so it is not the first thing a reader sees.

### Rotate `KEEPER_MNEMONIC` at the flip

The repository has exactly one Actions secret, `KEEPER_MNEMONIC`, and it is a
live hot key servicing the TestNet app every thirty minutes. Publishing does
not expose it (GitHub withholds secrets from fork-PR workflows, and workflow
permissions are `read`), but the account it controls becomes trivially
discoverable from the on-chain executions. Rotating at the flip costs nothing
and shortens the blast radius of anything that went wrong while the repo was
private.

### Two identity decisions to make deliberately, not by default

Neither is a leak. Both are permanent once published.

- `pyproject.toml:5` and `.algokit/.copier-answers.yml:4` carry
  `0xLeif <leif.algo@pm.me>`. That reads as a deliberate public maintainer
  identity; confirm it is.
- Five real Algorand addresses are published as the intended MainNet 3-of-5
  members (`tests/test_multisig.py:15-19`, labelled `LEDGER`, `CORVID`, `HOT`,
  `KYN`, `GASPAR`), along with the derived multisig
  `NHQU7QBDTUC4Q5I7LV3A35GGG36QUK5EL6PM4ZVBJKZ7AS6EDOU7BCRDWA` in six files
  including `SECURITY.md:99` and `scripts/network.py:71`. These are public
  keys, not secrets, and publishing them is what makes the governance claim
  checkable. It also permanently associates five on-chain identities, one of
  them named `HOT`, with this project. Worth a conscious yes. Note that the
  multisig holds nothing on MainNet today (`amount: 0`) and issue #79 ("Set up
  the MainNet multisig creator (3 of 5)") is still open, so `SECURITY.md:98-99`
  states in the present tense something that is still a plan.
- `docs/reviews/` attributes four review rounds to named model versions
  (Grok 4.6, Fable 5, Kimi 3). If any of those names are pre-release or under
  an agreement, publishing names them. Owner's call; I am flagging it, not
  judging it.

### Commit trailers carry a private session URL

147 commits end with `Claude-Session: https://claude.ai/code/session_01JL4Z...`
and 2 more with a second session id. These are not credentials (the URL is
useless without the owner's account) but they are internal metadata in every
commit message, and they cannot be removed without rewriting history. Reported,
not remediated, per the constraint. If they are unwanted, the only options are
living with them or a full history rewrite, and a rewrite is not worth it for
this.

## 4. PUBLIC-READINESS GAPS

### What is already in good shape

- **LICENSE.** Present, full Apache-2.0 text, 202 lines, with the appendix
  filled in as `Copyright 2026 CorvidLabs`. It agrees with
  `js/package.json:5` (`"license": "Apache-2.0"`). `NOTICE` carves out
  `web/public/brand/` as trademarks and explains what a forker must replace.
  This is better than most repositories manage.
- **SECURITY.md.** Present, with a reporting path that works the moment the
  repository is public (draft advisory), a 72-hour acknowledgement commitment,
  an explicit scope and out-of-scope list, and an honest statement of the
  unfrozen-creator risk. Undermined only by B1.
- **CONTRIBUTING.md.** Present and genuinely useful: the Python pin, the
  spec-sync strict gate, the five files that move in lockstep with the `Upkeep`
  struct.
- **Issue and PR templates.** Both kinds present, plus a `config.yml` routing
  security reports away from public issues.
- **Repository surface.** No wiki, no discussions, no pages, no releases, no
  forks. Issue and PR bodies and comments were scanned and contain no secrets.

### The gaps

**Private repositories linked as if a reader can open them.** Confirmed against
the GitHub API: `CorvidLabs/fledge` and `CorvidLabs/spec-sync` are public;
`CorvidLabs/site` and `CorvidLabs/design-system` are **private**.

- `README.md:190` and `web/README.md:8` link
  `https://github.com/CorvidLabs/design-system` as the explanation of the
  console's styling. Every public reader gets a 404 on the one link that
  explains how the UI works.
- `CorvidLabs/site` appears in `README.md:195`, `web/README.md:43`,
  `AGENTS.md:18`, `fledge.toml:32`, `scripts/publish_console.py:11,233,241,290`
  and throughout `docs/console-plan.md`. `docs/console-plan.md:518-539` quotes
  the site's nginx `location` block and names the file it belongs in
  (`deploy/vps/nginx.conf` in that repo). Nothing there is a secret: no
  hostname, no IP, no path outside `/arcron/console/`, no token. It is simply
  another repository's internals, described in detail, in a document a stranger
  cannot act on.
- `AGENTS.md:63` tells the reader to "re-vendor with the design system's
  `sync-to.sh`", a script inside the private repo.

**Commands that only work on the author's machine.** `fledge run site-console
-- --site ../../site` appears in `README.md:205-206`, `web/README.md:45`,
`AGENTS.md:18` and `fledge.toml:35`, presented as the publishing procedure. It
assumes a sibling checkout of a private repository.

**The npm package does not exist.** `js/README.md:8` shows
`import ... from '@corvidlabs/arcron'` and `js/README.md:29` says "consume this
as a dependency rather than copying it". `https://registry.npmjs.org/@corvidlabs/arcron`
returns **404**. `js/package.json` also declares `"version": "1.0.0"` with
`"main": "./src/index.ts"`, raw TypeScript with no build output, and the only
consumer resolves it as `workspace:*`. Either publish it, or say plainly that
it is consumable inside the monorepo and by vendoring.

**The live console 404s its own deep links.** Verified:
`https://corvidlabs.xyz/arcron/console/` returns 200,
`https://corvidlabs.xyz/arcron/console/u/19` returns **404**. The nginx
fallback described at `docs/console-plan.md:518-528` and `fledge.toml:50-55` is
genuinely still missing on the server. `web/README.md:86-88` describes the
`404.html` copy as though it settles the matter, which it does not without the
server-side rule. Publishing is what makes shareable upkeep links matter, so
this is worth landing before or with the flip.

**Package metadata is thin.** `pyproject.toml` has no `license`, no
`repository` and no `homepage` field. `.algokit/.copier-answers.yml:8-9` still
carries `contract_name: hello_world` and `project_name: corvid_vault` from the
original scaffold, which is the only place in the tree that still names the
repository's previous identity.

**One broken relative link.** `.github/ISSUE_TEMPLATE/bug_report.yml:9` points
at `../blob/main/SECURITY.md`, which from an issue page resolves to
`github.com/CorvidLabs/blob/main/SECURITY.md`. Use an absolute URL.

**`README.md` structure.** `## Running a keeper` appears twice, at
`README.md:314` and `README.md:383`, with `## Running a keeper bot` between
them. `README.md:137` says "Pre-requisites: Python 3.13" while
`pyproject.toml:12` allows `>=3.12,<3.14` and `CONTRIBUTING.md:16` says "3.12
or 3.13"; `AGENTS.md:24` repeats the narrower 3.13.

## 5. WHAT I SCANNED AND HOW

`gitleaks` **was available** (`/opt/homebrew/bin/gitleaks`) and both scans were
run, each twice: once with the repository's own `.gitleaks.toml` and once with
default rules only, so the repository's allowlist could not hide anything.

| Scan | Command | Result |
|---|---|---|
| Tree, repo config | `gitleaks dir .` on a clean `git archive main` export | **0 findings** |
| Tree, default rules only | same, `-c` a config that is only `[extend] useDefault = true` | 42 findings, **all false positives** |
| History, repo config | `gitleaks git . --log-opts="--all"` | **0 findings**, 136 commits |
| History, default rules only | same, default config | 256 findings, **all the same false positives** |

The 42 and 256 are every base64 state key in `smart_contracts/artifacts/`,
which the `generic-api-key` rule matches because ARC-56 stores them under a
JSON field named `key`. I decoded them rather than trusting the comment in
`.gitleaks.toml`: `bGFzdF9iZWF0X3JvdW5k` is `last_beat_round`,
`cHJvdmlkZXI=` is `provider`, `YmVhY29uX2FwcA==` is `beacon_app`. The
allowlist is scoped to one rule and one path and is justified.

Because gitleaks does not know what an Algorand mnemonic looks like, I scanned
independently. `git cat-file --batch-all-objects --batch` dumped all 2,993
objects (53 MB, which is a superset of what a clone receives since it includes
unreachable objects) and I regex-scanned the lot for: 24-or-more-word lowercase
runs, 86-character base64 (a 64-byte ed25519 secret key), 64-character hex,
Discord and Slack webhook URLs, JWTs, GitHub tokens (`ghp_`/`gho_`/
`github_pat_` and friends), PEM private key headers, ssh keys, `x-api-key` and
`purestake`-style API-key hints, email addresses, IPv4 literals and Algorand
addresses. Every hit was chased to its source:

- **Mnemonics:** one hit, blob `236e335b`, unreachable from every ref
  (`git rev-list --objects --all` does not list it) so it is never pushed. It is
  a 4.7 MB binary lockfile containing a bundled `algosdk`, and the words are the
  BIP39 English wordlist. Not an account.
- **86-char base64 and 64-char hex:** `poetry.lock`, `bun.lock`, TEAL source
  maps and ARC-56 artifacts. Integrity hashes.
- **Emails:** eight distinct, six from lockfile package metadata,
  `noreply@github.com`, and `leif.algo@pm.me` in two files. Commit authorship is
  `0xLeif <leif.algo@pm.me>`, `Leif <leif.algo@pm.me>` and
  `Claude <noreply@anthropic.com>`, nothing else.
- **IPv4:** `0.0.0.0` and `127.0.0.1` only. Every other "hit" was a version
  string or a number with dots in a lockfile.
- **Webhooks, JWTs, GitHub tokens, PEM keys, ssh keys, API keys:** **zero**, in
  the tree and in every object.

Also done:

- `git log --all -p -U0` (21 MB of patch text) grepped for `mnemonic`,
  `passphrase`, `seed phrase`, `private key`, `secret key`. Every hit is a
  placeholder, a variable name, a warning, or documentation. No values.
- Every path ever added in history enumerated (362 distinct) and filtered for
  env, secret, credential, `.pem`, `.key`, `id_rsa`, `id_ed25519`, `.p12`,
  `kmd`, `mnemonic`, `token`. No real env file, ever.
- `git check-ignore` run against ten realistic env paths (results in section 1).
- Tree grepped for `CorvidLabs/site`, `corvidlabs.com`, `nginx`, `ssh `, `scp `,
  `root@`, IPv4 literals, `/var/www`, `/etc/nginx`, and named VPS providers. No
  real host, no real IP, no ssh config, no deploy target. `deploy/vps/install.sh`
  and `deploy/vps/package.sh` use `<user>@<host>` placeholders throughout, and
  `deploy/com.corvidlabs.arcron-keeper.plist` uses `/Users/CHANGEME/`.
- All three `.github/workflows/` read in full for secret exposure and runner
  safety (see B4).
- Repository settings via the API: `visibility: private`, no wiki, no
  discussions, no pages, no releases, no forks, `default_workflow_permissions:
  read`, one Actions secret (`KEEPER_MNEMONIC`).
- **Issues and pull requests**, which become public too: all 46 issues and all
  PRs pulled with bodies and comments (440 KB of JSON) and run through the same
  regex battery. Clean. The only sensitive-shaped strings are the five multisig
  member addresses and two program digests.
- Live verification against the chain and the network: TestNet app state and
  box count, MainNet lookups for the deployer and the multisig, HTTP checks on
  the console root and a deep link, `registry.npmjs.org` for the package,
  `api.github.com` for the visibility of four linked repositories.
- The advertised gate actually run: `poetry run pytest tests/ -q` (254 passed),
  `cd js && bun test` (123 passed), `cd web && bun test` (91 passed),
  `specsync check --strict` (7 specs, 0 failed),
  `scripts.verify_release --network testnet` (live app matches the release
  record).

### What I could NOT check

- **The contents of `CorvidLabs/site` and `CorvidLabs/design-system`.** Both are
  private. I could only assess what *this* repository quotes about them, which
  is an nginx `location` block and a deploy shape. Whether anything else has
  leaked the other way, into those repos from this one, is outside what I can
  see.
- **Whether the five multisig member addresses correspond to keys actually
  held.** No private key material for them exists in this repository, which is
  what I can prove. Whether the 3-of-5 is operationally real is issue #79.
- **32 local-only branches.** The repository has 49 local branches and 17 on
  `origin`. Only pushed refs become public, so the 32 local-only ones publish
  nothing today, but they would if pushed. My scans covered all local refs, so
  they are clean either way.
- **Unreachable objects** are included in my scan and excluded from a clone.
  I checked them anyway; the only interesting one is the lockfile blob above.
- **Whether the audit's own conclusions survive the fixes.** Every blocker here
  is a text change, so re-run `poetry run pytest tests/ -q` and
  `specsync check --strict` after fixing, and re-check B3's digest against
  `scripts.verify_release` rather than against this document.
- **Anything added after 2026-08-26.** This audit describes `main` at
  `6a621b0` plus the two commits on `fix/disabled-button-contrast`.

### How to repeat this

```sh
# Both gitleaks passes, with and without the repository's allowlist
printf '[extend]\nuseDefault = true\n' > /tmp/default.toml
git archive main | tar -x -C /tmp/tree-main
gitleaks dir /tmp/tree-main --no-banner --redact=0
gitleaks dir /tmp/tree-main -c /tmp/default.toml --no-banner --redact=0
gitleaks git . --log-opts="--all" --no-banner --redact=0
gitleaks git . --log-opts="--all" -c /tmp/default.toml --no-banner --redact=0

# Mnemonics, keys and webhooks across every object git holds
git cat-file --batch-all-objects --batch --buffer > /tmp/allobjects.bin
grep -aoE '([a-z]{3,8} ){23,}[a-z]{3,8}' /tmp/allobjects.bin | sort -u

# The gitignore claim, both halves
git check-ignore -v .env.testnet deploy/keeper.env web/.env
git log --all --pretty=format: --name-only --diff-filter=A | sort -u | grep -Ei '\.env|secret|\.pem$'
```
