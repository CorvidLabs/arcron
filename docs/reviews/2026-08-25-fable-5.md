# Arcron — Independent Security & Readiness Review

> One of three independent, parallel passes. Breadth over depth.

| | |
|---|---|
| **Target** | app `769891898` · Algorand TestNet |
| **State** | **Unfrozen** (`frozen = 0`, verified on-chain) |
| **Branch / commit** | `deploy/alpha-2` · `69ba2a0` |
| **Date** | 2026-08-25 |
| **Method** | First-hand source read of all 10 contracts + 3 fan-out review agents (scripts, web/js, docs) + CI lane |
| **Prior work** | `keeper` contract: 5 adversarial review rounds (not repeated here) |

**Verdict — would I escrow my own money on MainNet today? No.** Confidence that it is safe for real money today: **3 / 10.** Not because the frozen contract can be robbed — I found no such path — but because the live app is *unfrozen*, unaudited, shipping on red CI, and its own security policy is wrong about the one property that matters.

---

## 1. Overview

Arcron is a permissionless keeper network on Algorand. A creator escrows ALGO in the `keeper` contract and registers an *upkeep*: "call app X with this fixed data every N rounds, pay R µALGO." Any keeper watches for due upkeeps, calls `execute()`, and is paid from that upkeep's own escrow, atomically with the call it triggers. No owner, no protocol rake, no token. The other six contracts (`rain`, `subscription`, `treasury`, `watchdog`, `deadman`, `embargo`) are example *targets* that demonstrate the "pull, don't push" pattern the design forces: the scheduled call does accounting only, and interested parties pull money/resources in their own later transactions.

That matches your description, with **one framing I'd correct**. You wrote it is "currently upgradeable" and MainNet is "planned soon" — both true, but the consequence is under-weighted in how the system presents itself. The live app **`769891898` is unfrozen right now** (confirmed on-chain: `frozen = 0`, `next_upkeep_id = 23`, ~5 live boxes). While unfrozen, the deployer key can replace `execute` and drain every escrow. That is the disclosed design state, not a bug — but it means "no admin over anyone's money" is a claim about the *frozen* contract, and the system is not there yet.

### Can anyone lose money that was not theirs to lose?

In the frozen, canonical-app, contract-logic sense: **no** — each box pays only itself, and escrow leaves only as a keeper fee or a creator refund, consistent with your five prior rounds. But three real paths move money wrongly *today*, none of them a flaw in `execute`'s arithmetic:

1. the unfrozen upgrade key (governance) — H1;
2. a look-alike-contract link in the console (social) — M1;
3. a demo contract that locks its owner's own funds forever (deadman) — M2.

---

## 2. Security concerns, ordered by severity

### HIGH

#### H1 — The live deployment is unfrozen: the deployer key can drain every escrow · Confirmed
The `update` path (`allow_actions=["UpdateApplication"]`) is gated only by `frozen == 0`, which is 0 on-chain today. Whoever holds `DEPLOYER_MNEMONIC` — a single throwaway key on TestNet; a 3-of-5 multisig is *planned* for MainNet but not in force here — can rewrite `execute` and take all escrow. Disclosed in `docs/security.md`, but the single largest risk. MainNet must **freeze before inviting outside money**, or accept "you are trusting a person, not bytecode." Not a code fix — a release-gate decision.
- `smart_contracts/keeper/contract.py:118-133`
- on-chain: `frozen = 0`

#### H2 — SECURITY.md tells reporters the opposite of the truth about immutability · Confirmed
`SECURITY.md:20-21`: *"The contracts cannot be patched in place. They are deployed with no update or delete path."* False (there is an `update` path until `freeze`), and self-contradicted at `SECURITY.md:80-83` (*"while that app is unfrozen it can replace the app's programs… reach every escrow"*). The live app is unfrozen. A researcher/integrator who trusts the security-policy front door under-estimates deployer-key risk exactly where they're most likely to look. Fixable immediately — it's a doc, but it's the *security* doc.
- `SECURITY.md:20-21`
- `SECURITY.md:80-83`

### MEDIUM

#### M1 — A crafted console link permanently repoints the console at an attacker's look-alike contract · Confirmed
The console resolves app id as *link → memory → default* and **persists the linked id to localStorage**; every later transaction derives its receiver from that id. All ABI/box layout is public, so an attacker deploys an identical-looking keeper, shares `console?network=testnet&app=<evil>`, and the victim sees a plausible registry and a working register form — their MBR + funding payments go to the attacker's app account, and the attacker's contract keeps them. **Nothing in the UI ever says "this is not the canonical app 769891898,"** and the poisoned choice sticks across visits. The wallet's receiver address is the only backstop. The `frozen` flag is also surfaced nowhere in the console, so a user can't tell a mutable deployment from a frozen one either.
- `web/src/app/core/entry.ts:1-17, 70-78`
- `web/src/app/core/arcron.service.ts:112-117`
- `js/src/keeper-txns.ts:82-94`

#### M2 — deadman can never return the owner's escrow to the owner · Confirmed
The contract has `arm`, `check_in`, `sweep`, `claim` — and no disarm, no owner-refund, no beneficiary change. Once armed, the escrow can leave *only* to the fixed beneficiary, and only after firing. An owner who changes their mind cannot recover their own money; if the beneficiary's key is lost, the funds are locked forever (no delete path). It's a demo (lower priority per scope), but it's a copy-paste template, and this is a genuine fund-lock, not a liveness nit.
- `smart_contracts/deadman/contract.py` (whole surface)

#### M3 — CI is red at the deployed release commit · Confirmed
`fledge lanes run ci` fails at step 6 (`web-test`): `web/src/app/core/entry.test.ts:56` still expects the superseded canonical app id `769823086`, while the code now returns `769891898`. The alpha-2 deploy commit updated `defaultAppId` but not the test, so the tree that is *live on TestNet* does not pass its own "must stay green" lane. Python tests, build, and spec check all pass. The "green CI" invariant — and the release evidence resting on it — is broken.
- `web/src/app/core/entry.test.ts:56`
- ran: `fledge lanes run ci` → fail @ step 6

### LOW

#### L1 — examples/register_upkeep.py is broken against the 1.0 contract, no test covers it · Confirmed
It calls `RegisterArgs(..., call_data=...)` (pre-1.0 API); the generated client takes `call_args` plus `policy/fee_cap/fee_asset/asset_fee`, so it raises `TypeError`. Its MBR formula is from the old 84-byte struct and under-funds the real ~62,100 µALGO box. `test_examples.py` only compiles `minimal_target.py`. First file an integrator copies.
- `examples/register_upkeep.py:56, 74-84`
- `smart_contracts/artifacts/keeper/keeper_client.py:68-79`

#### L2 — govern's app-id guard doesn't cover the most dangerous transaction type · Confirmed
The guard `if in_file and in_file != args.app_id` uses `in_file`, which is 0 (falsy) for any non-app-call, so a payment/rekey/close of the multisig account smuggled into a signing file bypasses the `--app-id` refusal entirely. The only defense left is the human reading `describe_transaction` (which does warn on rekey/close). The govern flow never produces such files, so this is integrity against a malicious file, not a normal-path bug. Related: `describe_transaction` prints only the *approval* program hash, never the clear-state hash, though the signed txn embeds both.
- `scripts/govern.py:234, 247`
- `scripts/multisig.py:216-218, 181-186`

#### L3 — rain.draw() is unauthenticated while subscription.charge() authenticates its keeper · Suspect (fairness)
Anyone can open a draw, snapshotting tickets and pot at a moment of their choosing (front-running the schedule to draw a smaller pot). Winner *fairness* still holds: `commit_round = round + 8` and the beacon answers only for a passed round, so the caller can't predict the outcome. Also `configure` accepts any `beacon_app > 0` with no constraint to the real Foundation beacon, so a rigged deployment is indistinguishable unless clients check the id. Liveness/fairness on a demo contract, not fund theft. The fairness reasoning is my analysis — I did not exhaustively model snapshot manipulation.
- `smart_contracts/rain/contract.py:245, 107-131`

#### L4 — The keeper bot over-declares its fee on ASA-bonus upkeeps · Confirmed
The bot adds the 1,000 µALGO bonus surcharge whenever `fee_asset > 0 and asset_balance >= asset_fee`, **without checking whether the keeper is opted into the asset**, while the contract pays the bonus only if the keeper *is* opted in. A non-opted-in keeper pays the surcharge into the fee pool and it's not refunded (Algorand fee pooling), netting 0 instead of +1,000. The comment above it argues the opposite of what happens. Self-inflicted, bounded. Display mirrors in `board.ts:66` and `upkeep-board.ts:211` show net reward assuming 3,000 cost when asset upkeeps cost 4,000.
- `scripts/keeper_bot.py:504-513`
- `smart_contracts/keeper/contract.py:393-400`

#### L5 — treasury.configure checks the MBR payment's receiver but not its amount · Confirmed (not exploitable)
And `deposit` works before `configure`. I initially flagged this as a strand risk and **downgraded it**: an underpayment just reverts `configure` via AVM minimum-balance enforcement on the box write, so it can't strand funds on a real chain. But it's the only sibling contract that omits the explicit amount assert, the `algorand-python-testing` mocks hide it, and no e2e covers treasury.
- `smart_contracts/treasury/contract.py:78-80`

### Documentation drift (each is itself a finding; confirmed unless noted)

- `deploying.md:83-87` shows a stale `govern status` sample for the live app reading "no update path / approval 1932 bytes"; the live app has `frozen=0` and a 2104-byte approval program.
- `keeper.spec.md:83` (invariant 18) says `cancel` "refuses before refunding" if the creator can't receive the ASA; the contract sets `bonus=0` and *proceeds*.
- `requirements.md:51,56` is a release behind ("one app arg, no ASA fees" vs shipped `MAX_CALL_ARGS=3` + ASA bonuses).
- `security.md:110-115` "the only multiply in the contract" is false — also at `contract.py:192, 307, 374` (all bounded, no overflow found).
- README / `arcron.md` say **alpha-1** while git says alpha-2; "nothing is skipped" is stated unconditionally but `SKIP_AHEAD` drops the backlog by design; `arcron.md:63-73` omits `update`/`freeze` from the API table.
- `specsync check --strict` **passes** (10/10) — but it validates section/export presence only; every drift here sailed through it.

---

## 3. Pros — what is genuinely good

- **The keeper contract is genuinely well-built.** Every balance mutation is a single guarded add/sub; the AVM panics on overflow rather than wrapping, so the failure mode is a rejected transaction, not a wrong number.
- **Overflow safety is proven from input bounds.** `MAX_UPKEEP_FEE` and `MAX_INTERVAL_ROUNDS` are both 1e9 → product ≤ 1e18 < uint64, deliberately not resting on "no chain lives that long."
- **The failure-mode reasoning lives in the code** — cancel's best-effort ASA vs guaranteed ALGO refund, the replay-doesn't-escalate guard (with a measured 34-run figure), the "can only bid what it holds" fallback. Each trap is closed with a stated reason.
- **The pull-don't-push, accounting-only-hook pattern is applied consistently** across all six targets. `subscription.charge` authenticating the keeper to prevent fabricated-period billing is a sharp catch.
- **Governance safety holds where it counts.** Multisig threshold is read from the signed blob, not the editable JSON; the address hashes `version‖threshold‖pubkeys` so it can't be downgraded; a single signer cannot act alone; genesis-id is verified before any signing.
- **Box decoders match the struct byte-for-byte** in both the Python bot and the TypeScript library, each pinned to a recorded on-chain box.

---

## 4. Cons — what is weak or will bite

- **The trust story is split-brained.** Careful, honest disclosure in `security.md` and README coexists with `SECURITY.md` and `deploying.md` telling readers the live app is immutable. Which one a person reads is luck.
- **Doc/spec drift is systemic.** Three landed features (governance, multi-arg, ASA bonuses) left `requirements.md`, `arcron.md`, `SECURITY.md`, and several spec type-tables behind. `specsync --strict` gives false confidence — it can't see semantic drift.
- **The console has no notion of a canonical deployment**, which turns "bring your own app id" (a feature) into M1 (a phishing vector).
- **The six examples have had far less scrutiny and it shows.** deadman's owner fund-lock and rain's unauthenticated draw are things the keeper contract would never ship — and they're the templates integrators start from.
- **Release hygiene is loose:** CI red at the deploy commit, a broken flagship example, alpha-1/alpha-2 disagreement. Together they undercut "sustained TestNet time + self-review" as an audit substitute.

---

## 5. Next steps, ranked

1. **Fix `SECURITY.md:20-21`.** It's the security front door and it's actively false. One edit.
2. **Decide the freeze gate explicitly for MainNet.** If real money is invited before `freeze`, say so in the loudest terms; ideally freeze first. This is the whole ballgame.
3. **Green the CI lane and enforce "deploy only from green."** So release evidence means something. Start at `entry.test.ts:56`.
4. **Give the console a canonical-app signal.** Badge the known app id, warn on any other, and surface `frozen`. Closes M1 and the mutable-vs-frozen gap.
5. **Fix or delete `examples/register_upkeep.py`** and add a compile/shape test for it like `minimal_target` has.
6. **Reconcile the docs to the shipped surface** — invariant 18, `requirements.md`, the "only multiply" claim, the `deploying.md` sample, alpha stage, the `update`/`freeze` API rows. A one-pass sweep.
7. **Resolve deadman's owner-refund story and pin rain's draw/beacon** — or clearly mark both as illustrative-only.
8. **Bot & display polish** — L4's opt-in check and the 4,000-µALGO asset-cost display.

---

## 6. Confidence rating: 3 / 10 for real money on MainNet today

The keeper contract logic is the strongest part, and I'd rate *it* alone much higher. The 3 reflects the system as it stands: unfrozen (trust-a-person, not trust-bytecode), unaudited, a security doc that's wrong about immutability, red CI on the shipped commit, and six lightly-reviewed satellite contracts. None of that is a hole in `execute`; all of it is why I wouldn't hand it money yet.

**What would raise it:** `freeze()` called on the MainNet deployment with `verify_build` confirming bytecode matches a tagged commit; an independent audit (or at least the doc corrections) so the trust story is single-voiced; green CI at the deployed commit; a canonical-app guard in the console. With the first three, I'd be at 7+.

**Single thing most likely wrong that I did not check:** whether app `769891898`'s *on-chain bytecode actually matches this source*. I read the source and confirmed it has no theft path, but I did not run `verify_build` against the live app — everything downstream rests on that untested assumption. Second most likely: the ASA clawback/freeze edges in cancel/execute on a real chain, which the unit tests only mock.

---

## 7. The direct question

**Would I personally escrow my own money in this on MainNet today? No.** Not because I found a way to steal escrow from the frozen contract — I didn't, and the contract logic is better than most audited code I've read.

I'd say no because the deployment is **unfrozen**, which makes it a bet on whoever holds the deployer key rather than on the bytecode; because it is **unaudited** and the document meant to govern that (`SECURITY.md`) is wrong about the one property that matters; and because the release is shipping with **red CI and a broken flagship example**, which tells me the "sustained TestNet time + self-review" gate is not yet held to its own standard. Freeze it, correct the trust docs, green the build, and give the console a canonical-app guard — then ask me again, because the core is worth trusting once the wrapper around it is.

---

### Method & coverage

Everything marked **Confirmed** was verified by reading the cited source or querying the chain; the two **Suspect** items (rain timing, and whether an alternate algod error format defeats keeper backoff) are flagged, not asserted. I did *not* run the e2e / soak / spike scripts, did not run `verify_build` against the live app, did not audit `execute`'s arithmetic beyond reading it (your five prior rounds did), and did not check the npm / Python supply chain. This is one of three independent passes — coverage over depth by design.
