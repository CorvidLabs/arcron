import logging

import algokit_utils

logger = logging.getLogger(__name__)


# define deployment behaviour based on supplied app spec
def _refuse_unguarded_mainnet(algorand: "algokit_utils.AlgorandClient") -> None:
    """Refuse to create a keeper on MainNet from a single key.

    Asks the node which chain it speaks for rather than trusting an argument,
    because this function takes none. A keeper created here would have the
    single-key DEPLOYER as its creator, permanently, which is the admin-key
    problem the multisig exists to avoid.
    """
    from scripts import network as net

    genesis = algorand.client.algod.suggested_params().gen
    if genesis not in net.genesis_ids(net.MAINNET):
        return
    from scripts import multisig as ms

    raise RuntimeError(
        f"Refusing to create a keeper on MainNet ({genesis}) from this path. It signs "
        "in process with a single key, and an app's creator cannot be changed "
        "afterwards. Use `poetry run python -m scripts.govern create`, which requires "
        f"the {ms.describe() if ms.configured() else '3-of-5'} multisig."
    )


def deploy() -> "KeeperClient":
    from smart_contracts.artifacts.keeper.keeper_client import (
        KeeperClient,
        KeeperFactory,
    )

    algorand = algokit_utils.AlgorandClient.from_environment()
    # The MainNet gate lives in `scripts.network.load_network`, and this
    # factory is reachable without it: anyone pointing ALGOD_SERVER at MainNet
    # and calling this creates a keeper from the single-key DEPLOYER, whose
    # creator can never be changed. `fledge run deploy-mainnet` is not that
    # path, but nothing stopped somebody taking this one.
    _refuse_unguarded_mainnet(algorand)
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
