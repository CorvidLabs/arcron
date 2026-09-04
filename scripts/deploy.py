"""Deploy the keeper (and optionally the pulse demo target) to a network.

    poetry run python -m scripts.deploy --network localnet
    poetry run python -m scripts.deploy --network testnet
    ARCRON_ALLOW_MAINNET=1 poetry run python -m scripts.deploy --network mainnet

What it does beyond calling the AlgoKit deploy config:

* funds the app account's base minimum balance, without which the app cannot
  hold a box or escrow a µALGO, and which is the single most common way a
  fresh deployment looks broken;
* verifies the deployed bytecode against a clean build of this tree, so the
  thing you just created is provably this source;
* prints the combined sha256 and the app id in the shape `docs/releases.md`
  wants recorded.

A new deployment starts **unfrozen**: its creator can still replace the
programs. That is deliberate while nobody depends on it, and it is given up
with `scripts/govern.py freeze` before anybody is asked to. `status` says which
state a deployment is in, and anyone can read it without trusting us.
"""

import argparse
import logging

import algokit_utils

from scripts import multisig as ms, network as net
from scripts.govern import _deployed, _frozen
from scripts.verify_build import _digest, _programs, _spec, rebuild

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# What an app account needs before it can hold anything at all.
BASE_MBR = 100_000


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    net.add_network_argument(parser)
    parser.add_argument("--with-pulse", action="store_true", help="also deploy the demo target")
    parser.add_argument("--no-rebuild", action="store_true", help="trust the built artifacts")
    args = parser.parse_args(argv)

    if not args.no_rebuild:
        rebuild()

    algorand = net.connect(args.network)
    algod = algorand.client.algod

    if args.network == net.MAINNET:
        # The connect gate is only ARCRON_ALLOW_MAINNET: a keeper hot key has
        # to be able to talk to MainNet. Create is the one call that must be
        # `corvid.algo`, because the creator cannot be changed afterwards.
        deployer = algorand.account.from_environment("DEPLOYER")
        net.require_mainnet_creator(deployer.address)

    if ms.configured():
        # A multisig cannot sign in process, so creating from one is a separate
        # flow: `govern create` is it. Do not point anyone at
        # `scripts/multisig_e2e.py`, which this comment used to: that script
        # generates three throwaway keys and drops them when it exits, so on a
        # real network it makes an app whose creator nobody holds. Refusing is
        # better than quietly deploying from the single-key DEPLOYER and
        # leaving a contract whose creator is not the multisig anyone was told
        # to expect.
        logger.error(
            f"A multisig is configured ({ms.describe()}), and this command signs "
            "in process. Use `poetry run python -m scripts.govern create` instead; "
            "see docs/deploying.md."
        )
        return 1

    from smart_contracts.keeper.deploy_config import deploy as deploy_keeper

    keeper = deploy_keeper()
    app_id = keeper.app_id

    account = algod.account_info(keeper.app_address)
    if account["amount"] < BASE_MBR:
        logger.info(f"Funding the app account with {BASE_MBR} µALGO of base minimum balance")
        deployer = algorand.account.from_environment("DEPLOYER")
        algorand.send.payment(
            algokit_utils.PaymentParams(
                sender=deployer.address,
                receiver=keeper.app_address,
                amount=algokit_utils.AlgoAmount(micro_algo=BASE_MBR - account["amount"]),
            )
        )

    approval, clear = _programs(_spec("keeper"))
    live_approval, live_clear = _deployed(algod, app_id)
    if _digest(approval, clear) != _digest(live_approval, live_clear):
        logger.error("The deployed app does not match this tree. Do not use it.")
        return 1

    pulse_id = None
    if args.with_pulse:
        from smart_contracts.pulse.deploy_config import deploy as deploy_pulse

        pulse_id = deploy_pulse().app_id

    frozen = _frozen(algod, app_id)
    logger.info("")
    logger.info(f"Keeper app {app_id} on {args.network}")
    logger.info(f"  address   {keeper.app_address}")
    logger.info(f"  approval  {len(approval)} bytes")
    logger.info(f"  sha256    {_digest(approval, clear)}")
    logger.info(f"  frozen    {frozen} ({'immutable' if frozen == 1 else 'the creator can still update'})")
    if pulse_id:
        logger.info(f"  pulse     {pulse_id}")
    logger.info("")
    logger.info("Verified: the deployed app is this source, byte for byte.")
    logger.info("Record the app id and sha256 in docs/releases.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
