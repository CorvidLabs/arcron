# Arcron: MainNet deployment path review

- **Date:** 2026-08-25
- **Reviewer:** Grok 4.6 (xAI), follow-up to the 2026-08-25 independent review
- **Scope:** create, signing, MainNet gate, `verify_build`, freeze sequencing. Not a re-review of contract logic.
- **Plan under attack:** 3-of-5 `NHQU7QBDTUC4Q5I7LV3A35GGG36QUK5EL6PM4ZVBJKZ7AS6EDOU7BCRDWA`; unattended dogfood first, then freeze, then invite outside escrow.

The create path is the part that can still ruin this. Signing is weaker than `show` makes it look. Freeze-after-soak is right for **your** money and not a privacy mechanism.

`SECURITY.md` no longer lies about there being no update path. It is still not a complete description of the MainNet trust model. `scripts/verify_build.py:3` still says the contract has no update path.

---

## 1. Create-time checklist

There is still **no `govern create`**. `scripts/deploy.py:54–64` refuses when a multisig is configured. `fledge run deploy-mainnet` (`fledge.toml:18`) is that same script. The only worked create in the repo is `scripts/multisig_e2e.py:64–70`, and it is a LocalNet proof that **generates throwaway 2-of-3 keys** (`:41–45`). Copying that file onto MainNet is the worst mistake available.

Every field below is set on `ApplicationCreateTxn` and cannot be changed later. Schema and extra pages are create-only in the protocol. Creator is `Txn.sender` forever. This app refuses `DeleteApplication`.

| Field | Must be | If wrong | Undoable? |
|---|---|---|---|
| **Sender / creator** | 3-of-5 `NHQU7QBDTUC4Q5I7LV3A35GGG36QUK5EL6PM4ZVBJKZ7AS6EDOU7BCRDWA` | You do not control `update`/`freeze`. A single-key `DEPLOYER` create is the admin-key bug, permanently. | **No** |
| **Threshold and member order** | `ARCRON_MULTISIG_THRESHOLD=3` and the five addresses in the **exact** order `tests/test_multisig.py:24,50–52` (`LEDGER, CORVID, HOT, KYN, GASPAR`). Order is inside the address (`docs/security.md:306–308`). | A permutation is a different account that holds nothing. Threshold 2 with the same keys is a different account, and two stolen keys steal the app. | **No** |
| **Approval + clear bytecode** | This tree’s keeper, after a clean rebuild. Combined `sha256(approval + 0x00 + clear)` must match `verify_build` on a tagged commit. | Backdoored programs. Fixable with `update` **until freeze**. After freeze, a new app. | Until freeze |
| **`extra_pages`** | **1** | See below | **No** (if create succeeds) |
| **Global schema** | `(nuint=2, nbytes=0)` — `next_upkeep_id` and `frozen` (`Keeper.arc56.json:231–239`, `multisig_e2e.py:67`) | Too small: create eval fails when `__init__` writes the second uint. Too large: extra creator MBR forever (28,500 per extra uint, 50,000 per extra bytes). | Fail = yes. Excess = **no** |
| **Local schema** | `(0, 0)` (`:68`) | Excess: extra creator MBR forever. Local state is unused; approval does not allow OptIn. | Excess = **no** |
| **OnCompletion** | `NoOp` (`arc56` `bareActions.create: ["NoOp"]`) | Anything else is rejected at create. | Yes (never created) |
| **Network / genesis** | `mainnet-v1.0`, MainNet genesis hash | A TestNet create you then treat as MainNet. | **No** (wrong chain) |

**`extra_pages` specifically.** Pages are 2,048 bytes and are **shared by approval and clear**. Live sizes: approval 2,104, clear 4, total 2,108. Capacity is `2048 × (1 + extra_pages)`.

The e2e formula is correct for these sizes:

```python
# scripts/multisig_e2e.py:63-69
extra_pages = (len(approval) + len(clear) - 1) // PROGRAM_PAGE
create = transaction.ApplicationCreateTxn(
    ...
    extra_pages=extra_pages,
```

`(2108 - 1) // 2048 = 1`.

| Value | What happens | Permanent? |
|---|---|---|
| **0** | Max 2,048 bytes. 2,108 does not fit. Node rejects (`approval program too long`, as the comment at `:58–61` says). | No. Nothing was created. |
| **1** | Max 4,096. Correct. Creator MBR: 0.1 app + 0.1 extra page + 2 × 0.0285 schema ≈ **0.257 ALGO** locked on the 3-of-5, forever. | Yes, and that is the intended lock. |
| **2** | Create **succeeds**. Capacity 6,144. Programs still run. Creator pays **another 0.1 ALGO MBR forever**. Extra pages cannot be added *or removed* later. | **Yes.** Waste, not a bricked app. |

**Confirmed** from the artifact sizes, the e2e, and Algorand’s create-only `ExtraProgramPages`. I did not broadcast a 0-page or 2-page create this session.

**What goes wrong if someone “follows `multisig_e2e.py`” on MainNet**

I confirmed these by reading the script:

1. It **overwrites** `ARCRON_MULTISIG_*` with three fresh keys (`:43–45`). Creator is not `NHQU…`. Those keys live in that process. When it exits, they are gone. Status will still print `frozen 0` / “the creator can still replace the programs”, which is then a lie: the creator is dead. If the keys were logged or swapped, whoever finds them owns MainNet admin. **Irreversible.**
2. It funds that throwaway 2-of-3 with 5 ALGO from `DEPLOYER` (`:48–53`). Real money into an account whose keys the script is about to drop.
3. It does **not** rebuild (`_programs(_spec("keeper"))` only). Stale or substituted artifacts get created.
4. It does **not** fund the **app account’s** 0.1 ALGO base MBR. `deploy.py:71–81` does. First `register` then fails until someone sends 0.1. Recoverable.
5. It does not refuse `--network mainnet`. Default is TestNet, but `ARCRON_ALLOW_MAINNET=1 poetry run python -m scripts.multisig_e2e --network mainnet` is enough.

Do not use that file as the MainNet create. Write `govern create` that prints creator, threshold, extra pages, schema, and combined digest, and requires typing `NHQU…` back.

**Still unguarded, and irreversible if it happens**

- `fledge run deploy-mainnet` with `ARCRON_ALLOW_MAINNET=1` and **no** `ARCRON_MULTISIG_ADDRESSES` deploys from `DEPLOYER_MNEMONIC` (`deploy.py:54–68`). The table in `docs/deploying.md:44` still lists only `.env.mainnet` and the flag. **MainNet must require a configured 3-of-5, and must refuse a single-key sender.** `configured()` is only “addresses env is set” (`multisig.py:65–67`); it does not check that `ms.address() == NHQU…`.
- `docs/deploying.md:142–143` and `:208–211` still teach **2-of-3**. Copy-paste create from the deploy guide yields the wrong address and a 2-key steal threshold.

---

## 2. Attacks on the signing path

Assume five holders who run `govern show` before `govern sign`. Ranked by what a compromised coordinator can still get if some of them only *glance*.

### 2.1 Rekey / close of the 3-of-5 — irreversible theft of admin

**Confirmed.** `app_id()` returns 0 for a payment (`multisig.py:216–218`). The guard is `if in_file and in_file != args.app_id` (`govern.py:233–239` and again at `:247`). `0` is falsy, so a payment, rekey, or close **skips the app-id check**. `ApplicationCreateTxn` also has index 0, so **create skips it too**.

`describe_transaction` does print `!! REKEYS` / `!! CLOSES` (`multisig.py:193–196`). `show` prints that. Then `sign` prints it **and signs in the same process with no prompt** (`govern.py:240–244`). Freeze asks you to type the app id (`:148–152`). Sign does not.

If three holders treat `show` as a ritual and run `sign` from a script, the coordinator rekeys `NHQU…` to themselves. After that, `update`/`freeze` are theirs. That is strictly worse than a malicious program: they own the account.

**Close:** `govern sign`/`submit` refuse `rekey_to` or `close_remainder_to` unless `--i-mean-to-rekey`. Refuse any non-app-call unless `--account-txn`. For app calls, compare `in_file != args.app_id` **without** the `if in_file` short-circuit; use `--app-id 0` only for create.

### 2.2 Malicious programs on create or update — irreversible after freeze

**Confirmed.** `sign` never rebuilds, never hashes the working tree, never calls `verify_build`. The defense is the holder comparing a hash from `show`.

That comparison is set up to fail:

- `show` prints **only** `sha256(approval)` (`multisig.py:185`).
- It does **not** print the clear hash.
- `verify_build`’s recorded identity is `sha256(approval + b"\x00" + clear)` (`verify_build.py:49–51`).
- The hint says compare against **`fledge run verify`** (`multisig.py:186`), and that task is `--no-rebuild` (`fledge.toml:10`). Holders hash **committed artifacts**, not a compile of the tag they think they have.

**Clear-state swap, confirmed as possible, theft currently suspected-not:** coordinator ships honest approval + hostile clear. Approval hash matches. Clear is 4 bytes of `pushint 1; return` (`Keeper.clear.teal:6–7`) and is not reachable today (no OptIn). After freeze you are stuck with it. If a later pre-freeze `update` ever allowed OptIn, the bomb could run. Integrity bug now; theft later. Print the **combined** digest, identical to `verify_build`, and the clear hash.

**Close:** `sign` of `UpdateApplication` or create must recompute combined digest from the txn bytes and refuse unless it equals `verify_build` on this tree (rebuild, refuse if dirty unless `--allow-dirty`). Do not point holders at `fledge run verify`.

### 2.3 Wrong network on a non-app txn — MainNet action under a TestNet command

**Confirmed.** `show`/`sign` call `net.connect(args.network)` (`govern.py:204`) and then **never use the client** for show/sign. The file’s `genesis_id` is printed (`multisig.py:170`) and not compared to `--network`.

For `update`/`freeze`, a MainNet app id vs `--app-id` TestNet id **does** refuse. For payments and create (id 0), it does not.

Attack: file is a MainNet rekey of `NHQU…`. Holder runs `govern sign --network testnet --app-id 769891898 --file that`. Guard skipped. They signed MainNet admin away. `submit` with `--network testnet` would send it to TestNet and the node would reject genesis — **unless they also submit with `--network mainnet`**. Signing is the irreversible step; submit is the gun.

**Close:** require `txn.genesis_id` ∈ the genesis ids for `--network`. On MainNet, require `ARCRON_ALLOW_MAINNET=1` **and** `ms.address() == NHQU…` **and** `txn.sender == that address`.

### 2.4 `show` reports the env threshold, not the blob’s

**Confirmed.** `govern.py:213` prints `ms.collected` of `ms.threshold()` (environment). Submit uses `blob_threshold` (`multisig.py:221–227`). The JSON `threshold`/`signers`/`address` fields are decoration (`:206–211`); only the blob counts.

A holder whose env is 3-of-5 can be shown “2 of 3” or “2 of 5” depending on whoever ran `show`. For an **update of the real app**, sender must hash to `NHQU…`, so the blob’s members+threshold cannot silently become 2-of-5 and still hit this app. For **create**, sender *is* whatever the blob says. A 2-of-5 create that nobody compares to `NHQU…` is a permanent wrong creator.

**Close:** print blob address, blob threshold, blob members, and refuse if they are not exactly the configured 3-of-5. Refuse JSON/blob disagreement.

### 2.5 Huge fee, no pause

**Confirmed.** Fee is printed (`:172`) and signed immediately. Coordinator sets `fee` to the msig’s spendable balance. Three glances drain the 3-of-5. Recoverable only if someone refills before freeze; not theft of app escrow, theft of the admin account’s ALGO, which can brick freeze if it drops below MBR until refilled.

**Close:** cap fee (e.g. 5,000 µALGO) unless `--allow-high-fee`. Pause: `Type the app id / sender to sign`.

---

## 3. Verdict on freeze sequencing

**The sequencing is right for your own money and wrong if you think it keeps strangers out.**

Unfrozen → own dogfood → freeze → invite is the correct order **given** that a struct-preserving bug should still be patchable while only you are at risk. Freeze-at-create would throw away the reason `update` exists, and you have already paid for that lesson twice on TestNet.

What the plan pretends, and the contract does not provide: a private registry. `register` is permissionless. Anyone who learns the app id can escrow during the unattended window. You cannot “not invite” on chain. Explorers, bot logs, a gist, a status tweet — that **is** the invite. An outside upkeep that lands before freeze is exactly the situation `docs/security.md` says you will not ask anyone to trust.

What I would do instead, plainly:

1. Create from the 3-of-5. Fund app MBR. Do not put the app id in README, console, `KEEPER_APP_ID` examples, or any public log.
2. Dogfood. If **any** unexpected registration appears, either `update` is still the point — leave it — or **freeze immediately** and treat that stranger as a real user you just took admin power over. Do not wait out the calendar.
3. Freeze from a clean tag, `verify_build` matching, `govern status` showing creator `NHQU…` and `frozen 0` going to `1`.
4. **Then** publish the id, add the console MainNet entry, invite.

Do not freeze at t=0. Do not treat “unattended for a few weeks” as a substitute for “the id is unpublished.” Three of five can still drain **your** dogfood the whole time; that is the 3-of-5 model, and it is acceptable only because it is your ALGO. The keeper bot must not hold a member mnemonic. `DEPLOYER` fallback on MainNet should hard-fail.

---

## 4. What `verify_build` does not prove

It proves: **this working tree, compiled with this Python, produced the same approval+clear bytes algod returns for that app id on the network `connect()` just checked.**

It does **not** prove:

| Claim | Reality |
|---|---|
| The app cannot be changed | That is `frozen`. Unfrozen + matching bytes is “matches **today**.” |
| Creator is the 3-of-5 | Never reads `params.creator`. |
| Extra pages / schema | Never reads `extra-program-pages` or schema. A 2-page-too-many app still “✔”. |
| Source is the release tag | Rebuilds the working tree. Dirty is printed (`verify_build.py:100–103`) and **not refused**. `--no-rebuild` trusts artifacts. `fledge run verify` is `--no-rebuild`. |
| Clear was reviewed | Combined digest includes it; a human using `show`’s approval-only hash never sees it. |
| Suggested params / network of a **file** you are about to sign | Different tool. |
| MBR funded, boxes empty, no unexpected escrow | Different tool (`govern status` does not check that either). |
| Puya version / 3.13 vs whatever the coordinator compiled | False mismatch if your toolchain differs; not a proof of honesty if you both use poisoned artifacts. |

Docstring at `verify_build.py:3` is still the old lie (“no update path”). Fix it before people use the tool as a freeze oracle.

---

## 5. Other irreversible, currently unguarded

- **`SECURITY.md` correction:** the “cannot be patched / no update path” paragraph is gone. What it says now about TestNet `769891898` being unfrozen is **true**. What is still wrong or incomplete: it still names `DEPLOYER_MNEMONIC` as “the one that matters” (`SECURITY.md:94–97`); on MainNet that must be the 3-of-5. `docs/security.md:13–18` still opens with “no admin key over anyone’s escrow” while the live and planned-unattended apps have one. The “If a bug is found” playbook (`:312–323`) assumes freeze already happened.
- **No MainNet in `js/src/networks.ts`:** **better** until freeze. Adding it is an invite. After freeze, lacking it is worse (people will build raw txns). Ship MainNet in the console only with `frozen` on the bar, and refuse to register if `frozen != 1` unless a loud override.
- **MainNet gate is still only `ARCRON_ALLOW_MAINNET=1`**, checked **before** `.env.mainnet` loads (`network.py:58–67`), so putting the flag in the env file does not enable it — good — but a shell that always exports the flag makes `--network mainnet` live. Require the flag **and** a 3-of-5 whose `address()` is `NHQU…`. If a multisig is configured and the sender would be `DEPLOYER`, refuse (deploy.py already refuses the other way).
- **App-account 0.1 ALGO** is not create-time-irreversible; anyone can fund it. Forgetting it looks like a broken contract. The hand-rolled path does not do it.
- **Program-size ceiling** is irreversible: extra_pages=1 means 4,096 bytes forever. Headroom is ~1,988 bytes. A later pre-freeze feature that needs page 3 is a new app, even with `update`.

**First thing I would do if this were mine:** do not run `deploy-mainnet` or `multisig_e2e` on MainNet at all. Add `govern create` that cannot emit a txn unless sender, extra_pages, schema, and combined digest are the checklist above, then have all five people type `NHQU…` before the first signature.
