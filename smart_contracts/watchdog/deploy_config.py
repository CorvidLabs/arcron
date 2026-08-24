import logging

import algokit_utils

logger = logging.getLogger(__name__)


def deploy() -> "WatchdogClient":
    from smart_contracts.artifacts.watchdog.watchdog_client import (
        WatchdogClient,
        WatchdogFactory,
    )

    algorand = algokit_utils.AlgorandClient.from_environment()
    algorand.set_suggested_params_cache_timeout(0)
    deployer_ = algorand.account.from_environment("DEPLOYER")
    factory = algorand.client.get_typed_app_factory(
        WatchdogFactory, default_sender=deployer_.address
    )
    app_client, result = factory.deploy(
        on_update=algokit_utils.OnUpdate.AppendApp,
        on_schema_break=algokit_utils.OnSchemaBreak.AppendApp,
    )
    logger.info(
        f"Watchdog app {app_client.app_id} deployed (operation: {result.operation_performed})"
    )
    client: WatchdogClient = app_client
    return client
