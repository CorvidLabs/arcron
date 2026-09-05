import logging

import algokit_utils

logger = logging.getLogger(__name__)


# define deployment behaviour based on supplied app spec
def deploy() -> "PulseClient":
    from smart_contracts.artifacts.pulse.pulse_client import (
        PulseClient,
        PulseFactory,
    )

    from scripts import network as net

    algorand = algokit_utils.AlgorandClient.from_environment()
    # Same rule as the keeper's deploy_config: the algokit path creates through
    # the indexer and checks nothing permanent, so it stays off MainNet.
    # `scripts/deploy.py --with-pulse` creates Pulse there, directly.
    net.refuse_algokit_create_on_mainnet(algorand.client.algod.suggested_params().gen, "pulse")
    # Public TestNet endpoints are slow; never let transactions be built from
    # stale cached suggested params (they expire before simulate/broadcast).
    algorand.set_suggested_params_cache_timeout(0)
    deployer_ = algorand.account.from_environment("DEPLOYER")

    factory = algorand.client.get_typed_app_factory(
        PulseFactory, default_sender=deployer_.address
    )

    app_client, result = factory.deploy(
        on_update=algokit_utils.OnUpdate.AppendApp,
        on_schema_break=algokit_utils.OnSchemaBreak.AppendApp,
    )

    logger.info(
        f"Pulse app {app_client.app_id} deployed "
        f"(operation: {result.operation_performed})"
    )
    client: PulseClient = app_client
    return client
