#!/usr/bin/env bash
# Install the Arcron keeper as a systemd service. Run on the host, as root:
#
#   scp /tmp/arcron-keeper.tar.gz <user>@<host>:/tmp/
#   ssh <user>@<host>
#   sudo mkdir -p /tmp/arcron-install
#   sudo tar -xzf /tmp/arcron-keeper.tar.gz -C /tmp/arcron-install
#   sudo bash /tmp/arcron-install/deploy/vps/install.sh
#
# Idempotent: safe to re-run to upgrade. It stops the service, replaces the
# code, reinstalls dependencies and starts it again. It never touches
# /etc/arcron/keeper.env, so an upgrade cannot clobber the mnemonic.
set -euo pipefail

APP_DIR=/opt/arcron
ENV_DIR=/etc/arcron
ENV_FILE="${ENV_DIR}/keeper.env"
SERVICE=keeper-bot
RUN_USER=keeper
SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

if [[ $EUID -ne 0 ]]; then
    echo "Run as root: sudo bash $0" >&2
    exit 1
fi

echo "==> Checking prerequisites"
command -v python3 >/dev/null || { echo "python3 not found" >&2; exit 1; }
PY_VERSION="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
case "$PY_VERSION" in
    3.12|3.13) : ;;
    3.14*) echo "Python $PY_VERSION: coincurve publishes no 3.14 wheels. Install 3.13." >&2; exit 1 ;;
    *) echo "Python $PY_VERSION is unsupported; need 3.12 or 3.13." >&2; exit 1 ;;
esac
echo "    python3 $PY_VERSION"

if ! command -v poetry >/dev/null; then
    echo "==> Installing Poetry"
    python3 -m pip install --quiet --break-system-packages "poetry>=2.0,<3.0" \
        || python3 -m pip install --quiet "poetry>=2.0,<3.0"
fi
echo "    poetry $(poetry --version 2>/dev/null | awk '{print $3}' | tr -d '()')"

echo "==> Ensuring the ${RUN_USER} user"
id -u "$RUN_USER" >/dev/null 2>&1 || useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$RUN_USER"

# Stop before replacing code, so a half-copied tree is never what is running.
if systemctl is-active --quiet "$SERVICE"; then
    echo "==> Stopping ${SERVICE} for the upgrade"
    systemctl stop "$SERVICE"
fi

echo "==> Installing to ${APP_DIR}"
mkdir -p "$APP_DIR"
for item in scripts smart_contracts pyproject.toml poetry.lock; do
    rm -rf "${APP_DIR:?}/${item}"
    cp -R "${SOURCE}/${item}" "${APP_DIR}/"
done
chown -R "$RUN_USER:$RUN_USER" "$APP_DIR"

echo "==> Installing dependencies (this takes a minute)"
cd "$APP_DIR"
# We are already root, so this is dropping privileges rather than gaining
# them: runuser is the tool for that, and it ships with systemd, whereas sudo
# is merely usually present.
if command -v runuser >/dev/null; then
    as_keeper() { runuser -u "$RUN_USER" -- "$@"; }
elif command -v sudo >/dev/null; then
    as_keeper() { sudo -u "$RUN_USER" -- "$@"; }
else
    echo "Neither runuser nor sudo found; cannot drop privileges." >&2
    exit 1
fi
as_keeper env POETRY_VIRTUALENVS_IN_PROJECT=true poetry install --only main --no-root --no-interaction

echo "==> Configuration"
mkdir -p "$ENV_DIR"
if [[ -f "$ENV_FILE" ]]; then
    echo "    ${ENV_FILE} exists, leaving it alone"
else
    cat > "$ENV_FILE" <<'ENV'
# The account that signs executions and receives the fees. A throwaway is fine
# on TestNet; it needs enough ALGO to pay 3,000 microAlgos per execution until
# the fees it collects cover that.
KEEPER_MNEMONIC=

# Which app to service. Required: the bot has no default, because an older
# deployment's boxes are a different shape and it would rather refuse than
# misread them.
KEEPER_APP_ID=769891898

ALGOD_SERVER=https://testnet-api.algonode.cloud
ALGOD_PORT=
ALGOD_TOKEN=
ENV
    echo "    wrote ${ENV_FILE} — add KEEPER_MNEMONIC before starting"
fi
chown root:"$RUN_USER" "$ENV_FILE"
chmod 640 "$ENV_FILE"

echo "==> Installing the units"
install -m 644 "${SOURCE}/deploy/keeper-bot.service" "/etc/systemd/system/${SERVICE}.service"

# The notifier alongside the keeper, because beta's thirty-day gate asks for
# both and because it is the only thing that can tell a stranger's upkeep from
# ours. Installed but not enabled without its own env file: it needs a webhook
# and the list of creators that count as us, and starting it without those
# gives a service that runs and reports nothing.
NOTIFIER_ENV="${ENV_DIR}/notifier.env"
install -m 644 "${SOURCE}/deploy/notifier.service" /etc/systemd/system/arcron-notifier.service
if [ ! -f "$NOTIFIER_ENV" ]; then
    install -m 640 "${SOURCE}/deploy/notifier.env.example" "$NOTIFIER_ENV"
    chown root:"$RUN_USER" "$NOTIFIER_ENV"
fi

systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null

if grep -q '^KEEPER_MNEMONIC=$' "$ENV_FILE"; then
    cat <<'DONE'

Installed, not started: KEEPER_MNEMONIC is still empty.

  sudo -e /etc/arcron/keeper.env     # paste the 25-word mnemonic
  sudo systemctl start keeper-bot
  sudo systemctl status keeper-bot
  sudo journalctl -u keeper-bot -f

Then the watcher, which is the other half of the thirty-day gate:

  sudo -e /etc/arcron/notifier.env   # webhook, and ARCRON_OURS
  sudo systemctl enable --now arcron-notifier
  sudo journalctl -u arcron-notifier -f

DONE
else
    systemctl start "$SERVICE"
    sleep 2
    systemctl --no-pager --lines=15 status "$SERVICE" || true
    echo
    echo "Running. Follow it with: sudo journalctl -u keeper-bot -f"
fi
