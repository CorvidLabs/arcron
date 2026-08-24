import logging
import os

import algokit_utils

logger = logging.getLogger(__name__)

# MicroAlgos covering the app account MBR (0.1 ALGO) + one ASA opt-in (0.1 ALGO).
BOOTSTRAP_MBR = 200_000


# define deployment behaviour based on supplied app spec
def deploy() -> "tuple[CorvidVaultClient, int]":
    from smart_contracts.artifacts.corvid_vault.corvid_vault_client import (
        BootstrapArgs,
        CorvidVaultClient,
        CorvidVaultFactory,
    )

    algorand = algokit_utils.AlgorandClient.from_environment()
    deployer_ = algorand.account.from_environment("DEPLOYER")

    factory = algorand.client.get_typed_app_factory(
        CorvidVaultFactory, default_sender=deployer_.address
    )

    app_client, result = factory.deploy(
        on_update=algokit_utils.OnUpdate.AppendApp,
        on_schema_break=algokit_utils.OnSchemaBreak.AppendApp,
    )

    if result.operation_performed in [
        algokit_utils.OperationPerformed.Create,
        algokit_utils.OperationPerformed.Replace,
    ]:
        # Resolve the CORVID ASA: use CORVID_ASSET_ID when set (e.g. TestNet),
        # otherwise create a mock CORVID ASA on LocalNet.
        corvid_asset_id = int(os.environ.get("CORVID_ASSET_ID", "0"))
        if corvid_asset_id == 0:
            if not algorand.client.is_localnet():
                raise ValueError(
                    "CORVID_ASSET_ID must be set when not deploying to LocalNet"
                )
            create_result = algorand.send.asset_create(
                algokit_utils.AssetCreateParams(
                    sender=deployer_.address,
                    total=10_000_000_000_000,  # 10M CORVID with 6 decimals
                    decimals=6,
                    asset_name="CORVID (LocalNet mock)",
                    unit_name="CORVID",
                    manager=deployer_.address,
                )
            )
            corvid_asset_id = create_result.asset_id
            logger.info(f"Created mock CORVID ASA with id {corvid_asset_id}")

        # Fund the app account MBR and opt it into the CORVID ASA.
        mbr_payment = algorand.create_transaction.payment(
            algokit_utils.PaymentParams(
                sender=deployer_.address,
                receiver=app_client.app_address,
                amount=algokit_utils.AlgoAmount(micro_algo=BOOTSTRAP_MBR),
            )
        )
        app_client.send.bootstrap(
            args=BootstrapArgs(mbr_payment=mbr_payment, asset=corvid_asset_id),
            # Cover the inner ASA opt-in transaction's fee via fee pooling.
            params=algokit_utils.CommonAppCallParams(
                extra_fee=algokit_utils.AlgoAmount(micro_algo=1_000)
            ),
        )
        logger.info(
            f"Bootstrapped {app_client.app_name} ({app_client.app_id}) "
            f"with CORVID ASA {corvid_asset_id}"
        )

    # Return the client and the ASA the vault is actually bound to (from global
    # state, so repeat deploys against an existing app stay consistent).
    bound_asset_id = app_client.state.global_state.asset_id
    client: CorvidVaultClient = app_client
    return client, bound_asset_id
