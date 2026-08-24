# The Archon keeper bot, as a long-lived process.
#
# Build from the repository root:
#   docker build -f deploy/Dockerfile -t archon-keeper .
#
# It needs an account to sign with and a network to talk to; see
# deploy/compose.yaml and docs/archon.md ("Operating a bot").
FROM python:3.13-slim

# Unbuffered so logs reach the collector as they happen, not at exit.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    POETRY_VIRTUALENVS_CREATE=false \
    ARCHON_LOG_FORMAT=json

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
USER keeper

ENTRYPOINT ["python", "-m", "scripts.keeper_bot"]
CMD ["--network", "testnet"]
