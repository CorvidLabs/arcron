#!/usr/bin/env bash
# Package the keeper for a Linux host, as a tarball ready to scp.
#
# The same shape as the site's deploy/vps/package.sh: build locally, ship an
# archive, keep credentials off the wire. Nothing here needs the contracts
# compiled on the far end, because smart_contracts/artifacts/ is committed and
# the bot only ever reads boxes and calls a generated client.
#
#   ./deploy/vps/package.sh
#   scp /tmp/arcron-keeper.tar.gz <user>@<host>:/tmp/
#   ssh <user>@<host> 'sudo mkdir -p /tmp/arcron-install &&
#       sudo tar -xzf /tmp/arcron-keeper.tar.gz -C /tmp/arcron-install &&
#       sudo bash /tmp/arcron-install/deploy/vps/install.sh'
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ARCHIVE="${1:-/tmp/arcron-keeper.tar.gz}"

cd "$REPO"

# What the archive is built from, written into it as BUILD and printed by
# install.sh, because G1 asks for the keeper and notifier to run "from main"
# and a tarball of a working tree otherwise carries no evidence of which tree.
# A dirty tree is refused: its archive would name a commit it does not match.
# ARCRON_PACKAGE_DIRTY=1 overrides that for a throwaway test, and says so in
# BUILD. A commit that is not on origin/main is packaged with a warning, since
# checking that needs a fetch this script does not make for you.
COMMIT="$(git rev-parse HEAD)"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
DIRTY=0
if [[ -n "$(git status --porcelain)" ]]; then
    DIRTY=1
    if [[ "${ARCRON_PACKAGE_DIRTY:-0}" != 1 ]]; then
        echo "Refusing: the working tree has uncommitted changes, so BUILD could not" >&2
        echo "name a commit this archive matches. Commit or stash first." >&2
        exit 1
    fi
fi
ON_MAIN=unknown
if git rev-parse --verify --quiet origin/main >/dev/null; then
    if git merge-base --is-ancestor "$COMMIT" origin/main; then
        ON_MAIN=yes
    else
        ON_MAIN=no
        echo "Warning: ${COMMIT:0:7} is not on origin/main (as last fetched). G1 runs from main." >&2
    fi
fi
STAMP_DIR="$(mktemp -d)"
trap 'rm -rf "$STAMP_DIR"' EXIT
printf 'commit=%s\nbranch=%s\ndirty=%s\non_origin_main=%s\npackaged_utc=%s\n' \
    "$COMMIT" "$BRANCH" "$DIRTY" "$ON_MAIN" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    > "${STAMP_DIR}/BUILD"

# Only what the bot imports at runtime, plus every file install.sh installs.
# The notifier unit and both env examples were missing from this list while
# install.sh `install`ed them, so a packaged install died at that line under
# `set -e`; the path had never been run end to end. No web/, no tests/, no
# .env.*: the env file is written on the host, so a mnemonic never rides in
# the archive.
# --no-xattrs, and COPYFILE_DISABLE for macOS's tar: a macOS workstation
# otherwise writes its extended attributes (com.apple.provenance) into every
# entry, and GNU tar on the server prints a warning per file while unpacking.
COPYFILE_DISABLE=1 tar --no-xattrs -czf "$ARCHIVE" \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    scripts \
    smart_contracts/__init__.py \
    smart_contracts/artifacts \
    pyproject.toml \
    poetry.lock \
    deploy/keeper-bot.service \
    deploy/keeper.env.example \
    deploy/notifier.service \
    deploy/notifier.env.example \
    deploy/vps/install.sh \
    deploy/vps/algod.compose.yaml \
    -C "$STAMP_DIR" BUILD

printf 'Packaged %s (%s) from %s\n' "$ARCHIVE" "$(du -h "$ARCHIVE" | cut -f1)" "${COMMIT:0:7}"
printf '\nNext:\n'
printf '  scp %s <user>@<host>:/tmp/\n' "$ARCHIVE"
printf '  ssh <user>@<host> "sudo mkdir -p /tmp/arcron-install \\
'
printf '      && sudo tar -xzf /tmp/arcron-keeper.tar.gz -C /tmp/arcron-install \\
'
printf '      && sudo bash /tmp/arcron-install/deploy/vps/install.sh"\n'
