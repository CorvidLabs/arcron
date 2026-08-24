import logging

import algokit_utils

logger = logging.getLogger(__name__)


def deploy() -> "BeaconStubClient":
    from smart_contracts.artifacts.beacon_stub.beacon_stub_client import (
        BeaconStubClient,
        BeaconStubFactory,
    )

    algorand = algokit_utils.AlgorandClient.from_environment()
    algorand.set_suggested_params_cache_timeout(0)
    deployer_ = algorand.account.from_environment("DEPLOYER")
    factory = algorand.client.get_typed_app_factory(
        BeaconStubFactory, default_sender=deployer_.address
    )
    app_client, result = factory.deploy(
        on_update=algokit_utils.OnUpdate.AppendApp,
        on_schema_break=algokit_utils.OnSchemaBreak.AppendApp,
    )
    logger.info(
        f"BeaconStub app {app_client.app_id} deployed "
        f"(operation: {result.operation_performed})"
    )
    client: BeaconStubClient = app_client
    return client
