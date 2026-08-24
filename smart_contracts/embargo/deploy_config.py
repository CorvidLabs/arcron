import logging

import algokit_utils

logger = logging.getLogger(__name__)


def deploy() -> "EmbargoClient":
    from smart_contracts.artifacts.embargo.embargo_client import (
        EmbargoClient,
        EmbargoFactory,
    )

    algorand = algokit_utils.AlgorandClient.from_environment()
    # Public TestNet endpoints are slow; never build transactions from stale
    # cached suggested params.
    algorand.set_suggested_params_cache_timeout(0)
    deployer_ = algorand.account.from_environment("DEPLOYER")

    factory = algorand.client.get_typed_app_factory(
        EmbargoFactory, default_sender=deployer_.address
    )
    app_client, result = factory.deploy(
        on_update=algokit_utils.OnUpdate.AppendApp,
        on_schema_break=algokit_utils.OnSchemaBreak.AppendApp,
    )
    logger.info(
        f"Embargo app {app_client.app_id} deployed "
        f"(operation: {result.operation_performed})"
    )

    # The app account holds the content box, so it must meet the base account
    # MBR before it can hold anything at all.
    APP_BASE_MBR = 100_000
    balance = algorand.client.algod.account_info(app_client.app_address)["amount"]
    if balance < APP_BASE_MBR:
        algorand.send.payment(
            algokit_utils.PaymentParams(
                amount=algokit_utils.AlgoAmount(micro_algo=APP_BASE_MBR),
                sender=deployer_.address,
                receiver=app_client.app_address,
            )
        )
        logger.info(f"Funded app account with {APP_BASE_MBR} µALGO base MBR")

    client: EmbargoClient = app_client
    return client
