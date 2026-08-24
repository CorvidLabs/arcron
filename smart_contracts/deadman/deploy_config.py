import logging

import algokit_utils

logger = logging.getLogger(__name__)


def deploy() -> "DeadManClient":
    from smart_contracts.artifacts.deadman.dead_man_client import (
        DeadManClient,
        DeadManFactory,
    )

    algorand = algokit_utils.AlgorandClient.from_environment()
    algorand.set_suggested_params_cache_timeout(0)
    deployer_ = algorand.account.from_environment("DEPLOYER")
    factory = algorand.client.get_typed_app_factory(
        DeadManFactory, default_sender=deployer_.address
    )
    app_client, result = factory.deploy(
        on_update=algokit_utils.OnUpdate.AppendApp,
        on_schema_break=algokit_utils.OnSchemaBreak.AppendApp,
    )
    logger.info(
        f"DeadMan app {app_client.app_id} deployed (operation: {result.operation_performed})"
    )
    client: DeadManClient = app_client
    return client
