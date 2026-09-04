# The Arcron keeper bot, as a long-lived process.
#
# Build from the repository root:
#   docker build -f deploy/Dockerfile -t arcron-keeper .
#
# It needs an account to sign with and a network to talk to; see
# deploy/compose.yaml and docs/arcron.md ("Operating a bot").
FROM python:3.13-slim

# Unbuffered so logs reach the collector as they happen, not at exit.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    POETRY_VIRTUALENVS_CREATE=false \
    ARCRON_LOG_FORMAT=json

RUN pip install --no-cache-dir "poetry>=2.0,<3.0"

WORKDIR /app

# Dependencies first, so a code change does not re-resolve the world.
COPY pyproject.toml poetry.lock ./
RUN poetry install --only main --no-root --no-interaction

# Only what the bot actually needs: itself and the generated client.
COPY scripts ./scripts
COPY smart_contracts ./smart_contracts

# No reason for a keeper to run as root.
RUN useradd --create-home --uid 10001 keeper && chown -R keeper /app

# The state directory has to exist *in the image*, owned by the user that will
# write it. Docker initialises a fresh named volume from whatever is at the
# mount point, so when this directory was absent the volume was created owned
# by root and the notifier could not write to its own state: it announced
# everything correctly and then logged "Permission denied" every cycle,
# forever. Setting `user:` in compose does not fix it, because that changes who
# the process is and not who owns the volume.
RUN mkdir -p /home/keeper/.local/state/arcron \
    && chown -R keeper:keeper /home/keeper/.local

USER keeper

ENTRYPOINT ["python", "-m", "scripts.keeper_bot"]
CMD []
