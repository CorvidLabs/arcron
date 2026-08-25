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

# Only what the bot imports at runtime. No web/, no tests/, no .env.* — the
# env file is written on the host, so a mnemonic never rides in the archive.
tar -czf "$ARCHIVE" \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    scripts \
    smart_contracts/__init__.py \
    smart_contracts/artifacts \
    pyproject.toml \
    poetry.lock \
    deploy/keeper-bot.service \
    deploy/vps/install.sh

printf 'Packaged %s (%s)\n' "$ARCHIVE" "$(du -h "$ARCHIVE" | cut -f1)"
printf '\nNext:\n'
printf '  scp %s <user>@<host>:/tmp/\n' "$ARCHIVE"
printf '  ssh <user>@<host> "sudo mkdir -p /tmp/arcron-install \\
'
printf '      && sudo tar -xzf /tmp/arcron-keeper.tar.gz -C /tmp/arcron-install \\
'
printf '      && sudo bash /tmp/arcron-install/deploy/vps/install.sh"\n'
