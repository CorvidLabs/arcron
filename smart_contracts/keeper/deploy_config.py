import logging

import algokit_utils

logger = logging.getLogger(__name__)


# define deployment behaviour based on supplied app spec
def _refuse_unguarded_mainnet(algorand: "algokit_utils.AlgorandClient") -> None:
    """Refuse MainNet from this path, whoever the deployer is.

    Asks the node which chain it speaks for rather than trusting an argument,
    because this function takes none. This used to admit `corvid.algo`; it no
    longer admits anyone, because `factory.deploy` with `AppendApp` finds an
    existing app through the indexer and creates a second one when the indexer
    is behind, and checks none of the fields a create fixes forever. MainNet is
    created by `scripts/deploy.py` (`fledge run deploy-mainnet`), which does.
    LocalNet and TestNet keep this path for the end-to-end and the rehearsals.
    """
    from scripts import network as net

    genesis = algorand.client.algod.suggested_params().gen
    net.refuse_algokit_create_on_mainnet(genesis, "keeper")


def deploy() -> "KeeperClient":
    from smart_contracts.artifacts.keeper.keeper_client import (
        KeeperClient,
        KeeperFactory,
    )

    algorand = algokit_utils.AlgorandClient.from_environment()
    # Reachable without `scripts.network.load_network`: anyone pointing
    # ALGOD_SERVER at MainNet and calling this factory would create a keeper
    # from DEPLOYER, unchecked. `_refuse_unguarded_mainnet` is the check, and
    # the answer on MainNet is no.
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
