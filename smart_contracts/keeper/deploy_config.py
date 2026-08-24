import logging

import algokit_utils

logger = logging.getLogger(__name__)


# define deployment behaviour based on supplied app spec
def deploy() -> "KeeperClient":
    from smart_contracts.artifacts.keeper.keeper_client import (
        KeeperClient,
        KeeperFactory,
    )

    algorand = algokit_utils.AlgorandClient.from_environment()
    # Public TestNet endpoints are slow; never let transactions be built from
    # stale cached suggested params (they expire before simulate/broadcast).
    algorand.set_suggested_params_cache_timeout(0)
    deployer_ = algorand.account.from_environment("DEPLOYER")

    factory = algorand.client.get_typed_app_factory(
        KeeperFactory, default_sender=deployer_.address
    )

    app_client, result = factory.deploy(
        on_update=algokit_utils.OnUpdate.AppendApp,
        on_schema_break=algokit_utils.OnSchemaBreak.AppendApp,
    )

    logger.info(
        f"Keeper app {app_client.app_id} deployed "
        f"(operation: {result.operation_performed})"
    )

    # The app account escrows ALGO and holds box MBR, so it must meet the
    # base account MBR (0.1 ALGO). Fund it once, idempotently.
    APP_BASE_MBR = 100_000
    app_balance = algorand.client.algod.account_info(app_client.app_address)[
        "amount"
    ]
    if app_balance < APP_BASE_MBR:
        algorand.send.payment(
            algokit_utils.PaymentParams(
                amount=algokit_utils.AlgoAmount(micro_algo=APP_BASE_MBR),
                sender=deployer_.address,
                receiver=app_client.app_address,
            )
        )
        logger.info(f"Funded app account with {APP_BASE_MBR} µALGO base MBR")

    client: KeeperClient = app_client
    return client
