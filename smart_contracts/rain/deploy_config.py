import logging

import algokit_utils

logger = logging.getLogger(__name__)


# define deployment behaviour based on supplied app spec
def deploy() -> "RainClient":
    """Create (or find) the Rain app. Idempotent, like keeper's and pulse's.

    This only creates the app. `rain` has no pre-fund step the way keeper
    and pulse do: its app account MBR is collected by `configure`'s own
    `mbr_payment` argument, which also fixes the beacon, the entry gate and
    the prize asset for the app's whole lifetime. Calling `configure` is
    deliberately left to the caller, who knows those three things and this
    function does not.
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

    app_client, result = factory.deploy(
        on_update=algokit_utils.OnUpdate.AppendApp,
        on_schema_break=algokit_utils.OnSchemaBreak.AppendApp,
    )

    logger.info(
        f"Rain app {app_client.app_id} deployed "
        f"(operation: {result.operation_performed})"
    )

    client: RainClient = app_client
    return client
