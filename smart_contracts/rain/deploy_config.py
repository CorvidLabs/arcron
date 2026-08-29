import logging

import algokit_utils

logger = logging.getLogger(__name__)


# define deployment behaviour based on supplied app spec
def deploy(app_id: int | None = None) -> "RainClient":
    """Create a Rain app, or wrap an existing one if `app_id` is given.

    Deliberately not `factory.deploy()`'s idempotent find-or-create, which
    keeper's and pulse's deploy_config use: that path looks up existing apps
    by creator and name through the indexer, and the public TestNet indexer
    is a shared, quota-limited resource that this project has already hit
    empty (see docs/releases.md's rain deployment note). A plain `create`
    needs no indexer at all, and re-running against a known `app_id` needs
    none either.

    This only creates the app. The hub's own floor is collected by
    `bootstrap`, and each rain's box MBR by `create_rain`. Neither is called
    here: the caller knows the keeper and the first rain, and this function
    does not.
    """
    from smart_contracts.artifacts.rain.rain_client import (
        RainClient,
        RainFactory,
    )

    algorand = algokit_utils.AlgorandClient.from_environment()
    # Public TestNet endpoints are slow; never let transactions be built from
    # stale cached suggested params (they expire before simulate/broadcast).
    algorand.set_suggested_params_cache_timeout(0)
    deployer_ = algorand.account.from_environment("DEPLOYER")

    factory = algorand.client.get_typed_app_factory(
        RainFactory, default_sender=deployer_.address
    )

    if app_id is not None:
        client: RainClient = factory.get_app_client_by_id(app_id=app_id)
        logger.info(f"Rain app {client.app_id} (existing)")
        return client

    app_client, _result = factory.send.create.bare()
    logger.info(f"Rain app {app_client.app_id} created")

    client = app_client
    return client
