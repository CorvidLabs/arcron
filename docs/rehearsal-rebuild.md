# Rebuilding the TestNet ceremony rehearsal

This branch, `rehearsal-update`, is a scratch branch for the TestNet ceremony
rehearsal (#250 F10, step 3 of "What to run next" in
`docs/design/mainnet-rollout.md`). It is never merged. Its first commit is the
rehearsal's one-byte change (`MAX_INTERVAL_ROUNDS` 1,000,000,000 to
999,999,999), which takes the combined digest from `c94c6e0c…` to `99164c3c…`
with the ARC-56 methods, structs, state and bare actions unchanged. This
document is the second commit.

## Why this document exists

The rehearsal kit (`rehearse.sh`, `chain_check.py` and two git worktrees) was
built on 2026-09-25 in a scratch directory under `/private/tmp`. macOS deletes
files there that have not been touched for three days, and at 00:00 on
2026-09-30 it deleted the two scripts, the rehearsal log and every file in
both worktrees. Only this branch survived, because it lives in the repository.
The decision on 2026-09-30 (Leif, through orc) was to rebuild the kit just
before G2 and to keep the recipe here, so the rebuild does not depend on any
one session surviving.

The kit was never run: the throwaway `CVM4NOTWQYDRAUVF3EYHLZJXWERUI33GLFCNAV4MR4YVNOT6Z3XJMDGKNE`
still held 10 TestNet ALGO and had created no app when this was written.

## What the kit is

| Piece | What it does |
|---|---|
| a create checkout | a detached worktree at the G2 candidate commit, clean, with no `.env` files |
| an update checkout | a worktree on a scratch branch: the candidate plus the one-byte change, rebuilt and committed |
| `chain_check.py` | reads an app straight from TestNet algod and checks digest, `frozen` and creator; holds no key |
| `rehearse.sh` | the ceremony, step by step, checked against the chain after every step |

`rehearse.sh`, in order: clears the environment to an allowlist (so an inherited
`ALGOD_SERVER_FALLBACK` or `ARCRON_MULTISIG_ADDRESSES` cannot redirect it);
refuses unless both checkouts are clean and at their pinned commits;
self-tests `chain_check.py` against the soaked keeper `769891898`; reads the
throwaway's mnemonic with `read -rs` and refuses any other key before
connecting; creates the keeper and Pulse from the create checkout (`deploy.py
--network testnet --with-pulse --yes`); checks creator, digest `c94c6e0c…` and
`frozen 0` on the chain; runs `govern status` and `verify_build`; sends a
code-changing `govern update` from the update checkout and checks the chain
now runs `99164c3c…`; runs `preflight` (two failures there are expected:
indexer lag and the rehearsal row); freezes and checks `frozen 1`; sends one
more update, which must be refused with the chain unchanged; and exits
non-zero if any step failed. Two reviewers (Claude and Grok) agreed at 95% or
better that the version below was safe to run as written.

## Rebuilding it

Do this within a day of running it, or outside `/tmp`. `REPO` is the arcron
checkout whose `.venv` has the project installed; `WORK` is where the kit goes.

### Step 1

Pick the commit to rehearse. It is the G2 candidate: the commit that will
be tagged `mainnet-1`. The pinned value below is `b91e7f1` (main after #262,
2026-09-25); anything later on `main` that does not change
`smart_contracts/` keeps the digests below.

### Step 2

The create checkout:

```bash
git -C "$REPO" fetch origin
git -C "$REPO" worktree add --detach "$WORK/rehearsal-create" <candidate>
```

### Step 3

The update checkout, on a new scratch branch. Use a new name: until its dead
worktree is pruned, git treats `rehearsal-update` as checked out elsewhere.

```bash
git -C "$REPO" worktree add -b rehearsal-update-g2 "$WORK/rehearsal-update" <candidate>
cd "$WORK/rehearsal-update"
sed -i '' 's/^MAX_INTERVAL_ROUNDS = 1_000_000_000$/MAX_INTERVAL_ROUNDS = 999_999_999/' smart_contracts/keeper/contract.py
env -i HOME="$HOME" PATH="$REPO/.venv/bin:$HOME/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin" python -m smart_contracts build
```

(`sed -i ''` is macOS; on Linux drop the `''`.) Check the result before
committing: the base must still build `c94c6e0c…`, the update `99164c3c…`,
and the ARC-56 surface must be unchanged.

```bash
"$REPO/.venv/bin/python" - <<'PY'
import json, subprocess, base64, hashlib
new = json.load(open("smart_contracts/artifacts/keeper/Keeper.arc56.json"))
old = json.loads(subprocess.check_output(["git", "show", "HEAD:smart_contracts/artifacts/keeper/Keeper.arc56.json"]))
def dig(s):
    a = base64.b64decode(s["byteCode"]["approval"]); c = base64.b64decode(s["byteCode"]["clear"])
    return hashlib.sha256(a + b"\x00" + c).hexdigest()
print("base  ", dig(old)); print("update", dig(new))
for k in ("methods", "structs", "state", "bareActions"):
    assert old[k] == new[k], k
print("methods/structs/state/bareActions identical")
PY
git commit -q -am "Rehearsal only: a byte that differs"
git log -1 --format=%h        # this is UPDATE_COMMIT
```

If the contract has changed since `c94c6e0c…` (alpha-4), the two digests in
`rehearse.sh` change with it: use what this check prints.

### Step 4

Write `chain_check.py` and `rehearse.sh` into `$WORK` exactly as below, and
check them: `shasum -a 256` must print `d7ce07e1fa6407723c3b1f9005645f13a9775cfc0923b2bedfb94ce73f127c50` for `chain_check.py` and
`14c1cbf5e803ee3d45d40225a893672abdf83e28bb927dcab097bc11478cac0e` for `rehearse.sh` before any edit.

### Step 5

Edit only these lines at the top of `rehearse.sh`: `SP` (to `$WORK`),
`CREATE_COMMIT` and `UPDATE_COMMIT` (seven-character short hashes from
steps 2 and 3), `VENV_BIN` (to `$REPO/.venv/bin`), and the two digests if
step 3 printed different ones. The comments on the two tree lines name the
old commits; update them too.

### Step 6

Dry run with a random key. It must pass the tree checks and the checker
self-test, then refuse the key, with nothing sent:

```bash
"$REPO/.venv/bin/python" -c 'from algosdk import account, mnemonic; k,_=account.generate_account(); print(mnemonic.from_private_key(k))' \
  | ALGOD_SERVER_FALLBACK=https://mainnet-api.algonode.cloud ARCRON_MULTISIG_ADDRESSES=X bash "$WORK/rehearse.sh"
```

### Step 7

Review. Any change beyond the lines in step 5 goes past a second reviewer
before it is run.

### Step 8

Leif runs it in his own terminal and pastes the throwaway's mnemonic when
it asks (the key is never shown, logged or written): `bash "$WORK/rehearse.sh"`.
Afterwards the log is `$WORK/rehearsal.log`; record the result in
`docs/design/mainnet-rollout.md` ("Rehearsal record") and delete the
scratch branches.

## chain_check.py

sha256 `d7ce07e1fa6407723c3b1f9005645f13a9775cfc0923b2bedfb94ce73f127c50`

```python
"""Read an app straight from TestNet algod and check it against expectations.

Holds no key and signs nothing. Exits 0 only if every expectation given matches.
Usage: chain_check.py APP_ID [--digest HEX] [--frozen 0|1] [--creator ADDR]
"""
import argparse
import base64
import hashlib
import os
import sys

from algosdk.v2client import algod

TESTNET_GENESIS = "testnet-v1.0"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("app_id", type=int)
    parser.add_argument("--digest")
    parser.add_argument("--frozen", type=int, choices=(0, 1))
    parser.add_argument("--creator")
    args = parser.parse_args()

    client = algod.AlgodClient("", os.environ["ALGOD_SERVER"])
    genesis = client.suggested_params().gen
    if genesis != TESTNET_GENESIS:
        print(f"CHECK FAIL: node reports genesis {genesis!r}, not {TESTNET_GENESIS}")
        return 1
    params = client.application_info(args.app_id)["params"]
    approval = base64.b64decode(params["approval-program"])
    clear = base64.b64decode(params["clear-state-program"])
    digest = hashlib.sha256(approval + b"\x00" + clear).hexdigest()
    frozen = next(
        (kv["value"].get("uint", 0) for kv in params.get("global-state", [])
         if base64.b64decode(kv["key"]) == b"frozen"),
        None,
    )
    print(f"CHECK app {args.app_id} on {genesis}: creator {params['creator']}, "
          f"digest {digest}, approval {len(approval)} bytes, frozen {frozen}")
    bad = []
    if args.digest and digest != args.digest:
        bad.append(f"digest {digest} != expected {args.digest}")
    if args.frozen is not None and frozen != args.frozen:
        bad.append(f"frozen {frozen} != expected {args.frozen}")
    if args.creator and params["creator"] != args.creator:
        bad.append(f"creator {params['creator']} != expected {args.creator}")
    for b in bad:
        print(f"CHECK FAIL: {b}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
```

## rehearse.sh

sha256 `14c1cbf5e803ee3d45d40225a893672abdf83e28bb927dcab097bc11478cac0e` as pinned on 2026-09-25 (edit only the lines in step 5).

```bash
#!/usr/bin/env bash
# TestNet ceremony rehearsal, docs/design/mainnet-rollout.md "What to run next", step 3
# (#250 F10): create from a clean detached checkout with a creator that has never made
# an app, then a CODE-CHANGING update, then freeze, then an update that must be refused.
#
# The rehearsal key is read with `read -rs` and exported only into this process. It is
# never echoed, logged or written to a file. The script refuses to go on unless the key
# is the throwaway CVM4..., so a wrong key (or corvid.algo's) stops it before anything
# connects. Every pass is also checked by reading the app back from the chain with
# chain_check.py, which holds no key, rather than trusting what the scripts print.
set -uo pipefail

# Start from an allowlisted environment, so nothing inherited from the shell can
# redirect this run: ALGOD_SERVER_FALLBACK would let node_retry fail over to another
# node, ARCRON_MULTISIG_ADDRESSES would turn update/freeze into writing an unsigned file.
for v in $(compgen -e); do
  case "$v" in
    HOME|PATH|TERM|USER|LOGNAME|SHELL|TMPDIR|LANG|LC_*) ;;
    *) unset "$v" 2>/dev/null || true ;;
  esac
done

SP=/private/tmp/claude-501/-Users-leif-Development--CorvidLabs--apps-arcron/88aee30e-80aa-411a-a5aa-5dc64dca68f1/scratchpad
CREATE_TREE="$SP/rehearsal-create"     # detached at b91e7f1 (main after #262), no .env files
CREATE_COMMIT=b91e7f1
UPDATE_TREE="$SP/rehearsal-update"     # branch rehearsal-update (MAX_INTERVAL_ROUNDS - 1)
UPDATE_COMMIT=c37013d
VENV_BIN=/Users/leif/Development/_CorvidLabs/_apps/arcron/.venv/bin
LOG="$SP/rehearsal.log"
EXPECT=CVM4NOTWQYDRAUVF3EYHLZJXWERUI33GLFCNAV4MR4YVNOT6Z3XJMDGKNE
SOAKED_DIGEST=c94c6e0cc561c028eeb3ccdd8c462c509ee106a28ba2e1d61469adbb62ffe124
UPDATED_DIGEST=99164c3c1f0d662346507cddbced352cfde326bc98926575848f7d2f1c9c5750

export PATH="$VENV_BIN:$HOME/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
# Public TestNet endpoints, so no .env file is needed (and none is present).
export ALGOD_SERVER=https://testnet-api.algonode.cloud
export INDEXER_SERVER=https://testnet-idx.algonode.cloud

trap 'unset DEPLOYER_MNEMONIC' EXIT
declare -a RESULTS=()
FAILS=0
note() { RESULTS+=("$1"); echo "== $1" | tee -a "$LOG"; }
fail() { FAILS=$((FAILS + 1)); note "FAIL $1"; }
run() { echo "\$ $*" | tee -a "$LOG"; "$@" 2>&1 | tee -a "$LOG"; return "${PIPESTATUS[0]}"; }
check() { run python "$SP/chain_check.py" "$@"; }
at_commit() {  # tree, commit: clean and exactly at that commit
  [ -z "$(git -C "$1" status --porcelain)" ] && [ "$(git -C "$1" rev-parse --short=7 HEAD)" = "$2" ]
}

: > "$LOG"
echo "Rehearsal log: $LOG"
at_commit "$CREATE_TREE" "$CREATE_COMMIT" || { echo "Refusing: $CREATE_TREE is not clean at $CREATE_COMMIT"; exit 1; }
at_commit "$UPDATE_TREE" "$UPDATE_COMMIT" || { echo "Refusing: $UPDATE_TREE is not clean at $UPDATE_COMMIT"; exit 1; }
# Self-test the read-back before anything is signed: if the checker cannot run, a
# ceremony that worked would be recorded as failed, and this creator may make only one
# keeper without --another. 769891898 is the soaked TestNet keeper.
check 769891898 --digest "$SOAKED_DIGEST" --frozen 0 \
  || { echo "Refusing: chain_check.py self-test against 769891898 failed. Nothing was sent."; exit 1; }

read -rsp "Paste the rehearsal account's 25-word mnemonic (hidden): " DEPLOYER_MNEMONIC; echo
export DEPLOYER_MNEMONIC

ADDR=$(python - <<'EOF'
import os
from algosdk import account, mnemonic
try:
    print(account.address_from_private_key(mnemonic.to_private_key(os.environ["DEPLOYER_MNEMONIC"])))
except Exception:
    print("INVALID")
EOF
)
if [ "$ADDR" != "$EXPECT" ]; then
  echo "Refusing: that key is ${ADDR:0:8}..., not the rehearsal throwaway ${EXPECT:0:8}.... Nothing was sent."
  exit 1
fi
note "key is the throwaway ${EXPECT:0:8}... (address checked, mnemonic not logged); env allowlisted"

# 1. Create, from the clean detached tree.
cd "$CREATE_TREE" || exit 1
note "1. create at $CREATE_COMMIT (clean tree, no .env files)"
run python -m scripts.deploy --network testnet --with-pulse --yes
CREATE_RC=$?
APP=$(grep -oE 'Keeper app [0-9]+ on testnet' "$LOG" | tail -1 | grep -oE '[0-9]+' | head -1)
PULSE=$(grep -oE 'Created pulse app [0-9]+' "$LOG" | tail -1 | grep -oE '[0-9]+')
if [ "$CREATE_RC" -ne 0 ] || [ -z "$APP" ]; then
  fail "1. create (exit $CREATE_RC, app '${APP:-none}'). Stopping; read $LOG"
  exit 1
fi
note "created keeper $APP, pulse ${PULSE:-none}"
check "$APP" --digest "$SOAKED_DIGEST" --frozen 0 --creator "$EXPECT" \
  && note "1b. chain: creator CVM4..., digest c94c6e0c..., frozen 0" || fail "1b. chain read-back after create"

# 2-3. Status and byte-for-byte verification of what was created.
run python -m scripts.govern status --network testnet --app-id "$APP" && note "2. govern status ok" || fail "2. govern status"
run python -m scripts.verify_build --network testnet --app-id "$APP" && note "3. verify_build keeper: byte for byte" || fail "3. verify_build keeper"
if [ -n "${PULSE:-}" ]; then
  run python -m scripts.verify_build --network testnet --contract pulse --app-id "$PULSE" && note "3b. verify_build pulse ok" || fail "3b. verify_build pulse"
fi

# 4-5. The code-changing update, from the scratch branch.
cd "$UPDATE_TREE" || exit 1
note "4. update from $UPDATE_COMMIT on rehearsal-update"
run python -m scripts.govern update --network testnet --app-id "$APP"
UPD_RC=$?
if [ "$UPD_RC" -ne 0 ]; then
  fail "4. govern update exit $UPD_RC"
elif check "$APP" --digest "$UPDATED_DIGEST" --frozen 0; then
  note "4. code-changing update landed: chain now runs 99164c3c..."
else
  fail "4. govern update exited 0 but the chain does not run 99164c3c..."
fi
run python -m scripts.verify_build --network testnet --app-id "$APP" && note "5. verify_build after update: byte for byte" || fail "5. verify_build after update"

# 6. Preflight's clock row should now name the update round. Two failures are expected
#    and are not failures: a lagging indexer (fails closed) and the rehearsal row pricing
#    a throwaway that just spent its balance. So its exit code is recorded, not counted.
echo "waiting 30s for the indexer to see the update" | tee -a "$LOG"; sleep 30
run python -m scripts.preflight --network testnet --app-id "$APP"
note "6. preflight ran (exit $?): read its clock row"

# 7-9. Freeze, then prove it from the chain.
run python -m scripts.govern freeze --network testnet --app-id "$APP" --yes
FREEZE_RC=$?
if [ "$FREEZE_RC" -eq 0 ] && check "$APP" --digest "$UPDATED_DIGEST" --frozen 1; then
  note "7. frozen: chain reads frozen 1"
else
  fail "7. freeze (exit $FREEZE_RC) or chain does not read frozen 1"
fi
run python -m scripts.govern status --network testnet --app-id "$APP" && note "8. status after freeze" || fail "8. status after freeze"
run python -m scripts.govern update --network testnet --app-id "$APP"
REFUSE_RC=$?
if [ "$REFUSE_RC" -ne 0 ] && grep -q "Refusing" <(tail -20 "$LOG") \
   && check "$APP" --digest "$UPDATED_DIGEST" --frozen 1; then
  note "9. update after freeze refused, and the chain is unchanged"
else
  fail "9. update after freeze was NOT refused cleanly (exit $REFUSE_RC)"
fi

unset DEPLOYER_MNEMONIC
echo
echo "================ Rehearsal summary ================"
printf '%s\n' "${RESULTS[@]}"
echo "Keeper $APP, Pulse ${PULSE:-none}. Failures: $FAILS. Full log: $LOG"
echo "Close this terminal when you are done; the key was only ever in this process."
[ "$FAILS" -eq 0 ]
```
